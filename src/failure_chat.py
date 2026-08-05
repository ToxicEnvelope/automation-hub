from __future__ import annotations

import json
import uuid
from typing import Any, Dict, List, Optional, Sequence
from urllib.parse import urlencode

from src.ai_agent import get_or_create_test_agent_analysis
from src.ai_provider import call_llm_json_schema, get_provider_config, provider_status

_ALLOWED_CONFIDENCE = {"high", "medium", "low"}
_ALLOWED_ANSWER_TYPES = {"evidence", "inference", "mixed", "unknown"}
MAX_CHAT_HISTORY_MESSAGES = 8
MAX_QUESTION_CHARS = 1500
MAX_HISTORY_CHARS = 1200
MAX_CHAT_TOKENS = 700

_CHAT_RESPONSE_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
        "confidence": {"type": "string", "enum": sorted(_ALLOWED_CONFIDENCE)},
        "answer_type": {"type": "string", "enum": sorted(_ALLOWED_ANSWER_TYPES)},
        "follow_up_suggestions": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": ["answer", "confidence", "answer_type", "follow_up_suggestions"],
}


def _clip(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _sanitize_history(history: Optional[Sequence[Dict[str, Any]]]) -> List[Dict[str, str]]:
    sanitized: List[Dict[str, str]] = []
    for item in list(history or [])[-MAX_CHAT_HISTORY_MESSAGES:]:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip().lower()
        if role not in {"user", "assistant"}:
            continue
        content = _clip(item.get("content"), MAX_HISTORY_CHARS)
        if content:
            sanitized.append({"role": role, "content": content})
    return sanitized


def _compact_evidence_refs(analysis_payload: Dict[str, Any]) -> List[Dict[str, str]]:
    agent = analysis_payload.get("agent") if isinstance(analysis_payload.get("agent"), dict) else {}
    refs: List[Dict[str, str]] = []

    for item in agent.get("evidence") or []:
        if not isinstance(item, dict):
            continue
        evidence_type = _clip(item.get("type") or item.get("status") or "evidence", 80)
        value = _clip(
            item.get("value")
            or item.get("message")
            or item.get("test")
            or item.get("name"),
            420,
        )
        if value:
            refs.append({"type": evidence_type or "evidence", "value": value})
        if len(refs) >= 5:
            break

    if refs:
        return refs

    failures = ((analysis_payload.get("evidence") or {}).get("failures") or [])
    for failure in failures[:1]:
        if not isinstance(failure, dict):
            continue
        message = _clip(failure.get("message") or failure.get("trace_excerpt"), 420)
        if message:
            refs.append({"type": "failure_message", "value": message})
        for step in (failure.get("failed_steps") or [])[:3]:
            if not isinstance(step, dict):
                continue
            value = _clip(step.get("message") or step.get("name"), 360)
            if value:
                refs.append({"type": "failed_step", "value": value})
        break

    return refs[:5]


def _compact_context(analysis_payload: Dict[str, Any]) -> Dict[str, Any]:
    report = analysis_payload.get("report") if isinstance(analysis_payload.get("report"), dict) else {}
    selected = analysis_payload.get("selected_test") if isinstance(analysis_payload.get("selected_test"), dict) else {}
    agent = analysis_payload.get("agent") if isinstance(analysis_payload.get("agent"), dict) else {}
    cause = agent.get("failure_cause") if isinstance(agent.get("failure_cause"), dict) else {}
    fix = agent.get("suggested_fix") if isinstance(agent.get("suggested_fix"), dict) else {}
    historical = agent.get("historical_insight") if isinstance(agent.get("historical_insight"), dict) else {}
    memory = analysis_payload.get("memory") if isinstance(analysis_payload.get("memory"), dict) else {}
    failures = ((analysis_payload.get("evidence") or {}).get("failures") or [])
    failure = failures[0] if failures and isinstance(failures[0], dict) else {}

    failed_steps = []
    for step in (failure.get("failed_steps") or [])[:5]:
        if not isinstance(step, dict):
            continue
        failed_steps.append({
            "name": _clip(step.get("name"), 180),
            "message": _clip(step.get("message"), 300),
        })

    attachments = []
    for attachment in (failure.get("attachments") or [])[:4]:
        if not isinstance(attachment, dict):
            continue
        attachments.append({
            "name": _clip(attachment.get("name"), 100),
            "type": _clip(attachment.get("type"), 80),
            "message": _clip(attachment.get("message"), 260),
        })

    matches = []
    for match in (historical.get("matches") or [])[:3]:
        if not isinstance(match, dict):
            continue
        matches.append({
            "test_name": _clip(match.get("test_name"), 180),
            "score": match.get("score"),
            "memory_status": _clip(match.get("memory_status"), 40),
            "failure_cause": _clip(match.get("failure_cause"), 360),
            "suggested_fix": _clip(match.get("suggested_fix"), 360),
        })

    return {
        "run": {
            "run_id": _clip(report.get("run_id"), 100),
            "suite": _clip(report.get("suite"), 80),
            "env": _clip(report.get("env"), 40),
            "platform": _clip(report.get("platform"), 40),
            "version": _clip(report.get("version"), 80),
            "build_number": _clip(report.get("build_number"), 80),
        },
        "selected_test": {
            "test_id": _clip(selected.get("test_id") or selected.get("uid"), 120),
            "name": _clip(selected.get("name"), 220),
            "full_name": _clip(selected.get("full_name"), 280),
            "status": _clip(selected.get("status"), 40),
        },
        "failure_evidence": {
            "message": _clip(failure.get("message"), 650),
            "trace_excerpt": _clip(failure.get("trace_excerpt"), 900),
            "failed_steps": failed_steps,
            "attachments": attachments,
        },
        "existing_analysis": {
            "category": _clip(cause.get("category"), 80),
            "summary": _clip(cause.get("summary"), 500),
            "confidence": _clip(cause.get("confidence"), 20),
            "suggested_fix": _clip(fix.get("summary"), 500),
            "suggested_steps": [_clip(step, 300) for step in (fix.get("steps") or [])[:5]],
        },
        "reviewed_memory": {
            "status": _clip(memory.get("memory_status"), 40),
            "effective_failure_cause": _clip(memory.get("effective_failure_cause"), 500),
            "effective_suggested_fix": _clip(memory.get("effective_suggested_fix"), 500),
        },
        "similar_previous_failures": matches,
    }


def _build_messages(
    *,
    context: Dict[str, Any],
    question: str,
    history: List[Dict[str, str]],
) -> List[Dict[str, str]]:
    instruction = (
        "You are AutomationHub Failure Chat Assistant. Answer a question about exactly one selected failed test.\n"
        "Use only FAILURE_CONTEXT_JSON and CHAT_HISTORY_JSON. Do not invent HTTP statuses, source files, "
        "selectors, logs, screenshots, services, or root causes that are not present.\n"
        "Clearly state when the evidence cannot determine an answer. Distinguish facts from inference. "
        "Keep the answer concise and practical, under 900 characters. Return JSON only and no markdown.\n"
        "Required shape: {\"answer\":string,\"confidence\":\"high|medium|low\","
        "\"answer_type\":\"evidence|inference|mixed|unknown\","
        "\"follow_up_suggestions\":[string]}. Include at most 3 follow-up suggestions.\n\n"
        f"FAILURE_CONTEXT_JSON:\n{json.dumps(context, ensure_ascii=False, separators=(',', ':'))}\n\n"
        f"CHAT_HISTORY_JSON:\n{json.dumps(history, ensure_ascii=False, separators=(',', ':'))}\n\n"
        f"CURRENT_QUESTION:\n{question}"
    )
    # Phi-3 GGUF templates can ignore a separate system role, so keep all
    # instructions and context in the first user message.
    return [{"role": "user", "content": instruction}]


def _heuristic_answer(context: Dict[str, Any], question: str) -> Dict[str, Any]:
    existing = context.get("existing_analysis") or {}
    history = context.get("similar_previous_failures") or []
    reviewed = context.get("reviewed_memory") or {}
    q = question.lower()

    reviewed_cause = str(reviewed.get("effective_failure_cause") or "").strip()
    reviewed_fix = str(reviewed.get("effective_suggested_fix") or "").strip()
    cause = reviewed_cause or str(existing.get("summary") or "The available evidence does not identify a precise root cause.")
    fix = reviewed_fix or str(existing.get("suggested_fix") or "Open the full report and inspect the failed step evidence.")
    confidence = str(existing.get("confidence") or "low").lower()
    if confidence not in _ALLOWED_CONFIDENCE:
        confidence = "low"

    if any(token in q for token in ("before", "previous", "again", "recurr", "happen")):
        if history:
            answer = f"Yes. AutomationHub found {len(history)} similar previous failure(s). The strongest historical cause is: {history[0].get('failure_cause') or 'not recorded'}."
        else:
            answer = "No similar reviewed failure was found in the current AI memory."
        answer_type = "evidence"
    elif any(token in q for token in ("check", "first", "next", "fix", "recommend")):
        answer = f"Start with this check: {fix}"
        answer_type = "mixed"
    elif any(token in q for token in ("bug report", "ticket", "defect")):
        test_name = ((context.get("selected_test") or {}).get("full_name") or (context.get("selected_test") or {}).get("name") or "Selected test")
        answer = f"Draft defect: {test_name} failed. Observed cause: {cause}. Recommended verification: {fix}"
        answer_type = "mixed"
    elif any(token in q for token in ("evidence", "prove", "support")):
        message = ((context.get("failure_evidence") or {}).get("message") or "No detailed failure message was available.")
        answer = f"The strongest direct evidence is: {message} The root-cause conclusion remains an inference unless logs or trace details confirm it."
        answer_type = "mixed"
    else:
        answer = cause
        answer_type = "inference"

    return {
        "answer": _clip(answer, 900),
        "confidence": confidence,
        "answer_type": answer_type,
        "follow_up_suggestions": [
            "What evidence supports that conclusion?",
            "Has this failure happened before?",
            "What should I check first?",
        ],
    }


def _normalize_chat_payload(value: Dict[str, Any]) -> Dict[str, Any]:
    confidence = str(value.get("confidence") or "low").strip().lower()
    if confidence not in _ALLOWED_CONFIDENCE:
        confidence = "low"
    answer_type = str(value.get("answer_type") or "unknown").strip().lower()
    if answer_type not in _ALLOWED_ANSWER_TYPES:
        answer_type = "unknown"
    suggestions = value.get("follow_up_suggestions")
    if not isinstance(suggestions, list):
        suggestions = []
    return {
        "answer": _clip(value.get("answer") or "The model did not return an answer.", 1200),
        "confidence": confidence,
        "answer_type": answer_type,
        "follow_up_suggestions": [
            _clip(item, 180) for item in suggestions if _clip(item, 180)
        ][:3],
    }


def ask_failure_chat(
    bsc: Any,
    *,
    suite: str,
    env: str,
    platform: str,
    run_id: str,
    question: str,
    selected_test_id: Optional[str] = None,
    selected_test_blob: Optional[str] = None,
    selected_test_name: Optional[str] = None,
    conversation_id: Optional[str] = None,
    history: Optional[Sequence[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    question = str(question or "").strip()
    if not question:
        raise ValueError("question is required")
    if len(question) > MAX_QUESTION_CHARS:
        raise ValueError(f"question must be at most {MAX_QUESTION_CHARS} characters")
    if not (selected_test_id or selected_test_blob or selected_test_name):
        raise ValueError("Select a failed test before asking the AI assistant")

    analysis = get_or_create_test_agent_analysis(
        bsc,
        suite=suite,
        env=env,
        platform=platform,
        run_id=run_id,
        refresh=False,
        selected_test_id=selected_test_id,
        selected_test_blob=selected_test_blob,
        selected_test_name=selected_test_name,
    )
    context = _compact_context(analysis)
    clean_history = _sanitize_history(history)
    messages = _build_messages(context=context, question=question, history=clean_history)

    model_payload, provider_used, provider_warning, inference = call_llm_json_schema(
        messages,
        response_schema=_CHAT_RESPONSE_SCHEMA,
        max_tokens=MAX_CHAT_TOKENS,
    )
    if model_payload:
        chat = _normalize_chat_payload(model_payload)
    else:
        if get_provider_config().require_model_response and provider_used == "heuristic_fallback":
            raise RuntimeError(provider_warning or "The embedded model did not return a valid chat response.")
        chat = _heuristic_answer(context, question)
        if provider_used != "heuristic":
            provider_used = "heuristic_fallback"

    selected = analysis.get("selected_test") if isinstance(analysis.get("selected_test"), dict) else {}
    report = analysis.get("report") if isinstance(analysis.get("report"), dict) else {}
    cid = _clip(conversation_id, 80) or f"chat_{uuid.uuid4().hex[:16]}"
    report_query = urlencode({
        "suite": suite,
        "env": env,
        "platform": platform,
        "run_id": run_id,
        "test_id": selected.get("test_id") or selected_test_id or "",
    })

    return {
        "ok": True,
        "conversation_id": cid,
        **chat,
        "evidence": _compact_evidence_refs(analysis),
        "selected_test": selected,
        "report": report,
        "report_viewer_url": f"/report-viewer.html?{report_query}",
        "provider": provider_used,
        "provider_warning": provider_warning,
        "fallback_used": provider_used in {"heuristic", "heuristic_fallback"},
        "actual_model_response": bool(
            provider_used == "embedded_llama_cpp"
            and inference.get("model_invoked")
            and inference.get("response_received")
            and inference.get("response_valid_json")
        ),
        "inference": inference,
        "model": provider_status(),
        "analysis": {
            "cached": bool(analysis.get("cached")),
            "memory_status": analysis.get("memory_status"),
            "evidence_hash": analysis.get("evidence_hash"),
        },
    }
