from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from fastapi import FastAPI, Query, status
from pydantic import BaseModel, Field
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.routing import APIRouter
from fastapi.staticfiles import StaticFiles

import hashlib
import json
import os

from src.utils.az import (
    blob_client,
    public_blob_url,
    require,
    list_recent_run_json_blobs,
    download_json,
)
from src.utils.vars import BASE_DIR, REPORTS_CONTAINER
from src.ai_summary import get_report_context, get_or_create_report_summary, list_report_failures
from src.ai_agent import get_or_create_test_agent_analysis, save_test_agent_feedback
from src.ai_provider import provider_status, run_provider_probe
from src.failure_chat import ask_failure_chat
from src.test_statistics import aggregate_test_statistics

# -------------------- Paths --------------------
STATIC_DIR = os.path.join(BASE_DIR, "static")
STATIC_ASSET_VERSION_TOKEN = "__STATIC_ASSET_VERSION__"


def _compute_static_asset_version() -> str:
    """Return a content hash used to invalidate browser/CDN static-asset caches."""
    configured = os.getenv("AUTOMATIONHUB_STATIC_ASSET_VERSION", "").strip()
    if configured:
        return configured

    digest = hashlib.sha256()
    for name in sorted(os.listdir(STATIC_DIR)):
        if not name.endswith((".css", ".js")):
            continue
        path = os.path.join(STATIC_DIR, name)
        if not os.path.isfile(path):
            continue
        digest.update(name.encode("utf-8"))
        with open(path, "rb") as asset_file:
            digest.update(asset_file.read())
    return digest.hexdigest()[:12]


STATIC_ASSET_VERSION = _compute_static_asset_version()


