#!/bin/zsh

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

if ! command -v uv >/dev/null 2>&1; then
  echo "Error: uv is not installed or not in PATH."
  echo "Install uv first, then run this file again."
  echo
  read "_input?Press Enter to close..."
  exit 1
fi

echo "Starting backend on http://127.0.0.1:$(uv run python -c 'from config import BACKEND_PORT; print(BACKEND_PORT)') ..."
echo "Press Ctrl+C to stop."
echo

uv run python app.py

echo
echo "Backend stopped."
read "_input?Press Enter to close..."