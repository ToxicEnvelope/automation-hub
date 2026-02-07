# AutomationHub (Allure Reports Portal)

AutomationHub is a lightweight **FastAPI + single-page UI** that lists automation **test runs** and opens the corresponding **Allure HTML report** stored in **Azure Blob Storage**.

A typical workflow:
1. Your **Runner** container executes Playwright tests.
2. Runner generates an Allure report (`allure-report/index.html`) and uploads artifacts to Blob Storage under a structured path.
3. **AutomationHub** reads `run.json` files from Blob Storage to build a **filterable Runs table** (env/platform/suite/search).
4. Clicking a run row opens the public `index.html` report in a new browser tab.

---

## What gets stored in Azure Storage

Storage Account: `REPORTS_STORAGE_ACCOUNT`  
Container: `REPORTS_CONTAINER` (you currently use `reports`)

For each run, the Runner uploads (recommended):
- `index.html` — the rendered Allure report (single-file)
- `run.json` — small metadata file used for listing/filtering
- `allure-results.zip` — raw Allure results (for re-generation, history/trends, debugging)

**Blob path convention**
```textmate
<REPORTS_PREFIX>/<suite>/<env>/<platform>/<run_id>/
├── index.html
├── run.json
└── allure-results.zip
```
Note: If your container is named `reports` and `REPORTS_PREFIX=runs`, URLs look like:
> `.../reports/runs/<suite>/...`
> If you prefer cleaner URLs, set `REPORTS_PREFIX=runs` (or similar).
---

## Project structure
```textmate
(root-level)
├─── src
│      ├─── app.py
│      ├─── main.py
│      ├─── requirements.txt
│      ├─── static
│      │      └─── index.html   
│      ├─── static
│      │      └─── data
│      │             ├─── runs_index.json
│      │             └─── runs_cache.json
│      └─── utils
│             ├───az.py
│             └───vars.py
└─── Setup
       ├─── install.sh
       ├─── GoldenCI
       │      └─── Dockerfile
       └─── RunnerCI
              └─── Dockerfile      
```

---

## Prerequisites

- Python 3.10+ (recommended)
- Azure permissions (see below)
- `requirements.txt` installed:
```shell
  pip install -r src/requirements.txt
```
- Required runtime environment variables
- AutomationHub needs these to list runs (listing is not possible anonymously even if blobs are publicly readable):

---

## Storage

- `REPORTS_STORAGE_ACCOUNT` -> Example: allureautotests
- `REPORTS_CONTAINER` -> Example: reports
- `REPORTS_PREFIX` -> Example: runs

---

## Optional UI/behavior
- none required for the UI; it calls /api/runs from the same host

---

## Azure permissions

Even with public `blob access` = `Blob`, Azure Storage does not allow anonymous listing.

`AutomationHub` must list blobs using `Azure identity (RBAC)`.

#### Assign to the `AutomationHub` identity (your user locally, and Managed Identity in Azure):
- Storage Blob Data Reader (minimum for listing + reading run.json)
  - If you also want AutomationHub to upload or manage blobs: Storage Blob Data Contributor

If AutomationHub also needs to read Azure App Configuration in the future:
- App Configuration Data Reader

---

## Run locally (IDE)
1) Authenticate Azure on your machine
```shell
 az login
 az account set --subscription "<your-subscription-id-or-name>"
```
2) Export environment variables
#### Powershell:
```shell
$env:REPORTS_STORAGE_ACCOUNT="allureautotests"
$env:REPORTS_CONTAINER="reports"
$env:REPORTS_PREFIX="runs"
```
#### Bash:
```shell
export REPORTS_STORAGE_ACCOUNT="allureautotests"
export REPORTS_CONTAINER="reports"
export REPORTS_PREFIX="runs"
```
3) Start the server
- Option A (recommended if you have src/main.py using uvicorn):
```shell
python src/main.py
```
- Option B (direct uvicorn):
```shell
uvicorn src.app:app --reload --host 0.0.0.0 --port 8000 
```
Open:
- UI: http://localhost:80/
- API: http://localhost:80/api/runs
- Swagger: http://localhost:80/docs#

---

## API endpoints
- `GET /`
  - Serves the UI (src/static/index.html)
- `GET /api/health`
  - Basic health check
- `GET /api/runs?env=&platform=&suite=&q=&limit=`
  - Returns a JSON list of runs.

#### Query parameters:
1) `env`: `qa|stage|prod` (optional)
2) `platform`: `web|mobile|whitelabel` (optional)
3) `suite`: `smoke|regression` (optional; omit or `all` means no filter)
4) `q`: free-text search across `run.json` and `run_id` (optional)
5) `limit`: default 50 (cap recommended)

#### Response fields (typical):
- `run_id, suite, env, platform, status, started_at, finished_at, report_url, results_url`

---

## UI behavior
The UI provides:
1. Environment dropdown
2. Platform dropdown
3. Suite dropdown
4. Search input (debounced)
5. Runs table
Clicking a run row opens `report_url` in a new tab.

---

## Environment Variables:
```shell
############################
# Blob / Storage (required)
############################

REPORTS_STORAGE_ACCOUNT="allureautotests"   # Azure Storage Account name that holds the reports container (used to build account_url + public URLs)
REPORTS_CONTAINER="reports"                 # Blob container name where runs are stored (used for listing + public URLs)

############################
# Blob layout (optional)
############################

REPORTS_PREFIX="runs"                       # Optional path prefix inside the container (runs/<suite>/<env>/<platform>/<run_id>/...)

############################
# Server-side cache (optional)
############################

RUNS_CACHE_TTL_SECONDS="720"                # TTL (seconds) for cached /api/runs responses (default 720 = 12 minutes)
RUNS_CACHE_FILE="/app/src/data/runs_cache.json"  # Path to persisted cache JSON on disk (default: <BASE_DIR>/data/runs_cache.json)
RUNS_CACHE_MAX_ENTRIES="3000"               # Max cache entries kept (LRU eviction after exceeding this)
RUNS_CACHE_PERSIST_DEBOUNCE_SECONDS="2.0"   # Debounce writes to RUNS_CACHE_FILE to reduce disk churn

############################
# Runs index (anti-stale) (optional)
############################

RUNS_INDEX_TTL_SECONDS="300"                # TTL (seconds) for the run.json existence index (default 300 = 5 minutes)
RUNS_INDEX_FILE="/app/src/data/runs_index.json"  # Path to persisted index JSON on disk (default: <BASE_DIR>/data/runs_index.json)

############################
# Frontend behavior (optional)
############################

# (No backend env vars here; stored in browser localStorage)
# localStorage key: "ah_theme"              # Persists the user theme (light/dark) in the browser

```