def _static_html_response(file_name: str) -> HTMLResponse:
    path = os.path.join(STATIC_DIR, file_name)
    with open(path, "r", encoding="utf-8") as html_file:
        content = html_file.read().replace(
            STATIC_ASSET_VERSION_TOKEN,
            STATIC_ASSET_VERSION,
        )

    return HTMLResponse(
        content=content,
        headers={
            "Cache-Control": "no-store, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )

# -------------------- Known filter values --------------------
# These values match the blob path layout:
#   [<optional_prefix>/]<suite>/<env>/<platform>/<run_id>/run.json
#
# An unfiltered ("no dimension selected") request is expanded server-side into
# these narrow prefixes. This avoids one broad Azure listing that can be cut
# off by max_blobs and return only one prefix group.
#
# NOTE: "all" is a real suite name written by the runner (a combined run that
# covers every suite: runs/all/<env>/<platform>/...), not just a placeholder
# for "no suite filter". It is listed here like any other suite so it's
# included both when no suite filter is chosen and when it's chosen explicitly.
KNOWN_SUITES = ("smoke", "regression", "bugs", "sanity", "all")
KNOWN_ENVS = ("qa", "stage", "prod")
KNOWN_PLATFORMS = ("web", "mobile", "whitelabel")


# -------------------- Filter helpers --------------------
def _normalize_filter_value(kind: str, value: Optional[str]) -> Optional[str]:
    """
    Normalize request values and blob path values for reliable comparison.

    Returns None for an empty/missing value, meaning "do not filter this
    dimension" (i.e. no chip selected). An explicit value of "all" is treated
    as a literal filter value rather than a "clear filter" synonym, since
    "all" is now itself a valid suite name.
    """
    if value is None:
        return None

    v = str(value).strip().lower()
    if not v:
        return None

    if kind == "env":
        if v == "production":
            return "prod"
        if v == "staging":
            return "stage"

    if kind == "platform":
        if v in ("white-label", "white label", "white_label"):
            return "whitelabel"

    return v


def _expand_filter_value(
    kind: str,
    value: Optional[str],
    known_values: tuple[str, ...],
) -> List[str]:
    """
    Expand a request filter for Azure listing.

    Specific value -> [value]
    None/empty/all  -> all known values for this dimension
    """
    normalized = _normalize_filter_value(kind, value)
    if normalized:
        return [normalized]
    return list(known_values)


def _candidate_filter_combinations(
    suite: Optional[str],
    env: Optional[str],
    platform: Optional[str],
) -> List[Dict[str, str]]:
    """
    Build narrow Azure-prefix combinations for the request.

    Example:
      suite=<empty>&env=prod&platform=web
      -> smoke/prod/web + regression/prod/web + bugs/prod/web + sanity/prod/web
         + all/prod/web

      suite=all&env=prod&platform=web
      -> all/prod/web only (an explicit "all" is a specific suite, not a wildcard)
    """
    suites = _expand_filter_value("suite", suite, KNOWN_SUITES)
    envs = _expand_filter_value("env", env, KNOWN_ENVS)
    platforms = _expand_filter_value("platform", platform, KNOWN_PLATFORMS)

    return [
        {"suite": s, "env": e, "platform": p}
        for s in suites
        for e in envs
        for p in platforms
    ]


def _extract_blob_parts(blob_name: str) -> Optional[Dict[str, str]]:
    """
    Extract suite/env/platform/run_id from the end of the blob path.

    Supports both:
      suite/env/platform/run_id/run.json
      prefix/.../suite/env/platform/run_id/run.json
    """
    parts = blob_name.split("/")
    if len(parts) < 5:
        return None

    suite, env, platform, run_id, file_name = parts[-5:]
    if file_name != "run.json":
        return None

    return {
        "suite": suite,
        "env": env,
        "platform": platform,
        "run_id": run_id,
    }


def _run_timestamp_epoch(run: "Run") -> float:
    """
    Return a sortable timestamp. Newer timestamps should sort before older ones.
    Falls back safely when timestamps are missing or malformed.
    """
    raw = run.finished_at or run.started_at or ""
    if not raw:
        return 0.0

    try:
        # Support common ISO strings ending with Z.
        normalized = raw.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:
        # ISO-like timestamps sort lexically in many cases, but epoch fallback is safest.
        return 0.0


# -------------------- API Models --------------------
@dataclass
class Run:
    run_id: str
    suite: str
    version: str
    build_number: str
    env: str
    platform: str
    status: str
    started_at: str
    finished_at: str
    report_url: str

    def to_json(self) -> Dict[str, Any]:
        return json.loads(json.dumps(asdict(self)))


# -------------------- FastAPI setup --------------------
app = FastAPI(title="AutomationHub")

app.add_middleware(GZipMiddleware, minimum_size=100, compresslevel=9)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_headers=["*"],
    allow_methods=["*"],
    allow_credentials=True,
)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", response_class=HTMLResponse)
def home() -> HTMLResponse:
    return _static_html_response("index.html")


@app.get("/report-viewer.html", response_class=HTMLResponse)
def report_viewer() -> HTMLResponse:
    return _static_html_response("report-viewer.html")


public_api = APIRouter(prefix="/api", tags=["PublicAPI"])


@public_api.get("/health")
def health() -> Dict[str, Any]:
    return {"ok": True}


@public_api.get("/ai/provider-status", response_class=JSONResponse)
def ai_provider_status() -> JSONResponse:
    """Return model file/load readiness without running inference."""
    return JSONResponse({"ok": True, "model": provider_status()}, status_code=status.HTTP_200_OK)


@public_api.post("/ai/provider-probe", response_class=JSONResponse)
def ai_provider_probe() -> JSONResponse:
    """Run a nonce-based inference probe that proves the GGUF produced a response."""
    payload = run_provider_probe()
    return JSONResponse(
        payload,
        status_code=status.HTTP_200_OK if payload.get("verified") else status.HTTP_503_SERVICE_UNAVAILABLE,
    )


class ReportSummaryRequest(BaseModel):
    suite: str
    env: str
    platform: str
    run_id: str
    refresh: bool = False
    test_id: Optional[str] = None
    test_blob: Optional[str] = None
    test_name: Optional[str] = None


class TestAgentAnalysisRequest(BaseModel):
    suite: str
    env: str
    platform: str
    run_id: str
    refresh: bool = False
    test_id: Optional[str] = None
    test_blob: Optional[str] = None
    test_name: Optional[str] = None


class TestAgentFeedbackRequest(BaseModel):
    suite: str
    env: str
    platform: str
    run_id: str
    test_id: Optional[str] = None
    test_blob: Optional[str] = None
    test_name: Optional[str] = None
    memory_id: Optional[str] = None
    feedback: Literal["helpful", "partially_correct", "incorrect"]
    actual_cause: Optional[str] = None
    actual_fix: Optional[str] = None
    notes: Optional[str] = None
    inference_id: Optional[str] = None
    evidence_hash: Optional[str] = None
    provider: Optional[str] = None


class FailureChatHistoryMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=1200)


