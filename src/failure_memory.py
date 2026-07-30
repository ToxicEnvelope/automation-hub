from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from src.utils.az import require
from src.utils.vars import REPORTS_CONTAINER

MEMORY_INDEX_BLOB = "ai-memory/index/failure-index.jsonl"
MAX_INDEX_CHARS = 2_000_000
MAX_MATCHES = 5


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _container_client(bsc: Any) -> Any:
    require("REPORTS_CONTAINER", REPORTS_CONTAINER)
    return bsc.get_container_client(REPORTS_CONTAINER)


def _download_text(bsc: Any, blob_name: str) -> str:
    try:
        raw = _container_client(bsc).download_blob(blob_name).readall()
        return raw.decode("utf-8", errors="replace")
    except Exception:
        return ""


def _upload_text(bsc: Any, blob_name: str, text: str) -> None:
    _container_client(bsc).upload_blob(blob_name, text.encode("utf-8"), overwrite=True)


def _upload_json(bsc: Any, blob_name: str, payload: Dict[str, Any]) -> None:
    raw = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    _container_client(bsc).upload_blob(blob_name, raw, overwrite=True)


def _safe_key(value: Any, *, max_len: int = 120) -> str:
    raw = str(value or "").strip()
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", raw).strip("-._")
    if not safe:
        safe = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return safe[:max_len]


def _normalize_text(value: Any) -> str:
    text = str(value or "").lower()
    text = re.sub(r"https?://\S+", " url ", text)
    text = re.sub(r"[a-f0-9]{16,}", " hash ", text)
    text = re.sub(r"\b\d+\b", " n ", text)
    text = re.sub(r"/[^\s]+", " path ", text)
    text = re.sub(r"[^a-z0-9_.#:-]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()[:900]


def _tokens(text: str) -> set[str]:
    ignore = {"the", "and", "for", "with", "that", "this", "was", "were", "not", "from", "into", "timeout", "error", "failed"}
    return {t for t in re.split(r"\s+", text) if len(t) >= 3 and t not in ignore}


def selected_failure_from_evidence(evidence: Dict[str, Any]) -> Dict[str, Any]:
    failures = evidence.get("failures") or []
    if failures and isinstance(failures[0], dict):
        return failures[0]
    selected = evidence.get("selected_test")
    return selected if isinstance(selected, dict) else {}


def build_failure_signature(evidence: Dict[str, Any]) -> Dict[str, Any]:
    run = evidence.get("run") or {}
    failure = selected_failure_from_evidence(evidence)
    test_name = failure.get("full_name") or failure.get("name") or "unknown-test"
    message = failure.get("message") or failure.get("trace_excerpt") or ""
    labels = failure.get("labels") if isinstance(failure.get("labels"), dict) else {}
    normalized_message = _normalize_text(message)
    normalized_test = _normalize_text(test_name)
    basis = "|".join([
        str(run.get("suite") or ""),
        normalized_test,
        normalized_message[:420],
        str(labels.get("feature") or ""),
        str(labels.get("story") or ""),
    ])
    digest = hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]
    slug_source = f"{test_name}-{normalized_message[:80]}"
    return {
        "signature": f"{_safe_key(slug_source, max_len=70)}-{digest}",
        "signature_hash": digest,
        "normalized_test": normalized_test,
        "normalized_message": normalized_message,
        "test_tokens": sorted(_tokens(normalized_test)),
        "message_tokens": sorted(_tokens(normalized_message)),
    }


def _read_index_records(bsc: Any) -> List[Dict[str, Any]]:
    raw = _download_text(bsc, MEMORY_INDEX_BLOB)
    if len(raw) > MAX_INDEX_CHARS:
        raw = raw[-MAX_INDEX_CHARS:]
    records: List[Dict[str, Any]] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
            if isinstance(item, dict):
                records.append(item)
        except Exception:
            continue
    return records


def _score_match(current: Dict[str, Any], candidate: Dict[str, Any]) -> float:
    score = 0.0
    if current.get("signature") and current.get("signature") == candidate.get("signature"):
        score += 0.55
    if current.get("normalized_test") and current.get("normalized_test") == candidate.get("normalized_test"):
        score += 0.25
    c_msg = set(current.get("message_tokens") or [])
    r_msg = set(candidate.get("message_tokens") or [])
    if c_msg and r_msg:
        score += 0.20 * (len(c_msg & r_msg) / max(1, len(c_msg | r_msg)))
    c_test = set(current.get("test_tokens") or [])
    r_test = set(candidate.get("test_tokens") or [])
    if c_test and r_test:
        score += 0.10 * (len(c_test & r_test) / max(1, len(c_test | r_test)))
    if current.get("suite") and current.get("suite") == candidate.get("suite"):
        score += 0.05
    return min(score, 1.0)


