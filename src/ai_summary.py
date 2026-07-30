from __future__ import annotations

import hashlib
import json
import os
import re
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests

from src.utils.az import build_blob_dir, download_json, public_blob_url, require
from src.utils.vars import REPORTS_CONTAINER, REPORTS_STORAGE_ACCOUNT, MAX_BLOBS_TO_SCAN, \
    MAX_FAILURES, MAX_TEXT_CHARS, FAILED_STATUSES


SECRET_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|token|secret|password|authorization|bearer)\s*[:=]\s*[^\s,;]+"),
    re.compile(r"(?i)Bearer\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(r"(?i)(sig=)[A-Za-z0-9%._~+/=-]+"),
]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_segment(value: str, name: str, *, lower: bool = True) -> str:
    raw = str(value or "").strip()
    if lower:
        raw = raw.lower()
    if not raw or raw == "all":
        raise ValueError(f"Missing required {name}")
    if "/" in raw or "\\" in raw or ".." in raw:
        raise ValueError(f"Invalid {name}")
    return raw


def normalize_locator(
    *,
    suite: str,
    env: str,
    platform: str,
    run_id: str,
) -> Dict[str, str]:
    normalized_platform = _safe_segment(platform, "platform")
    if normalized_platform in {"white-label", "white label", "white_label"}:
        normalized_platform = "whitelabel"

    normalized_env = _safe_segment(env, "env")
    if normalized_env == "production":
        normalized_env = "prod"
    elif normalized_env == "staging":
        normalized_env = "stage"

    return {
        "suite": _safe_segment(suite, "suite"),
        "env": normalized_env,
        "platform": normalized_platform,
        "run_id": _safe_segment(run_id, "run_id", lower=False),
    }


def _container_client(bsc: Any) -> Any:
    require("REPORTS_CONTAINER", REPORTS_CONTAINER)
    return bsc.get_container_client(REPORTS_CONTAINER)


def _blob_exists(bsc: Any, blob_name: str) -> bool:
    try:
        return bool(_container_client(bsc).get_blob_client(blob_name).exists())
    except Exception:
        return False


def _upload_json(bsc: Any, blob_name: str, payload: Dict[str, Any]) -> None:
    raw = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    _container_client(bsc).upload_blob(blob_name, raw, overwrite=True)


def _redact(text: Any, *, limit: int = MAX_TEXT_CHARS) -> str:
    value = "" if text is None else str(text)
    for pattern in SECRET_PATTERNS:
        value = pattern.sub(lambda m: f"{m.group(1) if m.groups() else 'secret'}=[REDACTED]", value)
    value = value.replace("\x00", "")
    if len(value) > limit:
        return value[:limit].rstrip() + "…"
    return value


def _allowed_public_blob_url(url: str) -> bool:
    if not url:
        return False
    expected_host = f"https://{REPORTS_STORAGE_ACCOUNT}.blob.core.windows.net/{REPORTS_CONTAINER}/"
    return str(url).startswith(expected_host)


def _blob_dir(locator: Dict[str, str]) -> str:
    return build_blob_dir(
        suite=locator["suite"],
        env=locator["env"],
        platform=locator["platform"],
        run_id=locator["run_id"],
    )


def _download_json_if_exists(bsc: Any, blob_name: str) -> Optional[Dict[str, Any]]:
    if not _blob_exists(bsc, blob_name):
        return None
    return download_json(bsc=bsc, blob_name=blob_name)


def _find_allure_root(bsc: Any, blob_dir: str) -> str:
    """
    Supports both layouts:
      <blob_dir>/widgets/summary.json
      <blob_dir>/awesome/widgets/summary.json
    """
    candidates = [blob_dir, f"{blob_dir}/awesome"]
    for root in candidates:
        if _blob_exists(bsc, f"{root}/widgets/summary.json"):
            return root
    for root in candidates:
        if _blob_exists(bsc, f"{root}/index.html"):
            return root
    return blob_dir


