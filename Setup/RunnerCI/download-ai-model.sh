#!/usr/bin/env bash
set -euo pipefail

MODEL_DIR="${MODEL_DIR:-/app/models}"
MODEL_FILE="${MODEL_FILE:-automationhub-agent.gguf}"
MODEL_PATH="${MODEL_DIR}/${MODEL_FILE}"

mkdir -p "${MODEL_DIR}"

verify_sha256() {
  local path="$1"
  if [[ -n "${AI_MODEL_SHA256:-}" ]]; then
    echo "Verifying AI model SHA-256: ${path}"
    echo "${AI_MODEL_SHA256}  ${path}" | sha256sum -c -
  fi
}

if [[ -s "${MODEL_PATH}" ]]; then
  verify_sha256 "${MODEL_PATH}"
  chmod 0644 "${MODEL_PATH}"
  echo "AI model already exists and is ready: ${MODEL_PATH}"
  exit 0
fi

if [[ -z "${AI_MODEL_DOWNLOAD_URL:-}" ]]; then
  echo "AI_MODEL_DOWNLOAD_URL is not set and ${MODEL_PATH} does not exist."
  echo "Build will continue without embedded model only if AI_MODEL_REQUIRED=false."
  exit 10
fi

echo "Downloading AI model..."
curl -L --fail --retry 5 --retry-delay 10 \
  -o "${MODEL_PATH}.tmp" \
  "${AI_MODEL_DOWNLOAD_URL}"

verify_sha256 "${MODEL_PATH}.tmp"

mv "${MODEL_PATH}.tmp" "${MODEL_PATH}"
chmod 0644 "${MODEL_PATH}"

echo "AI model ready: ${MODEL_PATH}"