def search_similar_failures(bsc: Any, evidence: Dict[str, Any], *, limit: int = MAX_MATCHES) -> List[Dict[str, Any]]:
    run = evidence.get("run") or {}
    current = build_failure_signature(evidence)
    current.update({
        "suite": run.get("suite"),
        "run_id": run.get("run_id"),
    })
    current_test = selected_failure_from_evidence(evidence).get("test_id")

    matches: List[Tuple[float, Dict[str, Any]]] = []
    for record in reversed(_read_index_records(bsc)):
        if record.get("run_id") == run.get("run_id") and record.get("test_id") == current_test:
            continue
        score = _score_match(current, record)
        if score >= 0.18:
            item = {
                "score": round(score, 3),
                "run_id": record.get("run_id"),
                "suite": record.get("suite"),
                "env": record.get("env"),
                "platform": record.get("platform"),
                "test_name": record.get("test_name"),
                "status": record.get("status"),
                "failure_cause": record.get("failure_cause"),
                "suggested_fix": record.get("suggested_fix"),
                "created_at": record.get("created_at"),
            }
            matches.append((score, item))
    matches.sort(key=lambda x: x[0], reverse=True)
    return [m for _, m in matches[:limit]]


def store_failure_memory(bsc: Any, evidence: Dict[str, Any], agent: Dict[str, Any], *, provider: str) -> Optional[Dict[str, Any]]:
    run = evidence.get("run") or {}
    failure = selected_failure_from_evidence(evidence)
    if not failure:
        return None

    sig = build_failure_signature(evidence)
    test_id = str(failure.get("test_id") or failure.get("name") or "unknown-test")
    memory_id = f"{run.get('run_id', 'unknown')}-{_safe_key(test_id, max_len=80)}"
    cause = agent.get("failure_cause") if isinstance(agent.get("failure_cause"), dict) else {}
    fix = agent.get("suggested_fix") if isinstance(agent.get("suggested_fix"), dict) else {}

    record = {
        "memory_id": memory_id,
        "created_at": utc_now_iso(),
        "provider": provider,
        "signature": sig.get("signature"),
        "signature_hash": sig.get("signature_hash"),
        "normalized_test": sig.get("normalized_test"),
        "normalized_message": sig.get("normalized_message"),
        "test_tokens": sig.get("test_tokens"),
        "message_tokens": sig.get("message_tokens"),
        "run_id": run.get("run_id"),
        "suite": run.get("suite"),
        "env": run.get("env"),
        "platform": run.get("platform"),
        "status": failure.get("status") or run.get("status"),
        "test_id": test_id,
        "test_name": failure.get("full_name") or failure.get("name"),
        "error_message": failure.get("message") or failure.get("trace_excerpt"),
        "failure_cause": cause.get("summary"),
        "failure_category": cause.get("category"),
        "confidence": cause.get("confidence"),
        "suggested_fix": fix.get("summary"),
        "human_feedback": None,
    }

    detail_blob = f"ai-memory/failures/{_safe_key(sig.get('signature'), max_len=100)}/{_safe_key(memory_id, max_len=120)}.json"
    try:
        _upload_json(bsc, detail_blob, record)
        records = _read_index_records(bsc)
        records = [r for r in records if r.get("memory_id") != memory_id]
        records.append(record)
        if len(records) > 3000:
            records = records[-3000:]
        _upload_text(bsc, MEMORY_INDEX_BLOB, "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n")
        return {"record_blob": detail_blob, "index_blob": MEMORY_INDEX_BLOB, "signature": sig.get("signature")}
    except Exception:
        return None


def store_feedback(bsc: Any, payload: Dict[str, Any]) -> Dict[str, Any]:
    run_id = _safe_key(payload.get("run_id") or "unknown-run")
    test_id = _safe_key(payload.get("test_id") or payload.get("test_name") or "unknown-test")
    feedback_blob = f"ai-memory/feedback/{run_id}-{test_id}-{int(datetime.now(timezone.utc).timestamp())}.json"
    record = {"created_at": utc_now_iso(), **payload}
    _upload_json(bsc, feedback_blob, record)
    return {"ok": True, "feedback_blob": feedback_blob}