def _resolve_report_url(bsc: Any, blob_dir: str, run_json: Optional[Dict[str, Any]]) -> str:
    for key in ("report_url", "reportUrl", "allure_report_url", "allureReportUrl"):
        url = str((run_json or {}).get(key) or "").strip()
        if _allowed_public_blob_url(url):
            return url

    allure_root = _find_allure_root(bsc, blob_dir)
    if _blob_exists(bsc, f"{allure_root}/index.html"):
        return public_blob_url(f"{allure_root}/index.html")

    # Preserve the original behavior as a fallback.
    return public_blob_url(f"{blob_dir}/index.html")


def _safe_cache_key(value: str) -> str:
    raw = str(value or "").strip()
    raw = raw.rsplit("/", 1)[-1]
    raw = raw.replace(".json", "")
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", raw).strip("-._")
    if not safe:
        safe = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return safe[:120]


def _cache_blob_name(blob_dir: str, *, selected_test_id: Optional[str] = None) -> str:
    if selected_test_id:
        return f"{blob_dir}/ai-summary-tests/{_safe_cache_key(selected_test_id)}.json"
    return f"{blob_dir}/ai-summary.json"


def report_summary_config() -> Dict[str, Any]:
    provider = os.getenv("AI_SUMMARY_PROVIDER", "").strip().lower()
    azure_ready = all(
        os.getenv(name, "").strip()
        for name in ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_DEPLOYMENT")
    )
    if not provider:
        provider = "azure_openai" if azure_ready else "heuristic"

    embedded_status: Dict[str, Any] = {}
    try:
        from src.ai_provider import provider_status
        embedded_status = provider_status()
    except Exception:
        embedded_status = {"provider": os.getenv("AI_PROVIDER", "embedded_llama_cpp")}

    return {
        "provider": provider,
        "agent_provider": embedded_status.get("provider", "embedded_llama_cpp"),
        "embedded_model": embedded_status,
        "azure_openai_configured": azure_ready,
        "cache_enabled": True,
        "max_failures": MAX_FAILURES,
        "max_test_case_blobs": MAX_BLOBS_TO_SCAN,
    }


def get_report_context(
    bsc: Any,
    *,
    suite: str,
    env: str,
    platform: str,
    run_id: str,
) -> Dict[str, Any]:
    locator = normalize_locator(suite=suite, env=env, platform=platform, run_id=run_id)
    blob_dir = _blob_dir(locator)
    run_blob = f"{blob_dir}/run.json"
    run_json = download_json(bsc=bsc, blob_name=run_blob) or {}
    allure_root = _find_allure_root(bsc, blob_dir)
    cache_blob = _cache_blob_name(blob_dir)

    return {
        **locator,
        "status": run_json.get("status", "unknown"),
        "version": run_json.get("version", "unknown"),
        "build_number": run_json.get("build_number", "unknown"),
        "started_at": run_json.get("started_at", ""),
        "finished_at": run_json.get("finished_at", ""),
        "report_url": _resolve_report_url(bsc, blob_dir, run_json),
        "blob_prefix": blob_dir,
        "allure_root": allure_root,
        "run_json_blob": run_blob,
        "ai_summary_blob": cache_blob,
        "ai_summary_cached": _blob_exists(bsc, cache_blob),
        "ai_config": report_summary_config(),
    }


def _list_json_blobs(bsc: Any, prefix: str, *, limit: int) -> List[str]:
    names: List[str] = []
    try:
        for blob in _container_client(bsc).list_blobs(name_starts_with=prefix):
            name = getattr(blob, "name", "")
            if name.endswith(".json"):
                names.append(name)
                if len(names) >= limit:
                    break
    except Exception:
        return names
    return names


