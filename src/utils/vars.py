import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# -------------------- Blob config (ENV VARS) --------------------
# Use these in your AutomationHub container/app settings:
#   REPORTS_STORAGE_ACCOUNT=allureautotests     (optional; default "allureautotests")
#   REPORTS_CONTAINER=reports                   (optional; default "reports")
#   REPORTS_PREFIX=runs                         (optional; default "runs")
REPORTS_STORAGE_ACCOUNT = os.getenv("REPORTS_STORAGE_ACCOUNT", "allureautotests").strip()
REPORTS_CONTAINER = os.getenv("REPORTS_CONTAINER", "reports").strip()
REPORTS_PREFIX = os.getenv("REPORTS_PREFIX", "runs").strip().strip("/")  # "reports" or "runs" etc.

# -------------------- Runs Index (optional optimization) --------------------
# If enabled, the server can read a single "runs index" blob instead of listing all run.json blobs.
# This avoids stale local cache after retention purge and reduces Storage list operations.
#
# Suggested blob path:
#   <REPORTS_PREFIX>/_index/runs_index.json
#
# You can override via env var:
#   RUNS_INDEX_BLOB=runs/_index/runs_index.json
RUNS_INDEX_BLOB = os.getenv(
    "RUNS_INDEX_BLOB",
    f"{REPORTS_PREFIX}/_index/runs_index.json" if REPORTS_PREFIX else "_index/runs_index.json",
).strip().lstrip("/")

# -------------------- File-backed TTL cache --------------------
# NOTE:
# - This cache is per-process.
# - It persists to a JSON file to survive restarts.
# - If you run multiple replicas/workers, use a shared store (Redis) or disable persistence
#   to avoid file write contention.
RUNS_CACHE_TTL_SECONDS = int(os.getenv("RUNS_CACHE_TTL_SECONDS", "720"))  # default 12m
RUNS_CACHE_FILE = os.getenv("RUNS_CACHE_FILE", os.path.join(BASE_DIR, "data", "runs_cache.json"))
RUNS_CACHE_MAX_ENTRIES = int(os.getenv("RUNS_CACHE_MAX_ENTRIES", "3000"))
RUNS_CACHE_PERSIST_DEBOUNCE_SECONDS = float(os.getenv("RUNS_CACHE_PERSIST_DEBOUNCE_SECONDS", "2.0"))

# -------------------- Runs index (anti-stale) --------------------
# Purpose:
#   Keep a lightweight index of currently-existing run.json blobs.
#   When cached responses are served, filter out items whose run.json no longer exists.
RUNS_INDEX_TTL_SECONDS = int(os.getenv("RUNS_INDEX_TTL_SECONDS", "600"))  # default 10m

# allow disabling index-based filtering (fallback to listing-only behavior)
RUNS_INDEX_FALLBACK_TO_LISTING = os.getenv("RUNS_INDEX_FALLBACK_TO_LISTING", "true").strip().lower() in ("1", "true", "yes", "y")

# allow overriding index path via env var (name kept as requested)
# Note: this is a local path in this implementation (same behavior), just configurable via env.
RUNS_INDEX_PATH = os.getenv("RUNS_INDEX_BLOB", os.path.join(BASE_DIR, "data", "runs_index.json"))