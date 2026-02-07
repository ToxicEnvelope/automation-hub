# Runner image for AutomationHub: copies code and starts FastAPI.
FROM isrotelautomation.azurecr.io/automation-hub-base:latest

USER root
WORKDIR /app

# Copy application code
COPY src /app/src

# Normalize line endings just in case (Windows Git)
RUN find /app -type f -name "*.py" -o -name "*.html" | xargs -r sed -i 's/\r$//'

ENV PYTHONPATH=/app \
    PORT=80

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -fsS http://localhost:80/api/health || exit 1

CMD ["python3", "/app/src/main.py"]