def _labels_to_dict(labels: Any) -> Dict[str, str]:
    out: Dict[str, str] = {}
    if not isinstance(labels, list):
        return out
    interesting = {"suite", "parentSuite", "subSuite", "package", "feature", "story", "severity", "tag", "host", "thread"}
    for item in labels:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        value = str(item.get("value") or "").strip()
        if name in interesting and value:
            if name in out:
                out[name] = f"{out[name]}, {value}"
            else:
                out[name] = value
    return out


def _collect_attachments(node: Any) -> List[Dict[str, str]]:
    found: List[Dict[str, str]] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            attachments = value.get("attachments")
            if isinstance(attachments, list):
                for att in attachments:
                    if not isinstance(att, dict):
                        continue
                    source = str(att.get("source") or "").strip()
                    if source:
                        found.append({
                            "name": _redact(att.get("name") or "attachment", limit=160),
                            "type": _redact(att.get("type") or "", limit=100),
                            "source": source,
                        })
            for child_key in ("steps", "beforeStages", "afterStages", "testStage"):
                child = value.get(child_key)
                if child is not None:
                    walk(child)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(node)
    deduped: List[Dict[str, str]] = []
    seen = set()
    for item in found:
        source = item.get("source", "")
        if source and source not in seen:
            seen.add(source)
            deduped.append(item)
    return deduped[:10]


def _collect_failed_steps(node: Any) -> List[Dict[str, str]]:
    steps: List[Dict[str, str]] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            status = str(value.get("status") or "").lower()
            name = str(value.get("name") or "").strip()
            details = value.get("statusMessage") or value.get("message") or ""
            status_details = value.get("statusDetails")
            if isinstance(status_details, dict):
                details = details or status_details.get("message") or status_details.get("trace") or ""
            if name and status in FAILED_STATUSES:
                steps.append({
                    "name": _redact(name, limit=220),
                    "status": status,
                    "message": _redact(details, limit=500),
                })
            for child_key in ("steps", "beforeStages", "afterStages", "testStage"):
                child = value.get(child_key)
                if child is not None:
                    walk(child)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(node)
    return steps[:8]


def _attachment_urls(allure_root: str, attachments: List[Dict[str, str]]) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    for att in attachments:
        source = att.get("source") or ""
        if not source or "/" in source or "\\" in source:
            continue
        out.append({
            **att,
            "url": public_blob_url(f"{allure_root}/data/attachments/{source}"),
        })
    return out


def _test_id_from_blob(blob_name: str) -> str:
    name = str(blob_name or "").rsplit("/", 1)[-1]
    if name.endswith(".json"):
        name = name[:-5]
    return name


