from __future__ import annotations

from typing import Any, Dict, List, Optional
from fastapi import FastAPI, Query, status
from fastapi.routing import APIRouter
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from dataclasses import dataclass, asdict
import json
import os
import time
import threading
from pathlib import Path

from src.utils.az import (
    blob_client,
    public_blob_url,
    require,
    list_run_json_blobs_page,
    download_json,
    decode_cursor,
    encode_cursor,
)
from src.utils.vars import (
    BASE_DIR, REPORTS_CONTAINER, RUNS_CACHE_MAX_ENTRIES,
    RUNS_CACHE_PERSIST_DEBOUNCE_SECONDS,RUNS_CACHE_FILE,
    RUNS_CACHE_TTL_SECONDS, RUNS_INDEX_FALLBACK_TO_LISTING,
    RUNS_INDEX_PATH, RUNS_INDEX_TTL_SECONDS
)
# -------------------- Paths --------------------
STATIC_DIR = os.path.join(BASE_DIR, "static")

_cache_lock = threading.Lock()
_last_persist_ts = 0.0

# key -> {"expires_at": float, "last_access": float, "value": dict}
_runs_cache: Dict[str, Dict[str, Any]] = {}

# in-memory index: {"saved_at": float, "blob_names": set[str]}
_runs_index_saved_at: float = 0.0
_runs_index_blob_names: set[str] = set()


def _cache_key(params: Dict[str, Any]) -> str:
    return json.dumps(params, sort_keys=True, separators=(",", ":"))


def _ensure_cache_dir() -> None:
    Path(RUNS_CACHE_FILE).parent.mkdir(parents=True, exist_ok=True)


def _load_cache_from_disk_locked() -> None:
    """
    Loads cache file into memory.
    Expects _cache_lock already held.
    """
    global _runs_cache
    try:
        p = Path(RUNS_CACHE_FILE)
        if not p.exists():
            _runs_cache = {}
            return

        raw = p.read_text(encoding="utf-8").strip()
        if not raw:
            _runs_cache = {}
            return

        data = json.loads(raw)
        entries = data.get("entries", {})
        now = time.time()

        loaded: Dict[str, Dict[str, Any]] = {}
        for k, v in entries.items():
            expires_at = float(v.get("expires_at", 0))
            if expires_at > now and "value" in v:
                loaded[k] = {
                    "expires_at": expires_at,
                    "last_access": float(v.get("last_access", now)),
                    "value": v["value"],
                }

        _runs_cache = loaded
    except Exception:
        # If cache is corrupted, ignore it (do not crash server)
        _runs_cache = {}


def _trim_cache_locked() -> None:
    """
    Drops expired and enforces max entries via LRU eviction.
    Expects _cache_lock already held.
    """
    now = time.time()

    # Drop expired
    for k, v in list(_runs_cache.items()):
        if float(v.get("expires_at", 0)) <= now:
            _runs_cache.pop(k, None)

    # LRU eviction
    if len(_runs_cache) > RUNS_CACHE_MAX_ENTRIES:
        lru = sorted(_runs_cache.items(), key=lambda kv: float(kv[1].get("last_access", 0)))
        to_drop = len(_runs_cache) - RUNS_CACHE_MAX_ENTRIES
        for i in range(to_drop):
            _runs_cache.pop(lru[i][0], None)


def _persist_cache_to_disk_locked(force: bool = False) -> None:
    """
    Persists cache to disk (debounced).
    Expects _cache_lock already held.
    """
    global _last_persist_ts
    now = time.time()

    if not force and (now - _last_persist_ts) < RUNS_CACHE_PERSIST_DEBOUNCE_SECONDS:
        return

    _ensure_cache_dir()
    _trim_cache_locked()

    tmp_path = Path(RUNS_CACHE_FILE + ".tmp")
    final_path = Path(RUNS_CACHE_FILE)

    payload = {
        "ttl_seconds": RUNS_CACHE_TTL_SECONDS,
        "saved_at": now,
        "entries": _runs_cache,
    }

    tmp_path.write_text(json.dumps(payload), encoding="utf-8")
    tmp_path.replace(final_path)

    _last_persist_ts = now


def cache_get(key: str) -> Optional[Dict[str, Any]]:
    with _cache_lock:
        item = _runs_cache.get(key)
        if not item:
            return None

        now = time.time()
        if now >= float(item.get("expires_at", 0)):
            _runs_cache.pop(key, None)
            return None

        item["last_access"] = now
        return item.get("value")


def cache_set(key: str, value: Dict[str, Any]) -> None:
    with _cache_lock:
        now = time.time()
        _runs_cache[key] = {
            "expires_at": now + RUNS_CACHE_TTL_SECONDS,
            "last_access": now,
            "value": value,
        }
        _persist_cache_to_disk_locked(force=False)


