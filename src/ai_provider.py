from __future__ import annotations

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
_INFERENCE_LOCK = threading.Lock()
_EMBEDDED_MODEL: Any = None
_EMBEDDED_MODEL_PATH: Optional[str] = None
_MODEL_LOADED_AT: Optional[float] = None


def _default_model_path() -> str:
    # BASE_DIR points to <project>/src. The bundled model is stored at
    # <project>/models in both local development and the Runner image.
    return os.path.abspath(os.path.join(BASE_DIR, "..", "models", "automationhub-agent.gguf"))


def get_provider_config() -> AIProviderConfig:
    provider = (os.getenv("AI_PROVIDER") or os.getenv("AI_SUMMARY_PROVIDER") or "embedded_llama_cpp").strip().lower()
    return AIProviderConfig(
        provider=provider,
        model_path=os.getenv("AI_MODEL_PATH", _default_model_path()).strip(),
        model_name=os.getenv("AI_MODEL_NAME", "automationhub-agent.gguf").strip(),
        context_tokens=int(os.getenv("AI_MODEL_CONTEXT_TOKENS", "4096")),
        threads=int(os.getenv("AI_MODEL_THREADS", str(max(1, min(4, os.cpu_count() or 2))))),
        max_tokens=int(os.getenv("AI_MODEL_MAX_TOKENS", "900")),
        temperature=float(os.getenv("AI_MODEL_TEMPERATURE", "0.0")),
        timeout_seconds=int(os.getenv("AI_MODEL_TIMEOUT_SECONDS", "180")),
        chat_format=os.getenv("AI_MODEL_CHAT_FORMAT", "").strip(),
        require_model_response=_env_bool("AI_REQUIRE_MODEL_RESPONSE", False),
    )


def _model_file_status(path: str) -> Dict[str, Any]:
    exists = bool(path and os.path.isfile(path))
    readable = bool(exists and os.access(path, os.R_OK))
    size_bytes = 0
    modified_ns: Optional[int] = None
    if exists:
        try:
            stat = os.stat(path)
            size_bytes = int(stat.st_size)
            modified_ns = int(stat.st_mtime_ns)
        except OSError:
            pass

    configured_sha256 = os.getenv("AI_MODEL_SHA256", "").strip().lower()
    fingerprint = configured_sha256 or (f"size:{size_bytes}:mtime_ns:{modified_ns}" if exists else "missing")
    return {
        "model_exists": exists,
        "model_readable": readable,
        "model_size_bytes": size_bytes,
        "model_nonempty": size_bytes > 0,
        "configured_sha256": configured_sha256 or None,
        "model_fingerprint": fingerprint,
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
    }


def _load_embedded_llama_cpp_model(cfg: AIProviderConfig) -> Any:
    global _EMBEDDED_MODEL, _EMBEDDED_MODEL_PATH, _MODEL_LOADED_AT

    if not cfg.model_path:
        raise RuntimeError("AI_MODEL_PATH is empty")
    if not os.path.isfile(cfg.model_path):
        raise RuntimeError(
            f"Bundled GGUF model was not found at {cfg.model_path}. "
            "Place automationhub-agent.gguf under the project models/ directory before building the runner image."
        )
    if not os.access(cfg.model_path, os.R_OK):
        raise RuntimeError(f"Bundled GGUF model is not readable: {cfg.model_path}")
    if os.path.getsize(cfg.model_path) <= 0:
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
    with _INFERENCE_LOCK:
        try:
            result = llm.create_chat_completion(**completion_kwargs)
        except (TypeError, ValueError) as exc:
            error_text = str(exc).lower()
            if "response_format" not in error_text and "schema" not in error_text:
                raise

            # Retain JSON grammar when an older binding rejects JSON Schema.
            response_format_mode = "json_object"
            completion_kwargs["response_format"] = {"type": "json_object"}
            try:
                result = llm.create_chat_completion(**completion_kwargs)
            except (TypeError, ValueError) as json_exc:
                if "response_format" not in str(json_exc).lower():
                    raise
                response_format_mode = "none"
                completion_kwargs.pop("response_format", None)
                result = llm.create_chat_completion(**completion_kwargs)

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
