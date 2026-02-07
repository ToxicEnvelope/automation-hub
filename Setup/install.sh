#!/usr/bin/env bash
set -euo pipefail

REQ_FILE="${REQ_FILE:-/app/src/requirements.txt}"

# Create venv + upgrade tooling
RUN python -m venv "$VENV_PATH" \
 && "$VENV_PATH/bin/pip" install --upgrade pip setuptools wheel

echo "Running: pip install -r ${REQ_FILE}"
"$VENV_PATH/bin/python" -m pip install --no-cache-dir -r "${REQ_FILE}"
echo "Installation Complete!"