class FailureChatRequest(BaseModel):
    suite: str
    env: str
    platform: str
    run_id: str
    question: str = Field(min_length=1, max_length=1500)
    conversation_id: Optional[str] = Field(default=None, max_length=80)
    test_id: Optional[str] = None
    test_blob: Optional[str] = None
    test_name: Optional[str] = None
    history: List[FailureChatHistoryMessage] = Field(default_factory=list, max_length=8)


class TestStatisticsRunRequest(BaseModel):
    suite: str
    env: str
    platform: str
    run_id: str


class TestStatisticsRequest(BaseModel):
    runs: List[TestStatisticsRunRequest] = Field(min_length=1, max_length=200)
    max_runs: int = Field(default=100, ge=1, le=200)
    max_test_cases: int = Field(default=5000, ge=1, le=20000)


@public_api.get("/runs", response_class=JSONResponse)
def list_runs(
    env: Optional[str] = Query(None),
    platform: Optional[str] = Query(None),
    suite: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    limit: int = Query(250, ge=1, le=500),

    # Fast listing controls.
    since_hours: int = Query(168, ge=1, le=168),
    max_blobs: int = Query(500, ge=1, le=2000),
    scan_pages: int = Query(20, ge=1, le=50),
    page_size: int = Query(1000, ge=50, le=5000),

    # Kept for UI compatibility. Fast mode does not paginate.
    cursor: Optional[str] = Query(None),
    refresh: int = Query(0),
) -> JSONResponse:
    """
    FAST listing (no cache, no locks):
      - supports suite/env/platform specific filters, including the literal
        "all" suite (a combined run covering every suite)
      - an omitted/empty dimension is expanded server-side into narrow Azure
        prefixes for every known value of that dimension
      - scans only recent blobs by last_modified
      - downloads/parses only bounded candidate run.json blobs
      - returns a single page: { items: Run[], next_cursor: null }

    Why server-side expansion exists:
      A single broad Azure listing can be cut off by max_blobs and return only one
      lexical/prefix group. Narrow prefix expansion avoids that while keeping the
      browser to one HTTP request per refresh.
    """
    require("REPORTS_CONTAINER", REPORTS_CONTAINER)

    bsc = blob_client()

    # Normalize once for post-list verification and comparisons.
    suite_filter = _normalize_filter_value("suite", suite)
    env_filter = _normalize_filter_value("env", env)
    platform_filter = _normalize_filter_value("platform", platform)

    # 1) Build narrow server-side Azure listing combinations.
    combinations = _candidate_filter_combinations(
        suite=suite,
        env=env,
        platform=platform,
    )

    # Keep work bounded while making "all" fair across combinations.
    combo_count = max(1, len(combinations))
    per_combo_max_items = max(1, max_blobs // combo_count)

    seen_blob_names: set[str] = set()
    candidate_blob_names: List[str] = []

    for combo in combinations:
        names = list_recent_run_json_blobs(
            bsc=bsc,
            suite=combo["suite"],
            env=combo["env"],
            platform=combo["platform"],
            since_hours=since_hours,
            max_items=per_combo_max_items,
            results_per_page=page_size,
            max_pages_to_scan=scan_pages,
        )

        for name in names:
            if name in seen_blob_names:
                continue
            seen_blob_names.add(name)
            candidate_blob_names.append(name)

    # 2) Build runs from run.json. Candidate count is bounded by max_blobs.
    results: List[Run] = []
    result_blob_names: List[str] = []

    for blob_name in candidate_blob_names:
        blob_parts = _extract_blob_parts(blob_name)
        if not blob_parts:
            continue

        _suite = blob_parts["suite"]
        _env = blob_parts["env"]
        _platform = blob_parts["platform"]
        _run_id = blob_parts["run_id"]

        # Post-list guard: only apply filters when a specific value was requested.
        if suite_filter and _normalize_filter_value("suite", _suite) != suite_filter:
            continue
        if env_filter and _normalize_filter_value("env", _env) != env_filter:
            continue
        if platform_filter and _normalize_filter_value("platform", _platform) != platform_filter:
            continue

        data = download_json(bsc=bsc, blob_name=blob_name)
        if not data:
            continue

        if q:
            qq = q.lower()
            if qq not in _run_id.lower() and qq not in json.dumps(data).lower():
                continue

        blob_dir = "/".join(blob_name.split("/")[:-1])
        report_url = public_blob_url(f"{blob_dir}/index.html")

        results.append(
            Run(
                run_id=data.get("run_id", _run_id),
                suite=data.get("suite", _suite),
                version=data.get("version", "unknown"),
                build_number=data.get("build_number", "unknown"),
                env=data.get("env", _env),
                platform=data.get("platform", _platform),
                status=data.get("status", "unknown"),
                started_at=data.get("started_at", ""),
                finished_at=data.get("finished_at", ""),
                report_url=report_url,
            )
        )
        result_blob_names.append(blob_name)

    # Sort running first, then newest first.
    combined = list(zip(results, result_blob_names))
    combined.sort(
        key=lambda rb: (
            0 if (rb[0].status or "").lower() == "running" else 1,
            -_run_timestamp_epoch(rb[0]),
            rb[0].run_id,
        )
    )

    items_out: List[Dict[str, Any]] = []
    for r, bn in combined[:limit]:
        d = r.to_json()
        d["_blob_name"] = bn
        items_out.append(d)

    payload = {
        "items": items_out,
        "next_cursor": None,
    }

    return JSONResponse(
        content=payload,
        status_code=status.HTTP_200_OK,
        media_type="application/json",
    )


@public_api.post("/test-statistics", response_class=JSONResponse)
def test_statistics(body: TestStatisticsRequest) -> JSONResponse:
    """Return bounded test-level Allure statistics for dashboard charts."""
    try:
        payload = aggregate_test_statistics(
            blob_client(),
            [run.model_dump() for run in body.runs],
            max_runs=body.max_runs,
            max_test_cases=body.max_test_cases,
        )
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    except Exception as exc:
        return JSONResponse(
            {"ok": False, "error": f"Failed loading test statistics: {exc}"},
            status_code=500,
        )

    return JSONResponse(payload, status_code=status.HTTP_200_OK)


@public_api.get("/report-context", response_class=JSONResponse)
def report_context(
    suite: str = Query(...),
    env: str = Query(...),
    platform: str = Query(...),
    run_id: str = Query(...),
) -> JSONResponse:
    """
    Return the AutomationHub-owned report viewer context for one run.

    The iframe uses the public Blob-hosted Allure report URL, while the AI agent
    uses the same suite/env/platform/run_id to read Blob artifacts server-side.
    """
    try:
        context = get_report_context(
            blob_client(),
            suite=suite,
            env=env,
            platform=platform,
            run_id=run_id,
        )
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    except Exception as exc:
        return JSONResponse({"ok": False, "error": f"Failed loading report context: {exc}"}, status_code=500)

    return JSONResponse({"ok": True, "report": context}, status_code=status.HTTP_200_OK)


@public_api.get("/report-tests", response_class=JSONResponse)
def report_tests(
    suite: str = Query(...),
    env: str = Query(...),
    platform: str = Query(...),
    run_id: str = Query(...),
) -> JSONResponse:
    """
    Return failed/broken/error Allure test cases for one run.

    The report viewer uses this list as the source of truth for the selected
    test. AutomationHub cannot safely inspect the user's click inside the
    cross-origin Blob iframe, so the AI panel owns test selection.
    """
    try:
        payload = list_report_failures(
            blob_client(),
            suite=suite,
            env=env,
            platform=platform,
            run_id=run_id,
        )
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    except Exception as exc:
        return JSONResponse({"ok": False, "error": f"Failed loading report tests: {exc}"}, status_code=500)

    return JSONResponse(payload, status_code=status.HTTP_200_OK)


@public_api.post("/ai/report-summary", response_class=JSONResponse)
def ai_report_summary(body: ReportSummaryRequest) -> JSONResponse:
    """
    Generate or return a cached AI failure summary for one run.

    The AI never scrapes the iframe. The backend reads run.json and Allure JSON
    artifacts from Blob Storage, extracts failure evidence, and then summarizes it.
    """
    try:
        payload = get_or_create_report_summary(
            blob_client(),
            suite=body.suite,
            env=body.env,
            platform=body.platform,
            run_id=body.run_id,
            refresh=body.refresh,
            selected_test_id=body.test_id,
            selected_test_blob=body.test_blob,
            selected_test_name=body.test_name,
        )
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    except Exception as exc:
        return JSONResponse({"ok": False, "error": f"Failed generating AI summary: {exc}"}, status_code=500)

    return JSONResponse(payload, status_code=status.HTTP_200_OK)


@public_api.post("/ai/test-agent-analysis", response_class=JSONResponse)
def ai_test_agent_analysis(body: TestAgentAnalysisRequest) -> JSONResponse:
    """
    Generate or return a cached AI Failure Agent analysis for one selected test.

    This is the embedded-LLM path. The backend extracts the selected Allure test
    evidence, searches historical failure memory, lazy-loads the bundled GGUF
    model only when needed, and falls back to deterministic heuristics if the
    model is missing/unavailable.
    """
    try:
        payload = get_or_create_test_agent_analysis(
            blob_client(),
            suite=body.suite,
            env=body.env,
            platform=body.platform,
            run_id=body.run_id,
            refresh=body.refresh,
            selected_test_id=body.test_id,
            selected_test_blob=body.test_blob,
            selected_test_name=body.test_name,
        )
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    except Exception as exc:
        return JSONResponse({"ok": False, "error": f"Failed generating AI agent analysis: {exc}"}, status_code=500)

    return JSONResponse(payload, status_code=status.HTTP_200_OK)


@public_api.post("/ai/failure-chat", response_class=JSONResponse)
def ai_failure_chat(body: FailureChatRequest) -> JSONResponse:
    """Answer a grounded question about one selected failed Allure test."""
    try:
        payload = ask_failure_chat(
            blob_client(),
            suite=body.suite,
            env=body.env,
            platform=body.platform,
            run_id=body.run_id,
            question=body.question,
            selected_test_id=body.test_id,
            selected_test_blob=body.test_blob,
            selected_test_name=body.test_name,
            conversation_id=body.conversation_id,
            history=[item.model_dump() for item in body.history],
        )
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    except Exception as exc:
        return JSONResponse({"ok": False, "error": f"Failed answering AI chat question: {exc}"}, status_code=500)

    return JSONResponse(payload, status_code=status.HTTP_200_OK)


@public_api.post("/ai/test-agent-feedback", response_class=JSONResponse)
def ai_test_agent_feedback(body: TestAgentFeedbackRequest) -> JSONResponse:
    """Persist human feedback / actual fix for the selected test AI analysis."""
    try:
        payload = save_test_agent_feedback(blob_client(), body.model_dump())
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    except Exception as exc:
        return JSONResponse({"ok": False, "error": f"Failed saving AI feedback: {exc}"}, status_code=500)

    return JSONResponse(payload, status_code=status.HTTP_200_OK)


app.include_router(public_api)
