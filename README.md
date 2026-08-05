# AutomationHub (Allure Reports Portal)

AutomationHub is a **FastAPI + single-page dashboard** for browsing automation runs and investigating Allure failures stored in Azure Blob Storage.

The portal now combines four workflows:

1. **Run monitoring** — filter and open uploaded Allure executions.
2. **Test-level statistics** — compare passed, failed, and error results by test name and identify the top recurring failures.
3. **AI Failure Agent** — generate a structured diagnosis for one selected failed test using the local Phi-3 GGUF model and reviewed failure memory.
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

## Optional UI and behavior

No separate frontend deployment is required. The browser calls the FastAPI routes from the same origin.

The dashboard persists these UI preferences in browser `localStorage`:

```text
ah-theme            Light/dark theme
ah-ai-chat-open     Floating AI assistant open/closed state
```

The AI chat conversation itself is kept only in the active page state and is reset when the selected run or test changes.

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

1. `env`: `qa|stage|prod` (optional)
2. `platform`: `web|mobile|whitelabel` (optional)
3. `suite`: `smoke|regression|bugs|sanity` (optional; omit or use `all` for no filter)
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

### AI and statistics endpoints

```text
GET  /api/ai/provider-status
POST /api/ai/provider-probe
POST /api/ai/test-agent-analysis
POST /api/ai/failure-chat
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
AI_MODEL_NAME=phi-3-mini-4k-instruct-q4
AI_MODEL_CONTEXT_TOKENS=4096
AI_MODEL_THREADS=4
AI_MODEL_MAX_TOKENS=900
AI_MODEL_TEMPERATURE=0.0
AI_MODEL_TIMEOUT_SECONDS=180
AI_MODEL_LOAD_MODE=lazy
AI_MODEL_CACHE_ENABLED=true
```

### Production note

For the MVP, run the AI-enabled service with a single backend worker. If the service runs with multiple workers or replicas, each worker can load its own copy of the model and increase memory usage.


### Build the docker
```
docker build \
  -f Setup/RunnerCI/Dockerfile \
  --build-arg AI_MODEL_DOWNLOAD_URL="https://huggingface.co/microsoft/Phi-3-mini-4k-instruct-gguf/resolve/main/Phi-3-mini-4k-instruct-q4.gguf?download=true" \
  --build-arg AI_MODEL_REQUIRED=true \
  -t automation-hub-runner:ai . --no-cache
  ```

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


### Phi-3 structured-output reliability fix

The official Phi-3 Mini 4K GGUF chat template may ignore a separate `system` message.
AutomationHub therefore sends the agent instructions and evidence together in the first `user` message.
The provider also uses JSON Schema mode, compacts attachment evidence, and rejects incomplete JSON instead of attempting to repair it.

Recommended runtime settings:

```env
AI_PROVIDER=embedded_llama_cpp
AI_MODEL_PATH=/app/models/automationhub-agent.gguf
AI_MODEL_CONTEXT_TOKENS=4096
AI_MODEL_MAX_TOKENS=900
AI_MODEL_TEMPERATURE=0.0
AI_REQUIRE_MODEL_RESPONSE=true
```

A rejected inference log now includes `finish_reason`, prompt/completion token usage, response-format mode, and whether the output appears truncated. If `finish_reason=length`, first reduce the evidence payload or raise `AI_MODEL_MAX_TOKENS` while keeping prompt tokens plus completion tokens within the 4096-token model context.

## Floating AI failure assistant

The dashboard (`index.html`) includes a floating **Ask AI** bubble for failure triage before opening the complete Allure report.

Primary implementation files:

```text
src/failure_chat.py
src/app.py
src/ai_provider.py
src/static/index.html
src/static/app.js
src/static/styles.css
tests/test_failure_chat.py
tests/test_app_routes.py
```

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

