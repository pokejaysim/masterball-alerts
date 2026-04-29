#!/bin/bash
# Create/update the local Mac Mini virtual environment.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="${MASTERBALL_VENV:-$SCRIPT_DIR/../pokemon-monitor-env}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

cd "$SCRIPT_DIR"

echo "Creating virtual environment: $VENV_DIR"
"$PYTHON_BIN" -m venv "$VENV_DIR"

echo "Installing Python dependencies..."
"$VENV_DIR/bin/python" -m pip install --upgrade pip
"$VENV_DIR/bin/python" -m pip install -r requirements.txt

echo "Installing Playwright Chromium..."
"$VENV_DIR/bin/python" -m playwright install chromium

echo "Initializing database..."
"$VENV_DIR/bin/python" database.py

echo "Running doctor..."
"$VENV_DIR/bin/python" doctor.py

echo "Done. Use ./control.sh start to run the monitor."
