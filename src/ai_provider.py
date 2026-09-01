from __future__ import annotations

import concurrent.futures
import hashlib
import json
import logging
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from src.utils.vars import BASE_DIR

_ALLOWED_CATEGORIES = {
    "product_bug",
    "test_automation",
    "infrastructure_or_environment",
    "product_or_environment",
    "product_or_test_automation",
    "missing_or_incomplete_report_artifacts",
    "unknown",
}
_ALLOWED_CONFIDENCE = {"high", "medium", "low"}
_TRUE_VALUES = {"1", "true", "yes", "on"}
logger = logging.getLogger("automationhub.ai")


_AGENT_RESPONSE_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "failure_cause": {
            "type": "object",
            "properties": {
                "category": {"type": "string", "enum": sorted(_ALLOWED_CATEGORIES)},
                "summary": {"type": "string"},
                "confidence": {"type": "string", "enum": sorted(_ALLOWED_CONFIDENCE)},
            },
            "required": ["category", "summary", "confidence"],
        },
        "evidence": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string"},
                    "value": {"type": "string"},
                },
                "required": ["type", "value"],
            },
        },
        "suggested_fix": {
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
                "owner": {"type": "string"},
                "steps": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["summary", "owner", "steps"],
        },
        "historical_insight": {
            "type": "object",
            "properties": {
                "matched_previous_failures": {"type": "integer"},
                "summary": {"type": "string"},
                "matches": {"type": "array", "items": {"type": "object"}},
            },
            "required": ["matched_previous_failures", "summary", "matches"],
        },
    },
    "required": [
        "title",
        "failure_cause",
        "evidence",
        "suggested_fix",
        "historical_insight",
    ],
}


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in _TRUE_VALUES


def _env_int(name: str, default: int) -> int:
    """
    Like os.getenv(name, default), but also treats an explicitly empty or
    non-numeric value as "not set" instead of crashing. os.getenv's own
    default only kicks in when the variable is entirely absent -- a
    container/orchestrator setting it to "" (e.g. an unset templated value)
    would otherwise reach int("") and raise, taking down every /api/ai/*
    endpoint on the next config read.
    """
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw.strip())
    except ValueError:
        logger.warning("Ignoring invalid %s=%r; using default %s", name, raw, default)
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw.strip())
    except ValueError:
        logger.warning("Ignoring invalid %s=%r; using default %s", name, raw, default)
        return default


