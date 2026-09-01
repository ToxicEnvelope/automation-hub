# AutomationHub (Allure Reports Portal)

AutomationHub is a **FastAPI + single-page dashboard** for browsing automation runs and investigating Allure failures stored in Azure Blob Storage.

The portal now combines four workflows:

1. **Run monitoring** — filter and open uploaded Allure executions.
2. **Test-level statistics** — compare passed, failed, and error results by test name and identify the top recurring failures.
3. **AI Failure Agent** — generate a structured diagnosis for one selected failed test using a local embedded GGUF model and reviewed failure memory.
4. **Floating Failure Chat Assistant** — ask follow-up questions from `index.html` before opening the full report.

A typical workflow:

1. The Runner container executes Playwright tests.
2. The Runner generates an Allure report and uploads the report artifacts plus `run.json` to Blob Storage.
3. AutomationHub reads the run metadata to build the dashboard and reads Allure test-case artifacts for test-level statistics.
4. A user can open the complete report, request a structured AI analysis, submit human feedback, or ask a scoped question from the dashboard chat bubble.

The embedded GGUF file is intentionally not stored in source-control archives. It is expected under the configured model path locally and inside the Runner image.

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

`<suite>` is one of `smoke`, `regression`, `bugs`, `sanity`, or `all` (a combined run covering every suite). `all` is a literal suite name here, not a wildcard.
---

## Project structure

```text
(root-level)
├── README.md
├── pytest.ini
├── src
│   ├── ai_agent.py                  # Structured failure analysis orchestration
│   ├── ai_provider.py               # Embedded llama.cpp provider and JSON schemas
│   ├── ai_summary.py                # Existing report-summary flow
│   ├── app.py                       # FastAPI routes and static application
│   ├── failure_chat.py              # Scoped dashboard failure-chat orchestration
│   ├── failure_memory.py            # Reviewed/corrected/rejected AI memory
│   ├── main.py
│   ├── requirements.txt
│   ├── test_statistics.py           # Allure test-name statistics aggregation
│   ├── static
│   │   ├── app.js                   # Dashboard, charts, and chat bubble behavior
│   │   ├── index.html               # Runs dashboard and floating Ask AI widget
│   │   ├── report-viewer.css
│   │   ├── report-viewer.html
│   │   ├── report-viewer.js
│   │   └── styles.css
│   └── utils
│       ├── az.py
│       └── vars.py
├── tests
│   ├── conftest.py
│   ├── fakes.py
│   ├── test_app_routes.py
│   ├── test_failure_chat.py
│   ├── test_failure_memory_feedback.py
│   └── test_test_statistics.py
└── Setup
    ├── install.sh
    ├── GoldenCI
    │   └── Dockerfile
    └── RunnerCI
        ├── Dockerfile
        └── download-ai-model.sh
```

The expected default local model location is:

```text
<project-root>/models/automationhub-agent.gguf
```

The Runner image uses:

