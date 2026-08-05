from __future__ import annotations

from src.failure_memory import (
    get_failure_memory,
    search_similar_failures,
    store_failure_memory,
    store_feedback,
)
from tests.fakes import FakeBlobServiceClient


def evidence(run_id: str) -> dict:
    failure = {
        "test_id": "test-search-hotel",
        "name": "test_search_hotel",
        "full_name": "tests.test_search#test_search_hotel",
        "status": "failed",
        "message": "Hotel autocomplete did not select the Hebrew hotel name",
        "labels": {"feature": "Reservation"},
    }
    return {
        "run": {
            "run_id": run_id,
            "suite": "smoke",
            "env": "prod",
            "platform": "web",
            "status": "failed",
        },
        "selected_test": failure,
        "failures": [failure],
    }


def agent(cause: str = "The hotel search failed") -> dict:
    return {
        "failure_cause": {
            "summary": cause,
            "category": "test_automation",
            "confidence": "medium",
        },
        "suggested_fix": {"summary": "Retry the hotel search"},
    }


def test_feedback_corrects_memory_and_survives_regeneration() -> None:
    bsc = FakeBlobServiceClient()
    memory = store_failure_memory(bsc, evidence("run-1"), agent(), provider="embedded_llama_cpp")
    assert memory and memory["memory_status"] == "unreviewed"

    result = store_feedback(bsc, {
        "memory_id": memory["memory_id"],
        "run_id": "run-1",
        "test_id": "test-search-hotel",
        "test_name": "tests.test_search#test_search_hotel",
        "feedback": "partially_correct",
        "actual_cause": "Autocomplete did not match the Hebrew hotel name",
        "actual_fix": "Select the result using the internal hotel ID",
        "notes": "Confirmed from the Playwright trace",
    })
    assert result["memory_status"] == "corrected"
    assert result["retrieval_enabled"] is True

    # Regeneration updates the AI result without erasing the human review.
    store_failure_memory(
        bsc,
        evidence("run-1"),
        agent("A newly generated but less precise model diagnosis"),
        provider="embedded_llama_cpp",
    )
    saved = get_failure_memory(bsc, memory_id=memory["memory_id"])
    assert saved is not None
    assert saved["memory_status"] == "corrected"
    assert saved["human_feedback"]["verdict"] == "partially_correct"
    assert saved["effective_failure_cause"] == "Autocomplete did not match the Hebrew hotel name"

    matches = search_similar_failures(bsc, evidence("run-2"))
    assert matches
    assert matches[0]["memory_status"] == "corrected"
    assert matches[0]["failure_cause"] == "Autocomplete did not match the Hebrew hotel name"
    assert matches[0]["suggested_fix"] == "Select the result using the internal hotel ID"


def test_rejected_memory_is_excluded_from_retrieval() -> None:
    bsc = FakeBlobServiceClient()
    memory = store_failure_memory(bsc, evidence("run-1"), agent(), provider="embedded_llama_cpp")
    assert memory

    result = store_feedback(bsc, {
        "memory_id": memory["memory_id"],
        "run_id": "run-1",
        "test_id": "test-search-hotel",
        "feedback": "incorrect",
    })
    assert result["memory_status"] == "rejected"
    assert result["retrieval_enabled"] is False
    assert search_similar_failures(bsc, evidence("run-2")) == []
