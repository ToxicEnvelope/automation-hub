from __future__ import annotations

import json

from src.test_statistics import aggregate_test_statistics
from tests.fakes import FakeBlobServiceClient


def put_json(bsc: FakeBlobServiceClient, name: str, payload) -> None:
    bsc.container.blobs[name] = json.dumps(payload).encode("utf-8")


def test_aggregate_test_outcomes_and_top_failures() -> None:
    bsc = FakeBlobServiceClient()
    put_json(
        bsc,
        "runs/smoke/prod/web/run-1/widgets/suites.json",
        [{
            "name": "suite",
            "children": [
                {"uid": "a1", "name": "test_search_hotel", "status": "failed"},
                {"uid": "b1", "name": "test_login", "status": "passed"},
            ],
        }],
    )
    put_json(
        bsc,
        "runs/smoke/prod/web/run-2/widgets/suites.json",
        [{
            "name": "suite",
            "children": [
                {"uid": "a2", "name": "test_search_hotel", "status": "broken"},
                {"uid": "b2", "name": "test_login", "status": "passed"},
                {"uid": "c2", "name": "test_checkout", "status": "failed"},
            ],
        }],
    )

    payload = aggregate_test_statistics(
        bsc,
        [
            {"suite": "smoke", "env": "prod", "platform": "web", "run_id": "run-1"},
            {"suite": "smoke", "env": "prod", "platform": "web", "run_id": "run-2"},
        ],
    )

    assert payload["status_counts"] == {
        "passed": 2,
        "failed": 2,
        "error": 1,
        "skipped": 0,
    }
    assert payload["meta"]["test_cases_scanned"] == 5
    assert payload["test_outcomes_by_name"][0]["test_name"] in {"test_login", "test_search_hotel"}
    assert payload["top_failed_tests"][0] == {
        "test_name": "test_search_hotel",
        "failed": 1,
        "error": 1,
        "failure_count": 2,
        "passed": 0,
        "skipped": 0,
        "total": 2,
    }


def test_falls_back_to_individual_test_case_files() -> None:
    bsc = FakeBlobServiceClient()
    put_json(
        bsc,
        "runs/regression/qa/web/run-3/data/test-cases/one.json",
        {"uid": "one", "fullName": "tests.test_api#test_api", "status": "error"},
    )

    payload = aggregate_test_statistics(
        bsc,
        [{"suite": "regression", "env": "qa", "platform": "web", "run_id": "run-3"}],
    )

    assert payload["status_counts"]["error"] == 1
    assert payload["meta"]["sources"] == {"data/test-cases": 1}
