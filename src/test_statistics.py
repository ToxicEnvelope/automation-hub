from __future__ import annotations

import json
from collections import Counter, defaultdict
from typing import Any, Dict, Iterable, List, Optional, Sequence

from src.utils.az import build_blob_dir, require
from src.utils.vars import REPORTS_CONTAINER

PASS_STATUSES = {"passed", "pass", "success", "successful"}
FAIL_STATUSES = {"failed", "fail", "failure"}
ERROR_STATUSES = {"broken", "error", "errored", "unknown"}
SKIP_STATUSES = {"skipped", "skip", "pending", "disabled"}
SUITE_INDEX_CANDIDATES = ("widgets/suites.json", "data/suites.json")


def _container_client(bsc: Any) -> Any:
    require("REPORTS_CONTAINER", REPORTS_CONTAINER)
    return bsc.get_container_client(REPORTS_CONTAINER)


def _download_json(bsc: Any, blob_name: str) -> Optional[Any]:
    try:
        raw = _container_client(bsc).download_blob(blob_name).readall()
        return json.loads(raw)
    except Exception:
        return None


def _safe_segment(value: Any, field: str) -> str:
    segment = str(value or "").strip()
    if not segment or "/" in segment or "\\" in segment or segment in {".", ".."}:
        raise ValueError(f"Invalid {field}")
    return segment


def _normalize_status(value: Any) -> str:
    status = str(value or "unknown").strip().lower()
    if status in PASS_STATUSES:
        return "passed"
    if status in FAIL_STATUSES:
        return "failed"
    if status in SKIP_STATUSES:
        return "skipped"
    if status in ERROR_STATUSES:
        return "error"
    return "error"


def _node_children(node: Dict[str, Any]) -> List[Any]:
    children: List[Any] = []
    for key in ("children", "testCases", "tests", "items"):
        value = node.get(key)
        if isinstance(value, list):
            children.extend(value)
    return children


def _iter_test_nodes(value: Any) -> Iterable[Dict[str, Any]]:
    if isinstance(value, list):
        for item in value:
            yield from _iter_test_nodes(item)
        return

    if not isinstance(value, dict):
        return

    children = _node_children(value)
    status = value.get("status")
    name = value.get("fullName") or value.get("full_name") or value.get("name")

    # Allure suite/group nodes can expose an aggregate status. Count only leaves.
    if status is not None and name and not children:
        yield value
        return

    for child in children:
        yield from _iter_test_nodes(child)


def _test_identity(node: Dict[str, Any]) -> str:
    return str(
        node.get("uid")
        or node.get("uuid")
        or node.get("historyId")
        or node.get("history_id")
        or ""
    ).strip()


def _test_name(node: Dict[str, Any]) -> str:
    return str(
        node.get("fullName")
        or node.get("full_name")
        or node.get("name")
        or "Unknown test"
    ).strip()


def _read_suite_index(bsc: Any, blob_dir: str) -> tuple[List[Dict[str, Any]], Optional[str]]:
    for relative_name in SUITE_INDEX_CANDIDATES:
        blob_name = f"{blob_dir}/{relative_name}"
        payload = _download_json(bsc, blob_name)
        if payload is None:
            continue
        nodes = list(_iter_test_nodes(payload))
        if nodes:
            return nodes, relative_name
    return [], None


def _read_test_case_fallback(
    bsc: Any,
    blob_dir: str,
    *,
    limit: int,
) -> List[Dict[str, Any]]:
    if limit <= 0:
        return []

    prefix = f"{blob_dir}/data/test-cases/"
    client = _container_client(bsc)
    nodes: List[Dict[str, Any]] = []

    for blob in client.list_blobs(name_starts_with=prefix):
        name = getattr(blob, "name", "")
        if not name.endswith(".json"):
            continue
        payload = _download_json(bsc, name)
        if isinstance(payload, dict) and payload.get("status") is not None:
            nodes.append(payload)
        if len(nodes) >= limit:
            break

    return nodes