# -------------------- Runs index helpers --------------------
def _ensure_index_dir() -> None:
    Path(RUNS_INDEX_PATH).parent.mkdir(parents=True, exist_ok=True)


def _load_runs_index_from_disk_locked() -> None:
    """
    Loads runs index file into memory.
    Expects _cache_lock already held.
    """
    global _runs_index_saved_at, _runs_index_blob_names
    try:
        p = Path(RUNS_INDEX_PATH)
        if not p.exists():
            _runs_index_saved_at = 0.0
            _runs_index_blob_names = set()
            return

        raw = p.read_text(encoding="utf-8").strip()
        if not raw:
            _runs_index_saved_at = 0.0
            _runs_index_blob_names = set()
            return

        data = json.loads(raw)
        _runs_index_saved_at = float(data.get("saved_at", 0.0))
        blob_list = data.get("blob_names", []) or []
        _runs_index_blob_names = set([b for b in blob_list if isinstance(b, str)])
    except Exception:
        _runs_index_saved_at = 0.0
        _runs_index_blob_names = set()


def _persist_runs_index_to_disk_locked(force: bool = True) -> None:
    """
    Persists runs index to disk.
    Expects _cache_lock already held.
    """
    _ensure_index_dir()
    tmp_path = Path(RUNS_INDEX_PATH + ".tmp")
    final_path = Path(RUNS_INDEX_PATH)
    payload = {
        "saved_at": _runs_index_saved_at,
        "blob_names": sorted(_runs_index_blob_names),
    }
    tmp_path.write_text(json.dumps(payload), encoding="utf-8")
    tmp_path.replace(final_path)


def _refresh_runs_index_locked(bsc) -> None:
    """
    Rebuild index by listing ALL run.json blobs (unfiltered).
    Expects _cache_lock already held.
    """
    global _runs_index_saved_at, _runs_index_blob_names

    # Build a complete set of run.json blobs by paging through listing.
    blob_set: set[str] = set()
    token: Optional[str] = None

    while True:
        blob_names, next_token = list_run_json_blobs_page(
            bsc=bsc,
            suite=None,
            env=None,
            platform=None,
            continuation_token=token,
            results_per_page=5000,
        )

        if blob_names:
            for bn in blob_names:
                if isinstance(bn, str):
                    blob_set.add(bn)

        if not next_token:
            break
        token = next_token

    _runs_index_blob_names = blob_set
    _runs_index_saved_at = time.time()
    _persist_runs_index_to_disk_locked(force=True)


def _get_runs_index_set(bsc, force_refresh: bool) -> set[str]:
    """
    Returns current index set (may refresh if TTL expired or forced).
    """
    # ✅ WIRE: allow disabling the index mechanism cleanly
    if not RUNS_INDEX_FALLBACK_TO_LISTING:
        return set()

    with _cache_lock:
        now = time.time()
        is_fresh = (now - float(_runs_index_saved_at or 0.0)) < RUNS_INDEX_TTL_SECONDS
        if force_refresh or not is_fresh:
            # If we don't have anything loaded yet, try disk first (fast path)
            if not _runs_index_blob_names:
                _load_runs_index_from_disk_locked()
                now = time.time()
                is_fresh = (now - float(_runs_index_saved_at or 0.0)) < RUNS_INDEX_TTL_SECONDS
            if force_refresh or not is_fresh:
                _refresh_runs_index_locked(bsc)
        return set(_runs_index_blob_names)


# Load cache + index on import/startup
with _cache_lock:
    _load_cache_from_disk_locked()
    _load_runs_index_from_disk_locked()


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
def home() -> str:
    index_path = os.path.join(STATIC_DIR, "index.html")
    with open(index_path, "r", encoding="utf-8") as f:
        return f.read()


public_api = APIRouter(prefix="/api", tags=["PublicAPI"])


@public_api.get("/health")
def health() -> Dict[str, Any]:
    return {"ok": True}