```text
/app/models/automationhub-agent.gguf
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

### Dashboard and reports

- `GET /`
  - Serves `src/static/index.html`.
- `GET /api/health`
  - Basic health check.
- `GET /api/runs?env=&platform=&suite=&q=&limit=`
  - Returns dashboard run metadata.
- `GET /api/report-context?suite=&env=&platform=&run_id=`
  - Returns report URLs, run context, and AI cache information.
- `GET /api/report-tests?suite=&env=&platform=&run_id=`
  - Returns failed, broken, error, and unknown Allure tests for a selected run.
- `POST /api/test-statistics`
  - Aggregates passed, failed, and error outcomes by test name for the dashboard charts.

### AI analysis, chat, and feedback

- `POST /api/ai/report-summary`
  - Existing report-summary endpoint for one selected test.
- `POST /api/ai/test-agent-analysis`
  - Generates or returns a cached structured Failure Agent analysis.
- `POST /api/ai/failure-chat`
  - Answers a grounded follow-up question about exactly one selected failed test.
- `POST /api/ai/test-agent-feedback`
  - Stores human review and reconciles verified, corrected, or rejected memory.
- `GET /api/ai/provider-status`
  - Reports provider, GGUF path, file state, model state, and fingerprint information.
- `POST /api/ai/provider-probe`
  - Invokes the local model with a random nonce and verifies a real JSON response.

### `/api/runs` query parameters

1. `env`: `qa|stage|prod` (optional; omit for every environment)
2. `platform`: `web|mobile|whitelabel` (optional; omit for every platform)
3. `suite`: `smoke|regression|bugs|sanity|all` (optional; omit for every suite). Note that `all` is a real suite value written by the runner for a combined run that covers every suite (`runs/all/<env>/<platform>/...`) — it is **not** a wildcard. To search across every suite, omit the parameter (or send an empty value) instead of passing `all`.
4. `q`: free-text search across `run.json` and `run_id` (optional)
5. `limit`: result limit; the backend applies a safety cap

Typical run fields:

```text
run_id, suite, env, platform, status, started_at, finished_at,
report_url, results_url, version, build_number
```

---

## UI behavior

The main dashboard provides:

1. Environment, platform, and suite filters.
2. Debounced free-text search.
3. Runs table with direct report access.
4. Existing execution-level charts.
5. **Test Outcomes by Name** statistics for passed, failed, and error tests.
6. **Top 5 Most Failed Tests** ranked by combined failed/error occurrences.
7. A floating **Ask AI** bubble for scoped failure investigation.

The floating assistant:

- Stays fixed at the bottom-right of `index.html`.
- Can be opened from the bubble or from an **Ask AI** action on a failed run.
- Requires one failed run and one failed/error test selection.
- Provides quick prompts and custom questions.
- Shows evidence references, confidence, answer type, and model/fallback status.
- Links directly to the selected test in the full report viewer.
- Remembers only whether the bubble is open by using `localStorage` key `ah-ai-chat-open`.
- Does not write ordinary chat messages into long-term failure memory.

The report viewer remains the deeper investigation surface. It embeds the Blob-hosted Allure report and shows structured AI analysis and human feedback controls beside it.

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
```

---

## Report Viewer + AI Summary Agent

New flow:
1. Main AutomationHub page lists runs.
2. Clicking a report row opens `/report-viewer.html?suite=<suite>&env=<env>&platform=<platform>&run_id=<run_id>` in a new tab.
3. The report viewer embeds the public Blob-hosted Allure report in an iframe.
4. The AI Summary Agent loads failed/broken/error test cases from Blob artifacts.
5. The user selects the exact test case in the AI panel.
6. The AI summary is scoped to that selected test, including evidence, likely root cause, and suggested fix.

New endpoints:
- `GET /api/report-context?suite=&env=&platform=&run_id=`
  - Returns run metadata, iframe report URL, Blob prefix, Allure root, and AI cache status.
- `GET /api/report-tests?suite=&env=&platform=&run_id=`
  - Returns failed/broken/error Allure test cases for the run.
  - The report viewer uses this as the source of truth for the selected test because AutomationHub cannot safely inspect clicks inside the cross-origin Blob iframe.
- `POST /api/ai/report-summary`
  - Body: `{ "suite": "bugs", "env": "prod", "platform": "web", "run_id": "branch-1234-a1b2c3d4", "test_id": "abc123", "test_blob": "runs/bugs/prod/web/branch-1234-a1b2c3d4/awesome/data/test-cases/abc123.json", "refresh": false }`
  - Returns cached or newly generated AI summary for the selected test.

AI behavior:
- The AI does not scrape the iframe DOM.
- Backend extracts evidence from:
  - `run.json`
  - `widgets/summary.json`
  - `widgets/categories.json`
  - `widgets/suites.json`
  - `data/test-cases/*.json`
- Selected-test summary cache is saved next to the run:
  - `<REPORTS_PREFIX>/<suite>/<env>/<platform>/<run_id>/ai-summary-tests/<test-id>.json`
- Run-level fallback summary cache is still supported:
  - `<REPORTS_PREFIX>/<suite>/<env>/<platform>/<run_id>/ai-summary.json`

AI environment variables:
```shell
AI_SUMMARY_PROVIDER="azure_openai"          # Optional. Defaults to azure_openai when Azure OpenAI vars exist; otherwise heuristic.
AZURE_OPENAI_ENDPOINT="https://<resource>.openai.azure.com"
AZURE_OPENAI_API_KEY="<key>"
AZURE_OPENAI_DEPLOYMENT="<deployment-name>"
AZURE_OPENAI_API_VERSION="2024-05-01-preview"
AI_SUMMARY_TIMEOUT_SECONDS="45"
AI_SUMMARY_MAX_TOKENS="1400"
AI_SUMMARY_MAX_FAILURES="40"
AI_SUMMARY_MAX_TEST_CASE_BLOBS="350"
AI_SUMMARY_MAX_TEXT_CHARS="1800"
```