def aggregate_test_statistics(
    bsc: Any,
    runs: Sequence[Dict[str, Any]],
    *,
    max_runs: int = 100,
    max_test_cases: int = 5000,
) -> Dict[str, Any]:
    """
    Aggregate actual Allure test outcomes for dashboard charts.

    The function prefers one suite-index JSON per run. It falls back to bounded
    individual test-case reads only when the suite index is absent. This keeps
    dashboard refreshes predictable and avoids an unbounded Blob scan.
    """
    max_runs = max(1, min(int(max_runs), 200))
    max_test_cases = max(1, min(int(max_test_cases), 20_000))

    status_counts: Counter[str] = Counter()
    by_name: Dict[str, Counter[str]] = defaultdict(Counter)
    source_counts: Counter[str] = Counter()
    warnings: List[str] = []

    runs_requested = len(runs)
    runs_scanned = 0
    runs_with_test_data = 0
    test_cases_scanned = 0
    truncated = runs_requested > max_runs

    for run in list(runs)[:max_runs]:
        if test_cases_scanned >= max_test_cases:
            truncated = True
            break

        try:
            suite = _safe_segment(run.get("suite"), "suite")
            env = _safe_segment(run.get("env"), "env")
            platform = _safe_segment(run.get("platform"), "platform")
            run_id = _safe_segment(run.get("run_id"), "run_id")
        except ValueError as exc:
            warnings.append(str(exc))
            continue

        runs_scanned += 1
        blob_dir = build_blob_dir(suite=suite, env=env, platform=platform, run_id=run_id)
        nodes, source = _read_suite_index(bsc, blob_dir)

        if not nodes:
            remaining = max_test_cases - test_cases_scanned
            nodes = _read_test_case_fallback(bsc, blob_dir, limit=min(remaining, 1000))
            source = "data/test-cases" if nodes else None

        if not nodes:
            continue

        runs_with_test_data += 1
        source_counts[source or "unknown"] += 1
        seen_in_run: set[str] = set()

        for node in nodes:
            if test_cases_scanned >= max_test_cases:
                truncated = True
                break

            name = _test_name(node)
            status = _normalize_status(node.get("status"))
            identity = _test_identity(node) or f"{name}|{status}"
            if identity in seen_in_run:
                continue
            seen_in_run.add(identity)

            status_counts[status] += 1
            by_name[name][status] += 1
            by_name[name]["total"] += 1
            test_cases_scanned += 1

    test_outcomes_by_name: List[Dict[str, Any]] = []
    top_failed_tests: List[Dict[str, Any]] = []
    for name, counts in by_name.items():
        passed = int(counts.get("passed", 0))
        failed = int(counts.get("failed", 0))
        error = int(counts.get("error", 0))
        skipped = int(counts.get("skipped", 0))
        total = int(counts.get("total", 0))
        failure_count = failed + error
        outcome = {
            "test_name": name,
            "passed": passed,
            "failed": failed,
            "error": error,
            "skipped": skipped,
            "failure_count": failure_count,
            "total": total,
        }
        test_outcomes_by_name.append(outcome)
        if failure_count > 0:
            top_failed_tests.append(outcome)

    test_outcomes_by_name.sort(
        key=lambda item: (
            -item["total"],
            -item["failure_count"],
            item["test_name"].lower(),
        )
    )

    top_failed_tests.sort(
        key=lambda item: (
            -item["failure_count"],
            -item["failed"],
            -item["error"],
            item["test_name"].lower(),
        )
    )

    return {
        "ok": True,
        "status_counts": {
            "passed": int(status_counts.get("passed", 0)),
            "failed": int(status_counts.get("failed", 0)),
            "error": int(status_counts.get("error", 0)),
            "skipped": int(status_counts.get("skipped", 0)),
        },
        "test_outcomes_by_name": test_outcomes_by_name[:10],
        "top_failed_tests": top_failed_tests[:5],
        "meta": {
            "runs_requested": runs_requested,
            "runs_scanned": runs_scanned,
            "runs_with_test_data": runs_with_test_data,
            "test_cases_scanned": test_cases_scanned,
            "max_runs": max_runs,
            "max_test_cases": max_test_cases,
            "truncated": truncated,
            "sources": dict(source_counts),
            "warnings": warnings[:10],
        },
    }
