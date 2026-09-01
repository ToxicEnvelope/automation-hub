from __future__ import annotations

from src.app import (
    KNOWN_SUITES,
    _candidate_filter_combinations,
    _expand_filter_value,
    _normalize_filter_value,
)
from src.ai_summary import normalize_locator
from src.utils.az import compute_name_starts_with


def test_known_suites_includes_all() -> None:
    assert "all" in KNOWN_SUITES


def test_compute_name_starts_with_treats_all_as_a_literal_segment() -> None:
    # Regression guard: this used to strip "all" back to "", which turned a
    # narrow runs/all/prod/web/ prefix into a full-container scan.
    assert compute_name_starts_with(suite="all", env="prod", platform="web") == "runs/all/prod/web/"


def test_normalize_filter_value_no_longer_collapses_literal_all() -> None:
    assert _normalize_filter_value("suite", "all") == "all"
    assert _normalize_filter_value("suite", "") is None
    assert _normalize_filter_value("suite", None) is None
    assert _normalize_filter_value("suite", "  ALL  ") == "all"


def test_expand_filter_value_explicit_all_is_narrow_not_broad() -> None:
    # An explicit suite=all request should query only the "all" prefix ...
    assert _expand_filter_value("suite", "all", KNOWN_SUITES) == ["all"]
    # ... while an empty/omitted suite still expands to every known suite,
    # which now includes "all" alongside the original four.
    assert set(_expand_filter_value("suite", "", KNOWN_SUITES)) == set(KNOWN_SUITES)


def test_candidate_combinations_include_all_suite_when_unfiltered() -> None:
    combos = _candidate_filter_combinations(suite=None, env="prod", platform="web")
    assert {"suite": "all", "env": "prod", "platform": "web"} in combos
    assert len(combos) == len(KNOWN_SUITES)


def test_candidate_combinations_explicit_all_is_isolated() -> None:
    combos = _candidate_filter_combinations(suite="all", env="prod", platform="web")
    assert combos == [{"suite": "all", "env": "prod", "platform": "web"}]


def test_normalize_locator_accepts_the_all_suite() -> None:
    # Regression guard: this used to raise ValueError("Missing required suite"),
    # which broke report viewing / AI summary / AI chat for "all" suite runs.
    locator = normalize_locator(suite="all", env="prod", platform="web", run_id="run-1")
    assert locator["suite"] == "all"
    assert locator["env"] == "prod"
    assert locator["platform"] == "web"
    assert locator["run_id"] == "run-1"