def json_from_model_text(text: str) -> Optional[Dict[str, Any]]:
    """Extract one complete JSON object without repairing truncated model output."""
    raw = str(text or "").strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE).strip()
    raw = re.sub(r"\s*```$", "", raw).strip()
    if not raw:
        return None

    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    except (TypeError, ValueError, json.JSONDecodeError):
        pass

    # Some models add a short textual prefix/suffix around an otherwise valid
    # top-level object. Decode only from the first opening brace; scanning later
    # braces could incorrectly accept a nested object from truncated output.
    first_object = raw.find("{")
    if first_object < 0:
        return None
    try:
        parsed, _ = json.JSONDecoder().raw_decode(raw[first_object:])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def normalize_agent_payload(value: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize an LLM response into the AutomationHub AI Failure Agent schema."""
    failure_cause = value.get("failure_cause") if isinstance(value.get("failure_cause"), dict) else {}
    suggested_fix = value.get("suggested_fix") if isinstance(value.get("suggested_fix"), dict) else {}
    historical = value.get("historical_insight") if isinstance(value.get("historical_insight"), dict) else {}

    category = str(failure_cause.get("category") or value.get("failure_category") or "unknown").strip().lower()
    if category not in _ALLOWED_CATEGORIES:
        category = "unknown"

    confidence = str(failure_cause.get("confidence") or value.get("confidence") or "low").strip().lower()
    if confidence not in _ALLOWED_CONFIDENCE:
        confidence = "low"

    evidence = value.get("evidence")
    if not isinstance(evidence, list):
        evidence = []

    steps = suggested_fix.get("steps")
    if not isinstance(steps, list):
        steps = []

    try:
        matched_previous_failures = int(historical.get("matched_previous_failures") or 0)
    except (TypeError, ValueError):
        matched_previous_failures = 0

    suggested_summary = suggested_fix.get("summary")
    if not suggested_summary and isinstance(value.get("suggested_fix"), str):
        suggested_summary = value.get("suggested_fix")

    return {
        "title": str(value.get("title") or "AI failure analysis").strip()[:180],
        "failure_cause": {
            "category": category,
            "summary": str(failure_cause.get("summary") or value.get("short_summary") or "No failure cause returned.").strip(),
            "confidence": confidence,
        },
        "evidence": evidence[:12],
        "suggested_fix": {
            "summary": str(suggested_summary or "No suggested fix returned.").strip(),
            "owner": str(suggested_fix.get("owner") or "unknown").strip()[:80],
            "steps": [str(step).strip() for step in steps if str(step).strip()][:8],
        },
        "historical_insight": {
            "matched_previous_failures": matched_previous_failures,
            "summary": str(historical.get("summary") or "No similar previous failures were used.").strip(),
            "matches": historical.get("matches") if isinstance(historical.get("matches"), list) else [],
        },
    }


@dataclass(frozen=True)
class AIProviderConfig:
    provider: str
    model_path: str
    model_name: str
    context_tokens: int
    threads: int
    max_tokens: int
    temperature: float
    timeout_seconds: int
    chat_format: str
    require_model_response: bool


_PROVIDER_LOCK = threading.Lock()
_EMBEDDED_MODEL: Any = None
_EMBEDDED_MODEL_PATH: Optional[str] = None
_MODEL_LOADED_AT: Optional[float] = None

# Dedicated single-lane executor for embedded model inference calls.
# llama.cpp has no safe mid-generation cancellation, so a slow/stuck call cannot
# be killed once started. Running it here lets the *caller* bound its own wait
# with future.result(timeout=...) instead of blocking indefinitely; the request
# thread gets its response (real or fallback) within AI_MODEL_TIMEOUT_SECONDS
# even if the background call keeps running to completion afterward.
_INFERENCE_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    max_workers=1, thread_name_prefix="ah-ai-inference"
)
_INFERENCE_QUEUE_LOCK = threading.Lock()
_INFERENCE_QUEUE_DEPTH = 0
# Once this many calls are already queued/running on the single lane, a new
# request is told immediately to use the deterministic fallback instead of
# joining a backlog that AI_MODEL_TIMEOUT_SECONDS has no hope of draining.
_MAX_QUEUED_INFERENCE_CALLS = max(1, int(os.getenv("AI_MODEL_MAX_QUEUED_CALLS", "2")))


class InferenceTimeoutError(RuntimeError):
    """The embedded model did not return within AI_MODEL_TIMEOUT_SECONDS."""


class InferenceBusyError(RuntimeError):
    """Too many embedded model calls are already queued/in-flight."""


def _acquire_inference_slot() -> bool:
    global _INFERENCE_QUEUE_DEPTH
    with _INFERENCE_QUEUE_LOCK:
        if _INFERENCE_QUEUE_DEPTH >= _MAX_QUEUED_INFERENCE_CALLS:
            return False
        _INFERENCE_QUEUE_DEPTH += 1
        return True


def _release_inference_slot() -> None:
    global _INFERENCE_QUEUE_DEPTH
    with _INFERENCE_QUEUE_LOCK:
        _INFERENCE_QUEUE_DEPTH = max(0, _INFERENCE_QUEUE_DEPTH - 1)


def _default_model_path() -> str:
    # BASE_DIR points to <project>/src. The bundled model is stored at
    # <project>/models in both local development and the Runner image.
    return os.path.abspath(os.path.join(BASE_DIR, "..", "models", "automationhub-agent.gguf"))


def get_provider_config() -> AIProviderConfig:
    provider = (os.getenv("AI_PROVIDER") or os.getenv("AI_SUMMARY_PROVIDER") or "embedded_llama_cpp").strip().lower()
    configured_model_path = os.getenv("AI_MODEL_PATH", "").strip()
    model_path = configured_model_path or _default_model_path()
    return AIProviderConfig(
        provider=provider,
        model_path=model_path,
        model_name=os.getenv("AI_MODEL_NAME", "automationhub-agent.gguf").strip(),
        context_tokens=_env_int("AI_MODEL_CONTEXT_TOKENS", 4096),
        threads=_env_int("AI_MODEL_THREADS", max(1, os.cpu_count() or 4)),
        max_tokens=_env_int("AI_MODEL_MAX_TOKENS", 900),
        temperature=_env_float("AI_MODEL_TEMPERATURE", 0.0),
        timeout_seconds=_env_int("AI_MODEL_TIMEOUT_SECONDS", 60),
        chat_format=os.getenv("AI_MODEL_CHAT_FORMAT", "").strip(),
        require_model_response=_env_bool("AI_REQUIRE_MODEL_RESPONSE", False),
    )


_SPLIT_GGUF_PATTERN = re.compile(
    r"^(?P<prefix>.+)-(?P<index>\d+)-of-(?P<total>\d+)(?P<ext>\.gguf)$",
    re.IGNORECASE,
)


def _split_gguf_shard_paths(path: str) -> List[str]:
    """
    Return every expected shard path for a (possibly split) GGUF model.

    llama.cpp's own split-model convention names shards
    "<name>-00001-of-00002.gguf", "<name>-00002-of-00002.gguf", etc. When
    AI_MODEL_PATH points at one shard of such a set, llama.cpp's C++ loader
    already knows how to pull in the rest by filename pattern at load time --
    this function does not duplicate that loading logic. It exists purely so
    AutomationHub's own health/status checks and preflight validation can see
    the *whole* model instead of just the one referenced shard.

    A path that doesn't match the split naming convention returns a
    single-item list containing just itself, so callers can treat split and
    non-split models identically.
    """
    if not path:
        return [path]

    directory, filename = os.path.split(path)
    match = _SPLIT_GGUF_PATTERN.match(filename)
    if not match:
        return [path]

    try:
        total = int(match.group("total"))
    except ValueError:
        return [path]

    if total <= 0:
        return [path]

    index_width = len(match.group("index"))
    prefix = match.group("prefix")
    total_str = match.group("total")
    ext = match.group("ext")

    return [
        os.path.join(directory, f"{prefix}-{str(i).zfill(index_width)}-of-{total_str}{ext}")
        for i in range(1, total + 1)
    ]


def _model_file_status(path: str) -> Dict[str, Any]:
    shard_paths = _split_gguf_shard_paths(path)
    total_parts = len(shard_paths)

    size_bytes = 0
    all_exist = True
    all_readable = True
    latest_modified_ns: Optional[int] = None
    missing_parts: List[str] = []

    for shard_path in shard_paths:
        shard_exists = bool(shard_path and os.path.isfile(shard_path))
        if not shard_exists:
            all_exist = False
            all_readable = False
            missing_parts.append(os.path.basename(shard_path))
            continue

        if not os.access(shard_path, os.R_OK):
            all_readable = False

        try:
            stat = os.stat(shard_path)
            size_bytes += int(stat.st_size)
            mtime_ns = int(stat.st_mtime_ns)
            if latest_modified_ns is None or mtime_ns > latest_modified_ns:
                latest_modified_ns = mtime_ns
        except OSError:
            all_readable = False

    configured_sha256 = os.getenv("AI_MODEL_SHA256", "").strip().lower()
    fingerprint = configured_sha256 or (
        f"size:{size_bytes}:mtime_ns:{latest_modified_ns}" if all_exist else "missing"
    )
    return {
        "model_exists": all_exist,
        "model_readable": bool(all_exist and all_readable),
        "model_size_bytes": size_bytes,
        "model_nonempty": size_bytes > 0,
        "configured_sha256": configured_sha256 or None,
        "model_fingerprint": fingerprint,
        "model_parts_total": total_parts,
        "model_parts_found": total_parts - len(missing_parts),
        "model_parts_missing": missing_parts,
    }


def provider_status() -> Dict[str, Any]:
    cfg = get_provider_config()
    file_status = _model_file_status(cfg.model_path)
    loaded = bool(_EMBEDDED_MODEL is not None and _EMBEDDED_MODEL_PATH == cfg.model_path)
    return {
        "provider": cfg.provider,
        "model_name": cfg.model_name,
        "model_path": cfg.model_path,
        **file_status,
        "model_ready_for_load": bool(
            cfg.provider == "embedded_llama_cpp"
            and file_status["model_exists"]
            and file_status["model_readable"]
            and file_status["model_nonempty"]
        ),
        "model_loaded": loaded,
        "model_loaded_at_epoch": _MODEL_LOADED_AT if loaded else None,
        "load_mode": "lazy",
        "context_tokens": cfg.context_tokens,
        "threads": cfg.threads,
        "max_tokens": cfg.max_tokens,
        "temperature": cfg.temperature,
        "timeout_seconds": cfg.timeout_seconds,
        "chat_format": cfg.chat_format or "auto",
        "require_model_response": cfg.require_model_response,
        "max_queued_inference_calls": _MAX_QUEUED_INFERENCE_CALLS,
        "current_inference_queue_depth": _INFERENCE_QUEUE_DEPTH,
    }


def _load_embedded_llama_cpp_model(cfg: AIProviderConfig) -> Any:
    global _EMBEDDED_MODEL, _EMBEDDED_MODEL_PATH, _MODEL_LOADED_AT

    if not cfg.model_path:
        raise RuntimeError("AI_MODEL_PATH is empty")

    file_status = _model_file_status(cfg.model_path)
    if not file_status["model_exists"]:
        if file_status["model_parts_total"] > 1:
            missing = ", ".join(file_status["model_parts_missing"])
            raise RuntimeError(
                f"AI_MODEL_PATH points at a {file_status['model_parts_total']}-part split GGUF "
                f"({os.path.basename(cfg.model_path)}), but these parts are missing: {missing}. "
                "All shards must sit in the same directory, with their original filenames, for "
                "llama.cpp to load the split model correctly."
            )
        raise RuntimeError(
            f"Bundled GGUF model was not found at {cfg.model_path}. "
            "Place the model file under the project models/ directory before building the runner image."
        )
    if not file_status["model_readable"]:
        raise RuntimeError(f"Bundled GGUF model is not readable: {cfg.model_path}")
    if not file_status["model_nonempty"]:
        raise RuntimeError(f"Bundled GGUF model is empty: {cfg.model_path}")

    with _PROVIDER_LOCK:
        if _EMBEDDED_MODEL is not None and _EMBEDDED_MODEL_PATH == cfg.model_path:
            return _EMBEDDED_MODEL

        try:
            from llama_cpp import Llama  # type: ignore
        except Exception as exc:
            raise RuntimeError(
                "AI_PROVIDER=embedded_llama_cpp requires llama-cpp-python. "
                "Install dependencies from src/requirements.txt in the AutomationHub image."
            ) from exc

        llama_kwargs: Dict[str, Any] = {
            "model_path": cfg.model_path,
            "n_ctx": cfg.context_tokens,
            "n_threads": cfg.threads,
            "verbose": False,
        }
        if cfg.chat_format:
            llama_kwargs["chat_format"] = cfg.chat_format

        _EMBEDDED_MODEL = Llama(**llama_kwargs)
        _EMBEDDED_MODEL_PATH = cfg.model_path
        _MODEL_LOADED_AT = time.time()
        return _EMBEDDED_MODEL


def embedded_llama_cpp_chat(
    messages: List[Dict[str, str]],
    cfg: Optional[AIProviderConfig] = None,
    *,
    response_schema: Optional[Dict[str, Any]] = None,
    max_tokens: Optional[int] = None,
) -> Tuple[Optional[Dict[str, Any]], str, Dict[str, Any]]:
    cfg = cfg or get_provider_config()
    llm = _load_embedded_llama_cpp_model(cfg)

    started = time.perf_counter()
    completion_kwargs: Dict[str, Any] = {
        "messages": messages,
        "temperature": cfg.temperature,
        "max_tokens": int(max_tokens or cfg.max_tokens),
        "response_format": {
            "type": "json_object",
            "schema": response_schema or _AGENT_RESPONSE_SCHEMA,
        },
    }
    response_format_mode = "json_schema"

    def _run(kwargs: Dict[str, Any]) -> Any:
        # Runs on the single-lane executor thread. Always frees its queue slot
        # on the way out, even if the caller below gave up waiting first.
        try:
            return llm.create_chat_completion(**kwargs)
        finally:
            _release_inference_slot()

    def _call_with_timeout(kwargs: Dict[str, Any]) -> Any:
        if not _acquire_inference_slot():
            raise InferenceBusyError(
                f"AutomationHub AI agent already has {_MAX_QUEUED_INFERENCE_CALLS} "
                "request(s) queued; using the deterministic fallback instead of "
                "waiting in line."
            )
        future = _INFERENCE_EXECUTOR.submit(_run, kwargs)
        try:
            return future.result(timeout=cfg.timeout_seconds)
        except concurrent.futures.TimeoutError as exc:
            elapsed = round((time.perf_counter() - started) * 1000, 2)
            logger.warning(
                "AI model inference exceeded AI_MODEL_TIMEOUT_SECONDS=%s elapsed_ms=%s; "
                "returning the deterministic fallback while the model call keeps "
                "running in the background.",
                cfg.timeout_seconds,
                elapsed,
            )
            raise InferenceTimeoutError(
                f"Embedded model did not respond within {cfg.timeout_seconds}s"
            ) from exc

    try:
        result = _call_with_timeout(completion_kwargs)
    except (TypeError, ValueError) as exc:
        error_text = str(exc).lower()
        if "response_format" not in error_text and "schema" not in error_text:
            raise

        # Retain JSON grammar when an older binding rejects JSON Schema.
        response_format_mode = "json_object"
        completion_kwargs["response_format"] = {"type": "json_object"}
        try:
            result = _call_with_timeout(completion_kwargs)
        except (TypeError, ValueError) as json_exc:
            if "response_format" not in str(json_exc).lower():
                raise
            response_format_mode = "none"
            completion_kwargs.pop("response_format", None)
            result = _call_with_timeout(completion_kwargs)

    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    choice = (result.get("choices") or [{}])[0]
    message = choice.get("message") if isinstance(choice, dict) else {}
    content = message.get("content", "") if isinstance(message, dict) else ""
    raw = str(content or "")
    parsed = json_from_model_text(raw)
    usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}

    diagnostics = {
        "source": "model",
        "model_invoked": True,
        "response_received": bool(raw.strip()),
        "response_valid_json": bool(parsed),
        "response_chars": len(raw),
        "response_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest() if raw else None,
        "finish_reason": choice.get("finish_reason") if isinstance(choice, dict) else None,
        "usage": usage,
        "elapsed_ms": elapsed_ms,
        "response_format_used": response_format_mode != "none",
        "response_format_mode": response_format_mode,
        "prompt_chars": sum(len(str(message.get("content") or "")) for message in messages),
        "inference_id": uuid.uuid4().hex,
    }
    logger.info(
        "AI model inference completed inference_id=%s valid_json=%s response_chars=%s elapsed_ms=%s finish_reason=%s",
        diagnostics["inference_id"],
        diagnostics["response_valid_json"],
        diagnostics["response_chars"],
        diagnostics["elapsed_ms"],
        diagnostics["finish_reason"],
    )
    return parsed, raw, diagnostics



def call_llm_json_schema(
    messages: List[Dict[str, str]],
    *,
    response_schema: Dict[str, Any],
    max_tokens: Optional[int] = None,
) -> Tuple[Optional[Dict[str, Any]], str, Optional[str], Dict[str, Any]]:
    """Invoke the configured provider and return arbitrary schema-constrained JSON."""
    cfg = get_provider_config()
    base_diagnostics: Dict[str, Any] = {
        "source": "heuristic",
        "model_invoked": False,
        "response_received": False,
        "response_valid_json": False,
        "response_chars": 0,
        "response_sha256": None,
        "finish_reason": None,
        "usage": {},
        "elapsed_ms": None,
        "response_format_used": False,
        "response_format_mode": "none",
        "inference_id": uuid.uuid4().hex,
    }

    if cfg.provider == "heuristic":
        return None, "heuristic", None, base_diagnostics

    if cfg.provider == "embedded_llama_cpp":
        try:
            parsed, raw, diagnostics = embedded_llama_cpp_chat(
                messages,
                cfg,
                response_schema=response_schema,
                max_tokens=max_tokens,
            )
            if parsed:
                return parsed, "embedded_llama_cpp", None, diagnostics

            warning = "Embedded model returned non-JSON output; deterministic fallback was used."
            diagnostics["source"] = "heuristic_fallback"
            diagnostics["failure"] = "non_json_model_output"
            diagnostics["raw_response_preview"] = raw[:500]
            diagnostics["raw_response_tail"] = raw[-240:]
            diagnostics["output_looks_truncated"] = bool(
                raw.lstrip().startswith("{") and not raw.rstrip().endswith("}")
            )
            logger.warning(
                "AI model output rejected inference_id=%s reason=non_json_model_output "
                "response_chars=%s finish_reason=%s prompt_tokens=%s completion_tokens=%s "
                "response_format=%s looks_truncated=%s preview=%r",
                diagnostics.get("inference_id"),
                diagnostics.get("response_chars"),
                diagnostics.get("finish_reason"),
                (diagnostics.get("usage") or {}).get("prompt_tokens"),
                (diagnostics.get("usage") or {}).get("completion_tokens"),
                diagnostics.get("response_format_mode"),
                diagnostics.get("output_looks_truncated"),
                raw[:240],
            )
            return None, "heuristic_fallback", warning, diagnostics
        except Exception as exc:
            diagnostics = dict(base_diagnostics)
            diagnostics["source"] = "heuristic_fallback"
            diagnostics["failure"] = "provider_exception"
            diagnostics["error"] = str(exc)
            logger.exception("Embedded llama.cpp provider failed")
            return None, "heuristic_fallback", f"Embedded llama.cpp provider failed: {exc}", diagnostics

    diagnostics = dict(base_diagnostics)
    diagnostics["source"] = "heuristic_fallback"
    diagnostics["failure"] = "unsupported_provider"
    return (
        None,
        "heuristic_fallback",
        f"Unsupported AI_PROVIDER={cfg.provider}; deterministic fallback was used.",
        diagnostics,
    )

def call_llm_json(
    messages: List[Dict[str, str]],
) -> Tuple[Optional[Dict[str, Any]], str, Optional[str], Dict[str, Any]]:
    """Return the normalized AutomationHub Failure Agent response."""
    parsed, provider_used, warning, diagnostics = call_llm_json_schema(
        messages,
        response_schema=_AGENT_RESPONSE_SCHEMA,
    )
    if parsed:
        return normalize_agent_payload(parsed), provider_used, warning, diagnostics
    return None, provider_used, warning, diagnostics


def run_provider_probe() -> Dict[str, Any]:
    """Invoke the configured embedded model with a nonce and verify its exact JSON reply."""
    cfg = get_provider_config()
    status = provider_status()
    if cfg.provider != "embedded_llama_cpp":
        return {
            "ok": False,
            "verified": False,
            "provider": cfg.provider,
            "error": "The model probe requires AI_PROVIDER=embedded_llama_cpp.",
            "model": status,
        }

    probe_token = f"automationhub-probe-{uuid.uuid4().hex[:16]}"
    messages = [
        {
            "role": "system",
            "content": (
                "You are a runtime verification probe. Return strict JSON only. "
                "Copy the supplied probe_token exactly and set status to ok."
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "instruction": "Return the exact probe_token without changing it.",
                    "probe_token": probe_token,
                    "required_response": {"probe_token": probe_token, "status": "ok"},
                },
                ensure_ascii=False,
            ),
        },
    ]

    probe_schema = {
        "type": "object",
        "properties": {
            "probe_token": {"type": "string"},
            "status": {"type": "string", "enum": ["ok"]},
        },
        "required": ["probe_token", "status"],
    }

    try:
        parsed, raw, diagnostics = embedded_llama_cpp_chat(
            messages,
            cfg,
            response_schema=probe_schema,
            max_tokens=120,
        )
    except Exception as exc:
        return {
            "ok": False,
            "verified": False,
            "provider": "embedded_llama_cpp",
            "error": str(exc),
            "model": provider_status(),
        }

    returned_token = str((parsed or {}).get("probe_token") or "")
    returned_status = str((parsed or {}).get("status") or "").strip().lower()
    verified = bool(parsed and returned_token == probe_token and returned_status == "ok")
    return {
        "ok": verified,
        "verified": verified,
        "provider": "embedded_llama_cpp",
        "expected_probe_token": probe_token,
        "returned_probe_token": returned_token or None,
        "returned_status": returned_status or None,
        "raw_response": raw[:1000],
        "inference": diagnostics,
        "model": provider_status(),
        "error": None if verified else "The model responded, but it did not return the exact verification payload.",
    }
