# AutomationHub embedded AI model

AutomationHub Option A expects a bundled GGUF model in this directory before building the Runner image.

Required filename:

```text
automationhub-agent.gguf
```

Expected container path after build:

```text
/app/models/automationhub-agent.gguf
```

Recommended MVP model:

```text
Phi-3-mini-4k-instruct GGUF, Q4 quantization
```

The model binary is intentionally not included in this source package because GGUF files are large and should be managed as a controlled release artifact.

Before building the Runner image:

```bash
cp /path/to/your/model.gguf models/automationhub-agent.gguf
```

Runtime environment defaults:

```env
AI_PROVIDER=embedded_llama_cpp
AI_MODEL_PATH=/app/models/automationhub-agent.gguf
AI_MODEL_NAME=phi-3-mini-4k-instruct-q4
AI_MODEL_CONTEXT_TOKENS=4096
AI_MODEL_THREADS=4
AI_MODEL_MAX_TOKENS=900
AI_MODEL_TEMPERATURE=0.2
AI_MODEL_LOAD_MODE=lazy
```

Behavior:

- The model is loaded lazily on the first `/api/ai/test-agent-analysis` request.
- The model is not loaded during AutomationHub startup.
- If the model file is missing or inference fails, AutomationHub returns a deterministic heuristic fallback analysis and displays a provider warning in the UI.
- The fallback exists so the Report Viewer stays usable while the model artifact is being prepared.

Important deployment note:

If AutomationHub runs with multiple backend workers or replicas, each worker can load its own copy of the model. For the MVP, run the AI-enabled AutomationHub service with one backend worker or move the AI agent into a separate internal worker service later.
