# Runner image for AutomationHub: copies code and starts FastAPI.
FROM isrotelautomation.azurecr.io/automation-hub-base:latest-ai

USER root
WORKDIR /app

ARG AI_MODEL_DOWNLOAD_URL=""
ARG AI_MODEL_SHA256=""
ARG AI_MODEL_REQUIRED="false"

# Copy application code
COPY src /app/src
COPY models /app/models
COPY models/automationhub-agent.gguf /app/models/automationhub-agent.gguf
COPY Setup/RunnerCI/download-ai-model.sh /tmp/download-ai-model.sh

RUN chmod +x /tmp/download-ai-model.sh \
    && AI_MODEL_DOWNLOAD_URL="${AI_MODEL_DOWNLOAD_URL}" \
       AI_MODEL_SHA256="${AI_MODEL_SHA256}" \
       MODEL_DIR="/app/models" \
       MODEL_FILE="automationhub-agent.gguf" \
       /tmp/download-ai-model.sh || MODEL_DOWNLOAD_EXIT="$?" \
    && if [ "${MODEL_DOWNLOAD_EXIT:-0}" != "0" ]; then \
         if [ "${AI_MODEL_REQUIRED}" = "true" ]; then \
           echo "AI model is required but was not downloaded."; \
           exit 1; \
         else \
           echo "AI model not embedded. Heuristic fallback will be used."; \
         fi; \
       fi \
    && rm -f /tmp/download-ai-model.sh

# Normalize line endings just in case
RUN find /app -type f \( -name "*.py" -o -name "*.html" -o -name "*.js" -o -name "*.css" \) \
    -print0 | xargs -0 -r sed -i 's/\r$//'

ENV PYTHONPATH=/app \
    PORT=80 \
    AI_PROVIDER=embedded_llama_cpp \
    AI_MODEL_PATH=/app/models/automationhub-agent.gguf \
    AI_MODEL_SHA256=${AI_MODEL_SHA256} \
    AI_MODEL_LOAD_MODE=lazy \
    AI_REQUIRE_MODEL_RESPONSE=false \
    AI_CACHE_FALLBACK_RESULTS=false \
    AI_STORE_FALLBACK_MEMORY=false

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -fsS http://localhost:80/api/health || exit 1

CMD ["python3", "/app/src/main.py"]