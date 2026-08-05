from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from src.ai_provider import call_llm_json, get_provider_config, provider_status, normalize_agent_payload
from src.ai_summary import (
    MAX_FAILURES,
    _blob_dir,
    _cache_blob_name,
    _redact,
    _safe_cache_key,
    _upload_json,
    download_json,
    extract_failure_evidence,
    heuristic_summary,
    normalize_locator,
)
from src.failure_memory import (
    get_failure_memory,
    search_similar_failures,
    store_failure_memory,
    store_feedback,
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")




def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _cached_agent_is_usable(cached: Dict[str, Any], current_model: Dict[str, Any]) -> bool:
    """Do not reuse stale heuristic output once the embedded model is available."""
    configured_provider = str(current_model.get("provider") or "").lower()
    cached_provider = str(cached.get("provider") or "").lower()
    cached_inference = cached.get("inference") if isinstance(cached.get("inference"), dict) else {}

    if configured_provider != "embedded_llama_cpp":
        return True

    if current_model.get("model_ready_for_load"):
        if cached_provider != "embedded_llama_cpp":
            return False
        if cached_inference.get("source") != "model":
            return False
        cached_model = cached.get("model") if isinstance(cached.get("model"), dict) else {}
        current_fingerprint = current_model.get("model_fingerprint")
        cached_fingerprint = cached_model.get("model_fingerprint")
        if current_fingerprint and cached_fingerprint and current_fingerprint != cached_fingerprint:
            return False

    return True

def _agent_cache_blob_name(blob_dir: str, selected_test_id: Optional[str]) -> str:
    if selected_test_id:
        return f"{blob_dir}/ai-agent-tests/{_safe_cache_key(selected_test_id)}.json"
    return f"{blob_dir}/ai-agent.json"


def _selected_failure(evidence: Dict[str, Any]) -> Dict[str, Any]:
    failures = evidence.get("failures") or []
    if failures and isinstance(failures[0], dict):
        return failures[0]
    selected = evidence.get("selected_test")
    return selected if isinstance(selected, dict) else {}


def _clip_text(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _compact_agent_context(evidence: Dict[str, Any], similar: list[Dict[str, Any]]) -> Dict[str, Any]:
    failure = _selected_failure(evidence)
    run = evidence.get("run") or {}

    labels = failure.get("labels") if isinstance(failure.get("labels"), dict) else {}
    compact_labels = {
        _clip_text(key, 60): _clip_text(value, 160)
        for key, value in list(labels.items())[:12]
    }

    compact_steps = []
    for step in (failure.get("failed_steps") or [])[:6]:
        if not isinstance(step, dict):
            continue
        compact_steps.append({
            "name": _clip_text(step.get("name"), 180),
            "status": _clip_text(step.get("status"), 40),
            "message": _clip_text(step.get("message"), 320),
        })

    # URLs and Blob source identifiers do not help causal reasoning and consume
    # a large part of Phi-3's 4K context window, so they are intentionally omitted.
    compact_attachments = []
    for attachment in (failure.get("attachments") or [])[:4]:
        if not isinstance(attachment, dict):
            continue
        compact_attachments.append({
            "name": _clip_text(attachment.get("name"), 100),
            "type": _clip_text(attachment.get("type"), 80),
            "message": _clip_text(attachment.get("message"), 360),
        })

    compact_similar = []
    for match in similar[:5]:
        if not isinstance(match, dict):
            continue
        compact_similar.append({
            "score": match.get("score"),
            "run_id": _clip_text(match.get("run_id"), 100),
            "suite": _clip_text(match.get("suite"), 80),
            "env": _clip_text(match.get("env"), 40),
            "platform": _clip_text(match.get("platform"), 40),
            "test_name": _clip_text(match.get("test_name"), 220),
            "status": _clip_text(match.get("status"), 40),
            "memory_status": _clip_text(match.get("memory_status"), 40),
            "human_verified": bool(match.get("human_verified")),
            "failure_cause": _clip_text(match.get("failure_cause"), 360),
            "suggested_fix": _clip_text(match.get("suggested_fix"), 360),
        })

    return {
        "current_failure": {
            "run_id": _clip_text(run.get("run_id"), 100),
            "suite": _clip_text(run.get("suite"), 80),
            "env": _clip_text(run.get("env"), 40),
            "platform": _clip_text(run.get("platform"), 40),
            "status": _clip_text(failure.get("status") or run.get("status"), 40),
            "version": _clip_text(run.get("version"), 80),
            "build_number": _clip_text(run.get("build_number"), 80),
            "test_id": _clip_text(failure.get("test_id"), 120),
            "test_name": _clip_text(failure.get("name"), 220),
            "full_name": _clip_text(failure.get("full_name"), 280),
            "message": _clip_text(failure.get("message"), 600),
            "trace_excerpt": _clip_text(failure.get("trace_excerpt"), 900),
            "duration_ms": failure.get("duration_ms"),
            "labels": compact_labels,
            "failed_steps": compact_steps,
            "attachments": compact_attachments,
        },
        "allure_context": {
            "failure_status_counts": evidence.get("failure_status_counts"),
            "available_failure_count": evidence.get("available_failure_count"),
            "test_case_blob_count_scanned": (evidence.get("blob") or {}).get("test_case_blob_count_scanned"),
        },
        "similar_previous_failures": compact_similar,
    }


def _heuristic_agent(context: Dict[str, Any], evidence: Dict[str, Any]) -> Dict[str, Any]:
    summary = heuristic_summary(evidence)
    similar = context.get("similar_previous_failures") or []
    failure = context.get("current_failure") or {}
    cause_summary = summary.get("likely_root_cause") or summary.get("short_summary") or "No clear root cause was extracted."
    fix_summary = summary.get("suggested_fix") or "Open the Allure report and inspect the selected test evidence."
    historical_summary = "No similar previous failures were found."
    if similar:
        historical_summary = (
            f"Found {len(similar)} similar previous failure(s). "
            "Use them as supporting context, not as proof of the current root cause."
        )

    return normalize_agent_payload({
        "title": summary.get("title") or failure.get("test_name") or "AI failure analysis",
        "failure_cause": {
            "category": summary.get("failure_category") or "unknown",
            "summary": cause_summary,
            "confidence": summary.get("confidence") or "low",
        },
        "evidence": summary.get("evidence") or [],
        "suggested_fix": {
            "summary": fix_summary,
            "owner": "unknown",
            "steps": [
                "Open the embedded Allure report and inspect the selected test step that failed.",
                "Reproduce the same scenario on the same environment and deployment target.",
                "Check whether the product behavior, network response, and selector/wait condition match the test expectation.",
                "Apply a product, environment, or automation fix only after the evidence confirms the cause.",
            ],
        },
        "historical_insight": {
            "matched_previous_failures": len(similar),
            "summary": historical_summary,
            "matches": similar,
        },
    })


def _build_messages(context: Dict[str, Any]) -> list[Dict[str, str]]:
    # The published Phi-3 GGUF chat template may ignore a separate system role.
    # Put all instructions in the first user message so the model always sees them.
    instruction = (
        "You are AutomationHub AI Failure Agent. Analyze exactly one selected automated test failure.\n"
        "Use only EVIDENCE_JSON. Never repeat or copy the input object as the answer. "
        "Do not invent services, logs, URLs, owners, or fixes.\n"
        "Return exactly one concise JSON object and no markdown. Required shape:\n"
        "{\"title\":string,\"failure_cause\":{\"category\":string,\"summary\":string,"
        "\"confidence\":\"high|medium|low\"},\"evidence\":[{\"type\":string,\"value\":string}],"
        "\"suggested_fix\":{\"summary\":string,\"owner\":string,\"steps\":[string]},"
        "\"historical_insight\":{\"matched_previous_failures\":number,"
        "\"summary\":string,\"matches\":[]}}\n"
        "Allowed category values: product_bug, test_automation, infrastructure_or_environment, "
        "product_or_environment, product_or_test_automation, "
        "missing_or_incomplete_report_artifacts, unknown.\n"
        "Keep failure_cause.summary under 500 characters, include at most 5 evidence items, "
        "and at most 5 suggested_fix.steps. Always return historical_insight.matches as an empty array; "
        "the server adds the authoritative matches afterward.\n\n"
        "EVIDENCE_JSON:\n"
    )
    evidence_json = json.dumps(context, ensure_ascii=False, separators=(",", ":"))
    return [{"role": "user", "content": instruction + evidence_json}]


def _summary_compat_from_agent(agent: Dict[str, Any]) -> Dict[str, Any]:
    cause = agent.get("failure_cause") if isinstance(agent.get("failure_cause"), dict) else {}
    fix = agent.get("suggested_fix") if isinstance(agent.get("suggested_fix"), dict) else {}
    return {
        "title": agent.get("title") or "AI failure analysis",
        "failure_category": cause.get("category") or "unknown",
        "confidence": cause.get("confidence") or "low",
        "short_summary": cause.get("summary") or "No failure cause returned.",
        "likely_root_cause": cause.get("summary") or "No failure cause returned.",
        "suggested_fix": fix.get("summary") or "No suggested fix returned.",
        "evidence": agent.get("evidence") if isinstance(agent.get("evidence"), list) else [],
    }


def _attach_memory_state(
    bsc: Any,
    payload: Dict[str, Any],
    *,
    run_id: str,
    selected_test_id: Optional[str],
    selected_test_name: Optional[str],
) -> Dict[str, Any]:
    memory_ref = payload.get("memory") if isinstance(payload.get("memory"), dict) else {}
    selected = payload.get("selected_test") if isinstance(payload.get("selected_test"), dict) else {}
    memory = get_failure_memory(
        bsc,
        memory_id=str(memory_ref.get("memory_id") or "").strip() or None,
        run_id=run_id,
        test_id=(
            selected_test_id
            or selected.get("test_id")
            or selected.get("uid")
        ),
        test_name=(
            selected_test_name
            or selected.get("full_name")
            or selected.get("name")
        ),
    )

    if not memory:
        payload["memory_status"] = "not_stored"
        payload["retrieval_enabled"] = False
        payload["human_feedback"] = None
        return payload

    memory_summary = {
        **memory_ref,
        "memory_id": memory.get("memory_id"),
        "memory_status": memory.get("memory_status", "unreviewed"),
        "retrieval_enabled": memory.get("retrieval_enabled", True),
        "human_feedback": memory.get("human_feedback"),
        "effective_failure_cause": memory.get("effective_failure_cause"),
        "effective_suggested_fix": memory.get("effective_suggested_fix"),
    }
    payload["memory"] = memory_summary
    payload["memory_status"] = memory_summary["memory_status"]
    payload["retrieval_enabled"] = memory_summary["retrieval_enabled"]
    payload["human_feedback"] = memory_summary["human_feedback"]
    return payload


def get_or_create_test_agent_analysis(
    bsc: Any,
    *,
    suite: str,
    env: str,
    platform: str,
    run_id: str,
    refresh: bool = False,
    selected_test_id: Optional[str] = None,
    selected_test_blob: Optional[str] = None,
    selected_test_name: Optional[str] = None,
) -> Dict[str, Any]:
    locator = normalize_locator(suite=suite, env=env, platform=platform, run_id=run_id)
    blob_dir = _blob_dir(locator)
    selected_cache_id = selected_test_id or selected_test_blob or selected_test_name
    cache_blob = _agent_cache_blob_name(blob_dir, selected_cache_id)

    current_model = provider_status()
    if not refresh:
        cached = download_json(bsc=bsc, blob_name=cache_blob)
        if (
            isinstance(cached, dict)
            and cached.get("agent")
            and _cached_agent_is_usable(cached, current_model)
        ):
            cached["ok"] = True
            cached["cached"] = True
            return _attach_memory_state(
                bsc,
                cached,
                run_id=run_id,
                selected_test_id=selected_test_id,
                selected_test_name=selected_test_name,
            )

    evidence = extract_failure_evidence(
        bsc,
        **locator,
        selected_test_id=selected_test_id,
        selected_test_blob=selected_test_blob,
        selected_test_name=selected_test_name,
    )
    similar = search_similar_failures(bsc, evidence)
    context = _compact_agent_context(evidence, similar)

    provider_warning: Optional[str] = None
    provider_used = "heuristic"
    agent: Optional[Dict[str, Any]] = None

    messages = _build_messages(context)
    model_payload, provider_used, provider_warning, inference = call_llm_json(messages)
    if model_payload:
        agent = model_payload
    else:
        if get_provider_config().require_model_response and provider_used == "heuristic_fallback":
            raise RuntimeError(provider_warning or "The embedded model did not return a valid response.")
        agent = _heuristic_agent(context, evidence)
        if provider_used != "heuristic":
            provider_used = "heuristic_fallback"

    # Ensure historical matches are preserved even if the model omitted them.
    historical = agent.get("historical_insight") if isinstance(agent.get("historical_insight"), dict) else {}
    if similar and not historical.get("matches"):
        historical["matches"] = similar
        historical["matched_previous_failures"] = len(similar)
        historical.setdefault("summary", f"Found {len(similar)} similar previous failure(s).")
        agent["historical_insight"] = historical

    store_fallback_memory = _env_bool("AI_STORE_FALLBACK_MEMORY", False)
    if provider_used == "heuristic_fallback" and not store_fallback_memory:
        memory_write: Optional[Dict[str, Any]] = {
            "skipped": True,
            "reason": "Heuristic fallback results are not stored as AI failure memory by default.",
        }
    else:
        memory_write = store_failure_memory(bsc, evidence, agent, provider=provider_used)

    payload = {
        "ok": True,
        "cached": False,
        "generated_at": _utc_now_iso(),
        "provider": provider_used,
        "provider_warning": provider_warning,
        "fallback_used": provider_used == "heuristic_fallback",
        "actual_model_response": bool(
            provider_used == "embedded_llama_cpp"
            and inference.get("model_invoked")
            and inference.get("response_received")
            and inference.get("response_valid_json")
        ),
        "inference": inference,
        "model": provider_status(),
        "evidence_hash": evidence.get("evidence_hash"),
        "report": evidence.get("run"),
        "blob": evidence.get("blob"),
        "selected_test": evidence.get("selected_test"),
        "agent": agent,
        "summary": _summary_compat_from_agent(agent),
        "evidence": {
            "failure_count": evidence.get("failure_count"),
            "available_failure_count": evidence.get("available_failure_count"),
            "failure_status_counts": evidence.get("failure_status_counts"),
            "selected_test": evidence.get("selected_test"),
            "available_failures": evidence.get("available_failures", [])[:MAX_FAILURES],
            "failures": evidence.get("failures", [])[:12],
        },
        "memory": memory_write,
        "agent_cache_blob": cache_blob,
    }

    payload = _attach_memory_state(
        bsc,
        payload,
        run_id=run_id,
        selected_test_id=selected_test_id,
        selected_test_name=selected_test_name,
    )

    cache_fallback_results = _env_bool("AI_CACHE_FALLBACK_RESULTS", False)
    should_cache = provider_used != "heuristic_fallback" or cache_fallback_results
    if should_cache:
        try:
            _upload_json(bsc, cache_blob, payload)
        except Exception as exc:
            payload["cache_warning"] = f"Could not write AI agent cache: {exc}"
    else:
        payload["cache_skipped"] = "Heuristic fallback results are not cached by default."

    return payload


def save_test_agent_feedback(bsc: Any, payload: Dict[str, Any]) -> Dict[str, Any]:
    return store_feedback(bsc, payload)