If Azure OpenAI is not configured, the endpoint still works using the local heuristic summarizer. This is useful for validation before connecting a real model.

---

## AI Failure Agent v2 — embedded GGUF model, Option A

This version adds the **AI Failure Agent** for selected failed tests in the Report Viewer.

### Flow

```text
Main AutomationHub page
  -> click report
  -> report-viewer.html opens in a new tab
  -> iframe shows the Blob-hosted Allure report
  -> AI Failure Agent analyzes the selected failed test
```

The AI does **not** scrape the iframe. The backend reads the same Blob artifacts directly:

```text
run.json
widgets/summary.json
widgets/categories.json
widgets/suites.json
data/test-cases/*.json
```

### What the agent returns

For the selected test, the agent returns:

```text
- Failure cause
- Evidence
- Suggested fix
- Similar previous failures
- Confidence
- Provider/model/cache status
```

### Embedded model provider

The new provider is:

```env
AI_PROVIDER=embedded_llama_cpp
```

It uses `llama-cpp-python` to load a bundled GGUF model from:

```text
/app/models/automationhub-agent.gguf
```

The model is lazy-loaded only when this endpoint is called:

```text
POST /api/ai/test-agent-analysis
```

If the model is missing or fails to load, AutomationHub falls back to deterministic heuristic analysis and shows a provider warning in the UI.

### Model placement before Docker build

The model binary is **not included** in this ZIP. Place the GGUF file before building the Runner image:

```bash
cp /path/to/model.gguf models/automationhub-agent.gguf
```

Then build the Runner image. The Runner Dockerfile copies the `models/` directory into:

```text
/app/models
```

### New endpoints

```text
POST /api/ai/test-agent-analysis
POST /api/ai/test-agent-feedback
POST /api/test-statistics
```

`POST /api/ai/test-agent-analysis` body:

```json
{
  "suite": "bugs",
  "env": "prod",
  "platform": "web",
  "run_id": "branch-1234-a1b2c3d4",
  "test_id": "abc123",
  "test_blob": "runs/bugs/prod/web/branch-1234-a1b2c3d4/awesome/data/test-cases/abc123.json",
  "refresh": false
}
```

### Dashboard test-level statistics

The dashboard keeps the existing execution-level charts and adds two Allure test-level charts:

```text
Test Outcomes by Name      - passed / failed / error occurrences grouped by test name
Top 5 Most Failed Tests   - failed and error occurrences grouped by test name
```

The frontend submits the latest completed run locators to:

```text
POST /api/test-statistics
```

Example body:

```json
{
  "runs": [
    {
      "suite": "smoke",
      "env": "prod",
      "platform": "web",
      "run_id": "exec-2734-c3840a39"
    }
  ],
  "max_runs": 100,
  "max_test_cases": 5000
}
```

The backend reads `widgets/suites.json` or `data/suites.json` first, so one Allure index Blob is normally sufficient per run. It falls back to a bounded `data/test-cases/*.json` scan only when the suite index is unavailable. Allure `broken`, `error`, and `unknown` statuses are grouped under the dashboard `error` category.

### Human feedback workflow

The report viewer supports `helpful`, `partially_correct`, and `incorrect` feedback. Feedback updates the related failure-memory record and also writes an immutable audit event.

```text
helpful                       -> verified and preferred during retrieval
partially_correct             -> corrected; human cause/fix become effective
incorrect without correction  -> rejected and excluded from retrieval
incorrect with correction     -> corrected; only the human correction is reused
```

`partially_correct` requires an `actual_cause` or `actual_fix`. Regenerating the same analysis preserves the existing human review. Cached analyses load the current feedback state from failure memory instead of trusting stale feedback embedded in the cache.

### Failure memory

AutomationHub stores historical failure memory in Blob Storage:

```text
ai-memory/index/failure-index.jsonl
ai-memory/failures/<signature>/<run_id>-<test_id>.json
ai-memory/feedback/<run_id>-<test_id>-<feedback-id>.json
runs/<suite>/<env>/<platform>/<run_id>/ai-agent-tests/<test-id>.json
```

This gives the agent historical context without retraining the model. Corrected and verified memories are ranked above unreviewed model output, while rejected memories are excluded from future prompt context.

### Required Blob permissions

The AI cache and memory features write JSON back to Blob Storage. The AutomationHub identity should have:

```text
Storage Blob Data Contributor
```

Reader permission is enough for the dashboard/report viewer, but not enough for AI cache/memory writes.

### Embedded AI environment variables

```env
AI_PROVIDER=embedded_llama_cpp
AI_MODEL_PATH=/app/models/automationhub-agent.gguf
AI_MODEL_NAME=qwen2.5-7b-instruct-q4_k_m
AI_MODEL_CONTEXT_TOKENS=32768
AI_MODEL_THREADS=4
AI_MODEL_MAX_TOKENS=900
AI_MODEL_TEMPERATURE=0.0
AI_MODEL_TIMEOUT_SECONDS=180
AI_MODEL_LOAD_MODE=lazy
AI_MODEL_CACHE_ENABLED=true
```