def _extract_failure_from_test_case(allure_root: str, blob_name: str, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    status = str(data.get("status") or "unknown").lower()
    if status not in FAILED_STATUSES:
        return None

    status_details = data.get("statusDetails") if isinstance(data.get("statusDetails"), dict) else {}
    time_obj = data.get("time") if isinstance(data.get("time"), dict) else {}
    attachments = _attachment_urls(allure_root, _collect_attachments(data))

    test_id = str(data.get("uid") or data.get("uuid") or data.get("historyId") or _test_id_from_blob(blob_name)).strip()

    return {
        "test_id": _redact(test_id, limit=160),
        "test_blob": blob_name,
        "name": _redact(data.get("name") or data.get("fullName") or "unknown test", limit=240),
        "full_name": _redact(data.get("fullName") or data.get("name") or "", limit=420),
        "status": status,
        "message": _redact(status_details.get("message") or data.get("statusMessage") or "", limit=900),
        "trace_excerpt": _redact(status_details.get("trace") or "", limit=1200),
        "duration_ms": time_obj.get("duration"),
        "labels": _labels_to_dict(data.get("labels")),
        "failed_steps": _collect_failed_steps(data),
        "attachments": attachments,
        "source_blob": blob_name,
    }


def _failure_matches_selection(
    failure: Dict[str, Any],
    *,
    selected_test_id: Optional[str] = None,
    selected_test_blob: Optional[str] = None,
    selected_test_name: Optional[str] = None,
) -> bool:
    wanted_id = str(selected_test_id or "").strip()
    wanted_blob = str(selected_test_blob or "").strip()
    wanted_name = str(selected_test_name or "").strip()

    if wanted_id and wanted_id in {
        str(failure.get("test_id") or ""),
        _test_id_from_blob(str(failure.get("test_blob") or "")),
        str(failure.get("source_blob") or ""),
    }:
        return True

    if wanted_blob and wanted_blob == str(failure.get("test_blob") or failure.get("source_blob") or ""):
        return True

    if wanted_name and wanted_name in {str(failure.get("name") or ""), str(failure.get("full_name") or "")}:
        return True

    return False


def _failure_selector(failure: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "test_id": failure.get("test_id") or _test_id_from_blob(str(failure.get("test_blob") or failure.get("source_blob") or "")),
        "test_blob": failure.get("test_blob") or failure.get("source_blob"),
        "name": failure.get("name"),
        "full_name": failure.get("full_name"),
        "status": failure.get("status"),
        "message": failure.get("message") or failure.get("trace_excerpt"),
        "duration_ms": failure.get("duration_ms"),
        "labels": failure.get("labels") or {},
        "attachments": failure.get("attachments") or [],
    }


def extract_failure_evidence(
    bsc: Any,
    *,
    suite: str,
    env: str,
    platform: str,
    run_id: str,
    selected_test_id: Optional[str] = None,
    selected_test_blob: Optional[str] = None,
    selected_test_name: Optional[str] = None,
) -> Dict[str, Any]:
    locator = normalize_locator(suite=suite, env=env, platform=platform, run_id=run_id)
    blob_dir = _blob_dir(locator)
    allure_root = _find_allure_root(bsc, blob_dir)
    run_blob = f"{blob_dir}/run.json"
    run_json = download_json(bsc=bsc, blob_name=run_blob) or {}

    widgets = {
        "summary": _download_json_if_exists(bsc, f"{allure_root}/widgets/summary.json"),
        "categories": _download_json_if_exists(bsc, f"{allure_root}/widgets/categories.json"),
        "suites": _download_json_if_exists(bsc, f"{allure_root}/widgets/suites.json"),
    }

    failures: List[Dict[str, Any]] = []
    test_case_blobs = _list_json_blobs(
        bsc,
        f"{allure_root}/data/test-cases/",
        limit=MAX_BLOBS_TO_SCAN,
    )

    for blob_name in test_case_blobs:
        if len(failures) >= MAX_FAILURES:
            break
        data = download_json(bsc=bsc, blob_name=blob_name)
        if not isinstance(data, dict):
            continue
        item = _extract_failure_from_test_case(allure_root, blob_name, data)
        if item:
            failures.append(item)

    available_failures = [_failure_selector(f) for f in failures]
    selection_requested = bool(selected_test_id or selected_test_blob or selected_test_name)
    selected_test: Optional[Dict[str, Any]] = None

    if selection_requested:
        selected_failures = [
            f for f in failures
            if _failure_matches_selection(
                f,
                selected_test_id=selected_test_id,
                selected_test_blob=selected_test_blob,
                selected_test_name=selected_test_name,
            )
        ]
        if not selected_failures:
            raise ValueError("Selected test was not found in the failed/broken Allure test-case artifacts")
        failures = selected_failures[:1]
        selected_test = _failure_selector(failures[0])

    status_counts = Counter([f.get("status", "unknown") for f in failures])
    evidence_hash = hashlib.sha256(
        json.dumps(
            {
                "locator": locator,
                "status": run_json.get("status"),
                "selected_test": selected_test,
                "failures": [
                    {
                        "test_id": f.get("test_id"),
                        "test_blob": f.get("test_blob"),
                        "name": f.get("name"),
                        "status": f.get("status"),
                        "message": f.get("message"),
                    }
                    for f in failures
                ],
            },
            sort_keys=True,
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()

    return {
        "locator": locator,
        "run": {
            "run_id": run_json.get("run_id", locator["run_id"]),
            "suite": run_json.get("suite", locator["suite"]),
            "env": run_json.get("env", locator["env"]),
            "platform": run_json.get("platform", locator["platform"]),
            "status": run_json.get("status", "unknown"),
            "version": run_json.get("version", "unknown"),
            "build_number": run_json.get("build_number", "unknown"),
            "started_at": run_json.get("started_at", ""),
            "finished_at": run_json.get("finished_at", ""),
            "report_url": _resolve_report_url(bsc, blob_dir, run_json),
        },
        "blob": {
            "blob_prefix": blob_dir,
            "allure_root": allure_root,
            "run_json_blob": run_blob,
            "test_case_blob_count_scanned": len(test_case_blobs),
            "test_case_scan_limit": MAX_BLOBS_TO_SCAN,
        },
        "widgets": widgets,
        "failure_count": len(failures),
        "available_failure_count": len(available_failures),
        "failure_status_counts": dict(status_counts),
        "selected_test": selected_test,
        "available_failures": available_failures,
        "failures": failures,
        "evidence_hash": evidence_hash,
    }


def _first_non_empty(values: Iterable[Any], default: str = "") -> str:
    for value in values:
        s = str(value or "").strip()
        if s:
            return s
    return default


def _category_from_text(text: str) -> Tuple[str, str]:
    t = text.lower()
    infra_terms = ["imagepull", "image pull", "container", "azure", "blob", "dns", "503", "504", "gateway", "connection refused", "timed out connecting", "network"]
    test_terms = ["strict mode violation", "locator", "selector", "expected", "assertion", "to be visible", "to have text", "pytest", "playwright"]
    product_terms = ["500", "api", "response", "search", "checkout", "booking", "not found", "empty result", "timeout waiting"]

    if any(term in t for term in infra_terms):
        return "infrastructure_or_environment", "medium"
    if any(term in t for term in product_terms) and any(term in t for term in test_terms):
        return "product_or_test_automation", "medium"
    if any(term in t for term in product_terms):
        return "product_or_environment", "medium"
    if any(term in t for term in test_terms):
        return "test_automation", "medium"
    return "unknown", "low"


def heuristic_summary(evidence: Dict[str, Any]) -> Dict[str, Any]:
    run = evidence.get("run") or {}
    failures = evidence.get("failures") or []
    combined_text = "\n".join(
        [
            _first_non_empty([f.get("message"), f.get("trace_excerpt"), f.get("name")])
            for f in failures[:10]
        ]
    )
    category, confidence = _category_from_text(combined_text)

    selected_test = evidence.get("selected_test") or {}

    if failures:
        first = failures[0]
        title = _redact(first.get("name") or "Failure summary", limit=100)
        primary_message = _first_non_empty(
            [first.get("message"), first.get("trace_excerpt")],
            default="No detailed error message was found in the Allure test-case artifacts.",
        )
        if selected_test:
            short_summary = (
                f"The selected test '{title}' failed in the {run.get('suite', 'unknown')} suite "
                f"on {run.get('env', 'unknown')}/{run.get('platform', 'unknown')}. "
                "The analysis below is scoped only to this selected test case."
            )
        else:
            short_summary = (
                f"The {run.get('suite', 'unknown')} suite on {run.get('env', 'unknown')}/{run.get('platform', 'unknown')} "
                f"completed with {len(failures)} failed or broken test case(s). The first visible failure is "
                f"'{title}'."
            )
    else:
        title = "No failed test cases found"
        primary_message = "The run status indicates a problem, but no failed/broken test-case JSON files were found in the Allure report artifacts."
        short_summary = (
            f"No failed or broken test cases were extracted for run {run.get('run_id', 'unknown')}. "
            "Check whether the report upload included the Allure data/test-cases artifacts."
        )
        category = "missing_or_incomplete_report_artifacts"
        confidence = "medium"

    suggested_fix = _suggested_fix(category, primary_message)

    return {
        "title": title,
        "failure_category": category,
        "confidence": confidence,
        "short_summary": short_summary,
        "likely_root_cause": _redact(primary_message, limit=900),
        "suggested_fix": suggested_fix,
        "evidence": [
            {
                "test": f.get("name"),
                "status": f.get("status"),
                "message": f.get("message") or f.get("trace_excerpt"),
                "attachments": f.get("attachments", []),
            }
            for f in failures[:8]
        ],
    }


def _suggested_fix(category: str, message: str) -> str:
    msg = message.lower()
    if category == "missing_or_incomplete_report_artifacts":
        return "Verify that the pipeline uploads the generated Allure report folder, including widgets and data/test-cases JSON files, not only the HTML entry point."
    if "selector" in msg or "locator" in msg or "strict mode" in msg:
        return "Inspect the failing selector in the Allure report. If the product UI changed intentionally, update the Playwright locator and add a more stable data-testid or role-based locator."
    if "timeout" in msg or "timed out" in msg:
        return "Reproduce the same flow on the reported environment and compare API/network timing. If the product response is slow, fix the service or adjust the test wait condition only after confirming the UI behavior is valid."
    if "500" in msg or "503" in msg or "504" in msg:
        return "Check the backend/API logs for the same run window and environment deployment, then rerun the affected suite after the service issue is resolved."
    if category == "infrastructure_or_environment":
        return "Check Azure Container App execution logs, image pull status, network/DNS errors, and Blob report upload completion before treating this as a product bug."
    return "Open the Allure report beside this summary, inspect the first failed test evidence, reproduce the scenario manually on the same environment, then decide whether the fix belongs in product code, test code, or environment configuration."


def _json_from_model_text(text: str) -> Optional[Dict[str, Any]]:
    raw = text.strip()
    raw = re.sub(r"^```(?:json)?", "", raw, flags=re.IGNORECASE).strip()
    raw = re.sub(r"```$", "", raw).strip()
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else None
    except Exception:
        match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
        if not match:
            return None
        try:
            value = json.loads(match.group(0))
            return value if isinstance(value, dict) else None
        except Exception:
            return None


def _compact_evidence_for_ai(evidence: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "run": evidence.get("run"),
        "failure_count": evidence.get("failure_count"),
        "available_failure_count": evidence.get("available_failure_count"),
        "failure_status_counts": evidence.get("failure_status_counts"),
        "selected_test": evidence.get("selected_test"),
        "failures": [
            {
                "name": f.get("name"),
                "full_name": f.get("full_name"),
                "status": f.get("status"),
                "message": f.get("message"),
                "trace_excerpt": f.get("trace_excerpt"),
                "failed_steps": f.get("failed_steps"),
                "attachments": f.get("attachments"),
                "labels": f.get("labels"),
            }
            for f in (evidence.get("failures") or [])[:12]
        ],
    }


def azure_openai_summary(evidence: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "").strip().rstrip("/")
    api_key = os.getenv("AZURE_OPENAI_API_KEY", "").strip()
    deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT", "").strip()
    api_version = os.getenv("AZURE_OPENAI_API_VERSION", "2024-05-01-preview").strip()
    timeout = float(os.getenv("AI_SUMMARY_TIMEOUT_SECONDS", "45"))

    if not endpoint or not api_key or not deployment:
        return None

    url = f"{endpoint}/openai/deployments/{deployment}/chat/completions?api-version={api_version}"
    prompt_payload = _compact_evidence_for_ai(evidence)

    system_prompt = (
        "You are an AutomationHub QA failure analysis agent. "
        "Summarize automated test failures using only the provided evidence. "
        "If selected_test is present, scope the summary only to that selected test case. "
        "Do not invent stack traces, URLs, services, owners, or root causes. "
        "Return strict JSON with keys: title, failure_category, confidence, short_summary, "
        "likely_root_cause, suggested_fix, evidence. "
        "failure_category must be one of: product_bug, test_automation, infrastructure_or_environment, "
        "product_or_environment, product_or_test_automation, missing_or_incomplete_report_artifacts, unknown. "
        "confidence must be high, medium, or low. evidence must be an array of concise objects."
    )

    user_prompt = json.dumps(prompt_payload, ensure_ascii=False)

    request_body = {
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.2,
        "max_tokens": int(os.getenv("AI_SUMMARY_MAX_TOKENS", "1400")),
        "response_format": {"type": "json_object"},
    }

    response = requests.post(
        url,
        headers={"api-key": api_key, "Content-Type": "application/json"},
        json=request_body,
        timeout=timeout,
    )

    # Some Azure OpenAI deployments/API versions do not accept response_format.
    # Retry once without it before falling back to the local heuristic summary.
    if response.status_code == 400 and "response_format" in response.text:
        request_body.pop("response_format", None)
        response = requests.post(
            url,
            headers={"api-key": api_key, "Content-Type": "application/json"},
            json=request_body,
            timeout=timeout,
        )

    response.raise_for_status()
    data = response.json()
    content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
    parsed = _json_from_model_text(content)
    return parsed


def generate_summary(evidence: Dict[str, Any]) -> Tuple[Dict[str, Any], str, Optional[str]]:
    config = report_summary_config()
    provider = config["provider"]

    if provider == "azure_openai":
        try:
            ai = azure_openai_summary(evidence)
            if ai:
                return ai, "azure_openai", None
        except Exception as exc:
            fallback = heuristic_summary(evidence)
            return fallback, "heuristic_fallback", f"Azure OpenAI summary failed: {exc}"

    return heuristic_summary(evidence), "heuristic", None



def list_report_failures(
    bsc: Any,
    *,
    suite: str,
    env: str,
    platform: str,
    run_id: str,
) -> Dict[str, Any]:
    locator = normalize_locator(suite=suite, env=env, platform=platform, run_id=run_id)
    evidence = extract_failure_evidence(bsc, **locator)
    return {
        "ok": True,
        "report": evidence.get("run"),
        "blob": evidence.get("blob"),
        "failure_count": evidence.get("available_failure_count", 0),
        "tests": evidence.get("available_failures", []),
    }

def get_or_create_report_summary(
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
    selected_cache_id = selected_test_id or _test_id_from_blob(selected_test_blob or "") or selected_test_name
    cache_blob = _cache_blob_name(blob_dir, selected_test_id=selected_cache_id)

    if not refresh:
        cached = download_json(bsc=bsc, blob_name=cache_blob)
        if isinstance(cached, dict) and cached.get("summary"):
            cached["ok"] = True
            cached["cached"] = True
            return cached

    evidence = extract_failure_evidence(
        bsc,
        **locator,
        selected_test_id=selected_test_id,
        selected_test_blob=selected_test_blob,
        selected_test_name=selected_test_name,
    )
    summary, provider_used, provider_warning = generate_summary(evidence)

    payload = {
        "ok": True,
        "cached": False,
        "generated_at": _utc_now_iso(),
        "provider": provider_used,
        "provider_warning": provider_warning,
        "evidence_hash": evidence.get("evidence_hash"),
        "report": evidence.get("run"),
        "blob": evidence.get("blob"),
        "summary": summary,
        "selected_test": evidence.get("selected_test"),
        "evidence": {
            "failure_count": evidence.get("failure_count"),
            "available_failure_count": evidence.get("available_failure_count"),
            "failure_status_counts": evidence.get("failure_status_counts"),
            "selected_test": evidence.get("selected_test"),
            "available_failures": evidence.get("available_failures", [])[:MAX_FAILURES],
            "failures": evidence.get("failures", [])[:12],
        },
    }

    try:
        _upload_json(bsc, cache_blob, payload)
    except Exception as exc:
        payload["cache_warning"] = f"Could not write AI summary cache: {exc}"

    return payload
