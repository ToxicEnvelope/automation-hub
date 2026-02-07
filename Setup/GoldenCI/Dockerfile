FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    VENV_PATH=/opt/venv

USER root
WORKDIR /app

# Minimal OS deps (curl is useful for healthchecks/debug)
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl jq zip unzip azure-cli\
    && rm -rf /var/lib/apt/lists/*

# Create venv + upgrade tooling
RUN python3 -m venv "$VENV_PATH" \
 && "$VENV_PATH/bin/pip" install --upgrade pip setuptools wheel

# Ensure venv is used by default
ENV PATH="$VENV_PATH/bin:$PATH"

# Install Python deps (cache-friendly)# Install python deps in the golden image for cachin# Install python deps in the golden image for caching
COPY src/requirements.txt /app/src/requirements.txt
RUN pip install -r /app/src/requirements.txt