`AI_MODEL_NAME` and `AI_MODEL_CONTEXT_TOKENS` above match the currently recommended model (see [Swapping the embedded AI model](#swapping-the-embedded-ai-model)). They are plain strings/numbers read from the environment — nothing in the code assumes a specific model, so any GGUF works as long as the file at `AI_MODEL_PATH` matches.

### Production note

For the MVP, run the AI-enabled service with a single backend worker. If the service runs with multiple workers or replicas, each worker can load its own copy of the model and increase memory usage.


### Build the docker
```
docker build \
  -f Setup/RunnerCI/Dockerfile \
  --build-arg AI_MODEL_DOWNLOAD_URL="https://huggingface.co/Qwen/Qwen2.5-7B-Instruct-GGUF/resolve/main/qwen2.5-7b-instruct-q4_k_m.gguf?download=true" \
  --build-arg AI_MODEL_SHA256="<sha256 of the downloaded file>" \
  --build-arg AI_MODEL_REQUIRED=true \
  -t automation-hub-runner:ai . --no-cache
  ```

  `models/automationhub-agent.gguf` must **not** already exist in the build context when you change `AI_MODEL_DOWNLOAD_URL` — see [Swapping the embedded AI model](#swapping-the-embedded-ai-model) for why.

---

## Verify that the GGUF actually generated the response

A successful `/api/ai/test-agent-analysis` HTTP response alone is not proof that the model ran, because AutomationHub can use the deterministic heuristic fallback. Use the runtime probe:

```bash
curl -sS http://localhost:80/api/ai/provider-status | python3 -m json.tool
curl -sS -X POST http://localhost:80/api/ai/provider-probe | python3 -m json.tool
```

The probe passes only when `llama.cpp` invokes the bundled GGUF and the model returns the exact random nonce as valid JSON. A verified response contains:

```json
{
  "ok": true,
  "verified": true,
  "provider": "embedded_llama_cpp",
  "inference": {
    "source": "model",
    "model_invoked": true,
    "response_received": true,
    "response_valid_json": true,
    "response_sha256": "...",
    "elapsed_ms": 1234.56,
    "inference_id": "..."
  }
}
```

Every test-agent response now also exposes:

```text
provider
fallback_used
actual_model_response
inference.inference_id
inference.elapsed_ms
inference.response_sha256
inference.response_valid_json
model.model_loaded
model.model_fingerprint
```

For a deployment where heuristic fallback must never be presented as AI output, configure:

```env
AI_REQUIRE_MODEL_RESPONSE=true
AI_CACHE_FALLBACK_RESULTS=false
```

With strict mode enabled, `/api/ai/test-agent-analysis` fails instead of returning heuristic output when the GGUF cannot load or does not return valid JSON.

The default local model path is now resolved as:

```text
<project-root>/models/automationhub-agent.gguf
```

The Runner image continues to use:

```text
/app/models/automationhub-agent.gguf
```

When `AI_MODEL_SHA256` is supplied during the Runner build, the build now verifies both an already-present `models/automationhub-agent.gguf` and a downloaded model before completing.


### Structured-output reliability

Some GGUF chat templates ignore a separate `system` message, so AutomationHub always sends the
agent instructions and evidence together in the first `user` message rather than relying on a
`system` role — this works the same way regardless of which model is loaded. The provider also
uses JSON Schema mode, compacts attachment evidence, and rejects incomplete JSON instead of
attempting to repair it.

A rejected inference log includes `finish_reason`, prompt/completion token usage, response-format
mode, and whether the output appears truncated. If `finish_reason=length`, first reduce the
evidence payload or raise `AI_MODEL_MAX_TOKENS`, keeping prompt tokens plus completion tokens
within the model's context window.

### Swapping the embedded AI model

Nothing in `src/ai_provider.py` or `src/ai_agent.py` is specific to any one model — the provider
loads whatever GGUF file lives at `AI_MODEL_PATH` and talks to it generically through
`llama_cpp.Llama.create_chat_completion`. Swapping models is a config and file change only; no
application code changes are required.

**The one gotcha:** `Setup/RunnerCI/download-ai-model.sh` skips downloading whenever
`models/automationhub-agent.gguf` already exists and is non-empty in the build context. If you
change `AI_MODEL_DOWNLOAD_URL` without first removing the existing file, the build will silently
keep the old model. Delete (or replace) `models/automationhub-agent.gguf` before rebuilding with a
new `AI_MODEL_DOWNLOAD_URL`. Keep the `models/` directory itself present (even empty, e.g. with a
`.gitkeep`) so the Dockerfile's `COPY models /app/models` step still succeeds.

Two current options, evaluated for this use case (CPU inference, JSON-schema-constrained output,
one persistent backend worker rather than a per-CI-job process):

**Qwen2.5-7B-Instruct** — recommended default. Purpose-tuned by Qwen for structured JSON output,
with the most mature GGUF/llama.cpp ecosystem of current small models. Roughly double the memory
and inference time of the previous Phi-3-mini-4k, which is an acceptable trade for an on-demand,
human-in-the-loop diagnosis feature rather than a per-CI-job cost.

```env
AI_MODEL_NAME=qwen2.5-7b-instruct-q4_k_m
AI_MODEL_CONTEXT_TOKENS=32768
```

```
--build-arg AI_MODEL_DOWNLOAD_URL="https://huggingface.co/Qwen/Qwen2.5-7B-Instruct-GGUF/resolve/main/qwen2.5-7b-instruct-q4_k_m.gguf?download=true"
```

Note: Qwen2.5-7B-Instruct's model card advertises up to 128K context via YaRN rope scaling, but
that extension is only validated in vLLM. Plain `llama-cpp-python` (as used here) does not expose
rope-scaling parameters through `AIProviderConfig` today, so 32768 is the safe native ceiling
without a code change — still 8x the previous 4096.

**Phi-4-mini-instruct** — lighter alternative if the host is RAM-constrained. Same 3.8B parameter
count and similar quantized footprint as the previous Phi-3-mini-4k, but with a genuinely native
128K context window and better benchmarks.

```env
AI_MODEL_NAME=phi-4-mini-instruct-q4_k_m
AI_MODEL_CONTEXT_TOKENS=32768
```

```
--build-arg AI_MODEL_DOWNLOAD_URL="https://huggingface.co/unsloth/Phi-4-mini-instruct-GGUF/resolve/main/Phi-4-mini-instruct-Q4_K_M.gguf?download=true"
```

(`AI_MODEL_CONTEXT_TOKENS` can be raised well beyond 32768 for Phi-4-mini specifically, since its
128K context is native rather than YaRN-extrapolated — raise it if evidence payloads need the
extra room.)

Leave `AI_MODEL_CHAT_FORMAT` unset for either model so `llama-cpp-python` auto-detects the chat
template embedded in the GGUF's own metadata.

**Confirming the new model is actually loaded** — `AI_MODEL_NAME` is just an operator-set label
and proves nothing by itself. To verify the swap actually took effect:

1. `GET /api/ai/provider-status` and check `model_size_bytes` (and `model_fingerprint`, which
   becomes the real SHA-256 once `AI_MODEL_SHA256` is set) against the known size/hash of the file
   you downloaded. This is the authoritative check — file identity, not the label.
2. `POST /api/ai/provider-probe` confirms a real GGUF is loaded and returns valid structured JSON
   rather than the deterministic heuristic fallback — pair it with step 1 for full confidence,
   since the probe alone proves *a* model works, not *which* one.
3. Temporarily set `AI_REQUIRE_MODEL_RESPONSE=true` during rollout so a failed load surfaces as a
   hard error on `/api/ai/test-agent-analysis` instead of quietly degrading to the heuristic
   fallback.
4. Run one real end-to-end pass: open a known failed test in the report viewer, trigger **Ask AI**,
   and confirm the diagnosis looks as expected.

### Split (multi-part) GGUF models

Some repos publish a quantization as multiple shards named
`<name>-00001-of-00002.gguf`, `<name>-00002-of-00002.gguf`, etc. (llama.cpp's own
`gguf-split` convention). `llama-cpp-python`'s underlying loader already supports this natively —
point `AI_MODEL_PATH` at the **first** shard, by its original filename, and llama.cpp finds the
rest by pattern-matching sibling files in the same directory. No application code re-implements
that loading logic.

What AutomationHub adds on top: `ai_provider.py` detects the `-NNNNN-of-MMMMM.gguf` naming
pattern in `AI_MODEL_PATH` and treats the whole set as one model for health/status purposes —

```env
AI_MODEL_PATH=/app/models/qwen2.5-7b-instruct-q5_k_m-00001-of-00002.gguf
AI_MODEL_NAME=qwen2.5-7b-instruct-q5_k_m
```

- `GET /api/ai/provider-status` reports `model_size_bytes` as the **sum across every shard**, plus
  `model_parts_total`, `model_parts_found`, and `model_parts_missing` — so a partially-copied
  model (e.g. only the first shard made it into the image) is visible as a status-check failure
  rather than something that only surfaces as a confusing load error on the first real request.
- `/api/ai/test-agent-analysis` (and the `provider-probe`) fail fast with a clear error naming the
  specific missing shard, instead of a generic "model not found" that would incorrectly suggest
  the referenced shard itself is absent.

Requirements: every shard must be copied into the same `models/` directory (the existing
`COPY models /app/models` Dockerfile step already does this as long as all shard files are present
locally when you build), and none of them may be renamed — the loader locates siblings by matching
the `-NNNNN-of-MMMMM.gguf` pattern in the filename you pointed `AI_MODEL_PATH` at.

`Setup/RunnerCI/download-ai-model.sh`'s single-URL download flow does not know how to fetch
multiple shards, so split models must be placed into `models/` manually (or via a separate
download step of your own) before running `docker build`, rather than through
`AI_MODEL_DOWNLOAD_URL`.

## Floating AI failure assistant

The dashboard (`index.html`) includes a floating **Ask AI** bubble for failure triage before opening the complete Allure report.

### Scope and behavior

1. Open the bubble or click **Ask AI** on a failed run row.
2. Select a failed/error Allure test.
3. Choose a quick action or type a custom question.
4. Review the grounded answer and evidence references.
5. Open the test-scoped full report when deeper inspection is needed.

Available quick actions include:

```text
Explain failure
Check recurrence
Next checks
Draft bug report
```

The conversation is intentionally tied to one selected test. The backend reuses:

- The cached or generated Failure Agent analysis.
- The selected Allure failure message, trace excerpt, failed steps, and bounded attachment metadata.
- Human-reviewed effective cause/fix memory.
- Up to three similar previous failures.
- The existing embedded `llama.cpp` provider and inference lock.

Ordinary chat messages are not written to long-term failure memory. Only explicit feedback submitted through the report viewer changes reviewed memory.

### Chat API

```http
POST /api/ai/failure-chat
Content-Type: application/json
```

Example request:

```json
{
  "suite": "smoke",
  "env": "prod",
  "platform": "web",
  "run_id": "exec-2734-c3840a39",
  "test_id": "63d9bf46178cb70e",
  "test_blob": null,
  "test_name": null,
  "question": "Why did this test fail?",
  "conversation_id": null,
  "history": []
}
```

At least one of `test_id`, `test_blob`, or `test_name` is required.

Chat limits:

```text
Maximum question length:        1,500 characters
Maximum history messages sent:  8
Maximum characters per message: 1,200
Maximum generated tokens:       700
Maximum evidence references:    5
Maximum historical matches:     3
```

Example response shape:

```json
{
  "ok": true,
  "conversation_id": "chat_45f8a73c12ab34cd",
  "answer": "The test failed during the hotel-search step. The supplied evidence does not prove whether the cause was an application issue, test-data issue, or locator issue.",
  "confidence": "medium",
  "answer_type": "mixed",
  "follow_up_suggestions": [
    "What evidence supports that conclusion?",
    "Has this failure happened before?"
  ],
  "evidence": [
    {
      "type": "failure_message",
      "value": "Failed: fail to search hotel אוריינט"
    }
  ],
  "report_viewer_url": "/report-viewer.html?suite=smoke&env=prod&platform=web&run_id=exec-2734-c3840a39&test_id=63d9bf46178cb70e",
  "provider": "embedded_llama_cpp",
  "fallback_used": false,
  "actual_model_response": true,
  "inference": {
    "inference_id": "...",
    "response_valid_json": true,
    "finish_reason": "stop"
  }
}
```

The provider is constrained to a dedicated JSON schema:

```json
{
  "answer": "string",
  "confidence": "high | medium | low",
  "answer_type": "evidence | inference | mixed | unknown",
  "follow_up_suggestions": ["string"]
}
```

When the model is unavailable and `AI_REQUIRE_MODEL_RESPONSE=false`, the endpoint returns a deterministic evidence-based fallback. The UI labels the response as fallback. With `AI_REQUIRE_MODEL_RESPONSE=true`, the endpoint fails rather than presenting fallback text as a model response.

### Grounding contract

The assistant must:

- Answer only from the selected failure context, existing structured analysis, reviewed memory, and recent bounded chat history.
- Distinguish direct evidence from inference.
- State when the available evidence cannot determine the cause.
- Avoid inventing selectors, HTTP statuses, source files, logs, screenshots, services, or backend failures.

### Frontend state

The browser keeps the conversation for the currently selected test during the active page session. Only the open/closed state is persisted:

```text
localStorage key: ah-ai-chat-open
```

Changing the selected run or test resets the active scoped conversation.


## Tests and validation

Run the automated tests from the project root:

```bash
pytest -q
```

Run Python compilation checks:

```bash
python -m compileall -q src tests
```

Run JavaScript syntax validation when Node.js is available:

```bash
node --check src/static/app.js
node --check src/static/report-viewer.js
```

Relevant chatbot coverage:

```text
tests/test_app_routes.py      Route registration and API surface
tests/test_failure_chat.py    Validation, grounding, fallback, and response shape
```

Relevant AI-memory and dashboard coverage:

```text
tests/test_failure_memory_feedback.py
tests/test_test_statistics.py
```

A local or deployed end-to-end validation should also confirm:

1. A failed run appears in the chat selector.
2. `/api/report-tests` returns failed/error tests.
3. A quick question invokes `/api/ai/failure-chat`.
4. The response identifies real-model versus fallback output.
5. The **Open full failure report** link targets the selected test.
6. Chat use does not create or overwrite long-term feedback memory.



### Static asset cache and chat-bubble deployment

The dashboard and report-viewer HTML inject a content-derived query version into local CSS and JavaScript URLs, for example:

```text
/static/styles.css?v=<content-hash>
/static/app.js?v=<content-hash>
```

The HTML routes also return `Cache-Control: no-store`. This prevents a deployment from combining new HTML/JavaScript with an older cached stylesheet, which would render the AI chat controls as unstyled document content. An optional `AUTOMATIONHUB_STATIC_ASSET_VERSION` environment variable can override the generated content hash for controlled releases.

The closed AI panel is marked `inert` and `aria-hidden`. Before closing, focus is returned to the **Ask AI** launcher. This prevents Chromium's `Blocked aria-hidden ... descendant retained focus` accessibility warning and keeps hidden chat controls outside keyboard navigation.

After deploying a new image, confirm that the browser requests versioned assets in DevTools **Network**, and that the response body contains the `.ai-chat-launcher` and `.ai-chat-panel` rules:

```bash
curl -fsS http://localhost/static/styles.css | grep -E "ai-chat-launcher|ai-chat-panel"
```