@public_api.get("/runs", response_class=JSONResponse)
def list_runs(
    env: Optional[str] = Query(None),
    platform: Optional[str] = Query(None),
    suite: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    cursor: Optional[str] = Query(None),
    refresh: int = Query(0),  # refresh=1 bypasses cache (and refreshes runs index)
) -> JSONResponse:
    """
    Paginated listing using Azure continuation token.

    Returns:
      { items: Run[], next_cursor: str|null }

    Cache:
      File-backed TTL cache (RUNS_CACHE_TTL_SECONDS). Use refresh=1 to bypass.
    """
    require("REPORTS_CONTAINER", REPORTS_CONTAINER)

    # -------------------- cache read --------------------
    cache_params = {
        "env": env,
        "platform": platform,
        "suite": suite,
        "q": q,
        "limit": limit,
        "cursor": cursor,
    }
    key = _cache_key(cache_params)

    # We need bsc for index refresh/filter (minimal overhead; BlobServiceClient creation is cheap)
    bsc = blob_client()

    # Get the current set of existing run.json blobs (refresh if asked / TTL expired)
    existing_run_json_blobs = _get_runs_index_set(bsc=bsc, force_refresh=bool(refresh))

    if not refresh:
        cached = cache_get(key)
        if cached is not None:
            # Filter out stale items whose run.json blob no longer exists (purged/retention)
            # ✅ WIRE: only filter if index is enabled (set non-empty)
            if existing_run_json_blobs:
                try:
                    items = cached.get("items", []) or []
                    filtered_items = []
                    for it in items:
                        bn = None
                        if isinstance(it, dict):
                            bn = it.get("_blob_name")
                        # If we don't have blob marker (older cache entries), keep it
                        if not bn or bn in existing_run_json_blobs:
                            filtered_items.append(it)

                    # Keep the same cursor (best-effort); this is only UI cleanliness
                    out = {"items": filtered_items, "next_cursor": cached.get("next_cursor")}
                    return JSONResponse(
                        content=out,
                        status_code=status.HTTP_200_OK,
                        media_type="application/json",
                    )
                except Exception:
                    # If anything goes wrong, fall back to live fetch
                    pass
            else:
                return JSONResponse(
                    content=cached,
                    status_code=status.HTTP_200_OK,
                    media_type="application/json",
                )

    # -------------------- live fetch --------------------
    results: List[Run] = []
    result_blob_names: List[str] = []

    page_token, skip, cursor_page_size = decode_cursor(cursor)
    page_size = int(cursor_page_size or max(300, limit * 9))

    max_pages_per_request = 5
    pages_fetched = 0

    next_cursor: Optional[str] = None
    current_page_token = page_token

    while len(results) < limit and pages_fetched < max_pages_per_request:
        pages_fetched += 1

        blob_names, next_page_token = list_run_json_blobs_page(
            bsc=bsc,
            suite=suite,
            env=env,
            platform=platform,
            continuation_token=current_page_token,
            results_per_page=page_size,
        )

        if not blob_names:
            next_cursor = None
            break

        start_index = skip if pages_fetched == 1 else 0

        if start_index >= len(blob_names):
            if next_page_token:
                current_page_token = next_page_token
                skip = 0
                next_cursor = encode_cursor(token=current_page_token, skip=0, page_size=page_size)
                continue
            next_cursor = None
            break

        consumed_in_page = start_index

        for i in range(start_index, len(blob_names)):
            blob_name = blob_names[i]
            consumed_in_page = i + 1

            # Skip blobs that no longer exist per the latest index (rare, but helps)
            # ✅ WIRE: only enforce if index is enabled (set non-empty)
            if existing_run_json_blobs and blob_name not in existing_run_json_blobs:
                continue

            parts = blob_name.split("/")
            if len(parts) < 6:
                continue

            _prefix, _suite, _env, _platform, _run_id, _file = parts[-6:]

            # Keep filters (still useful if suite/env/platform is "all"/None)
            if suite and suite != "all" and _suite != suite:
                continue
            if env and _env != env:
                continue
            if platform and _platform != platform:
                continue

            data = download_json(bsc=bsc, blob_name=blob_name)
            if not data:
                continue

            if q:
                qq = q.lower()
                if qq not in _run_id.lower() and qq not in json.dumps(data).lower():
                    continue

            blob_dir = "/".join(parts[:-1])  # strip run.json
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

            if len(results) >= limit:
                next_cursor = encode_cursor(
                    token=current_page_token,
                    skip=consumed_in_page,
                    page_size=page_size,
                )
                break

        if len(results) >= limit:
            break

        if next_page_token:
            current_page_token = next_page_token
            skip = 0
            next_cursor = encode_cursor(token=current_page_token, skip=0, page_size=page_size)
        else:
            next_cursor = None
            break

    # Sort newest-first
    def _sort_key(r: Run) -> str:
        fa = getattr(r, "finished_at", "") or ""
        return fa if fa else getattr(r, "run_id", "")

    combined = list(zip(results, result_blob_names))
    combined.sort(key=lambda rb: _sort_key(rb[0]), reverse=True)

    items_out: List[Dict[str, Any]] = []
    for r, bn in combined:
        d = r.to_json()
        d["_blob_name"] = bn  # internal marker for stale filtering
        items_out.append(d)

    payload = {
        "items": items_out,
        "next_cursor": next_cursor,
    }

    # -------------------- cache write --------------------
    cache_set(key, payload)

    return JSONResponse(
        content=payload,
        status_code=status.HTTP_200_OK,
        media_type="application/json",
    )


app.include_router(public_api)
