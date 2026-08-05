from __future__ import annotations

from src import failure_chat
from tests.fakes import FakeBlobServiceClient


def analysis_payload() -> dict:
    return {
        "ok": True,
        "cached": True,
        "provider": "embedded_llama_cpp",
        "evidence_hash": "abc123",
        "report": {
            "run_id": "run-1",
            "suite": "smoke",
            "env": "prod",
            "platform": "web",
            "version": "V135",
            "build_number": "latest",
        },
        "selected_test": {
            "test_id": "test-search-hotel",
            "name": "test_search_hotel",
            "full_name": "tests.test_search#test_search_hotel",
            "status": "failed",
        },
        "agent": {
            "failure_cause": {
                "category": "test_automation",
                "summary": "The hotel search step failed while selecting the Hebrew hotel name.",
                "confidence": "medium",
            },
            "evidence": [
                {"type": "failure_message", "value": "Failed: fail to search hotel אוריינט"},
            ],
            "suggested_fix": {
                "summary": "Inspect the autocomplete selection behavior.",
                "steps": ["Review the Playwright trace"],
            },
            "historical_insight": {
                "matches": [],
                "matched_previous_failures": 0,
            },
        },
        "evidence": {
            "failures": [{
                "message": "Failed: fail to search hotel אוריינט",
                "trace_excerpt": "locator click failed",
                "failed_steps": [{"name": "search hotel", "message": "hotel was not selected"}],
                "attachments": [],
            }],
        },
        "memory": {
            "memory_status": "unreviewed",
            "effective_failure_cause": "The hotel search step failed while selecting the Hebrew hotel name.",
            "effective_suggested_fix": "Inspect the autocomplete selection behavior.",
        },
        "memory_status": "unreviewed",
    }


def test_failure_chat_returns_grounded_model_response(monkeypatch) -> None:
    bsc = FakeBlobServiceClient()
    monkeypatch.setattr(failure_chat, "get_or_create_test_agent_analysis", lambda *args, **kwargs: analysis_payload())
    monkeypatch.setattr(
        failure_chat,
        "call_llm_json_schema",
        lambda *args, **kwargs: (
            {
                "answer": "The failure occurred during the hotel autocomplete selection step.",
                "confidence": "medium",
                "answer_type": "mixed",
                "follow_up_suggestions": ["What should I check first?"],
            },
            "embedded_llama_cpp",
            None,
            {
                "model_invoked": True,
                "response_received": True,
                "response_valid_json": True,
                "inference_id": "inf-1",
            },
        ),
    )
    monkeypatch.setattr(failure_chat, "provider_status", lambda: {"provider": "embedded_llama_cpp"})

    payload = failure_chat.ask_failure_chat(
        bsc,
        suite="smoke",
        env="prod",
        platform="web",
        run_id="run-1",
        selected_test_id="test-search-hotel",
        question="Why did this fail?",
    )

    assert payload["ok"] is True
    assert payload["actual_model_response"] is True
    assert payload["fallback_used"] is False
    assert payload["evidence"] == [
        {"type": "failure_message", "value": "Failed: fail to search hotel אוריינט"}
    ]
    assert "test_id=test-search-hotel" in payload["report_viewer_url"]


def test_failure_chat_uses_deterministic_fallback(monkeypatch) -> None:
    bsc = FakeBlobServiceClient()
    monkeypatch.setattr(failure_chat, "get_or_create_test_agent_analysis", lambda *args, **kwargs: analysis_payload())
    monkeypatch.setattr(
        failure_chat,
        "call_llm_json_schema",
        lambda *args, **kwargs: (
            None,
            "heuristic_fallback",
            "model unavailable",
            {
                "model_invoked": True,
                "response_received": False,
                "response_valid_json": False,
            },
        ),
    )
    monkeypatch.setattr(failure_chat, "provider_status", lambda: {"provider": "embedded_llama_cpp"})
    monkeypatch.setattr(
        failure_chat,
        "get_provider_config",
        lambda: type("Config", (), {"require_model_response": False})(),
    )

    payload = failure_chat.ask_failure_chat(
        bsc,
        suite="smoke",
        env="prod",
        platform="web",
        run_id="run-1",
        selected_test_id="test-search-hotel",
        question="What should I check first?",
        history=[{"role": "user", "content": "Why did this fail?"}],
    )

    assert payload["fallback_used"] is True
    assert payload["actual_model_response"] is False
    assert "Inspect the autocomplete" in payload["answer"]
    assert payload["answer_type"] == "mixed"


def test_failure_chat_requires_selected_test() -> None:
    bsc = FakeBlobServiceClient()
    try:
        failure_chat.ask_failure_chat(
            bsc,
            suite="smoke",
            env="prod",
            platform="web",
            run_id="run-1",
            question="Why?",
        )
    except ValueError as exc:
        assert "Select a failed test" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("Expected a selected-test validation error")
