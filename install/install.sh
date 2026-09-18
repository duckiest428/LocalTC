#!/usr/bin/env bash
# LocalTC for development on macOS/Linux (the sim itself runs on Windows: install/install.ps1).
#   ./install/install.sh            Python environment, Whisper, Ollama model, downloaded models
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY="$(command -v python3.12 || command -v python3.13 || command -v python3.11 || true)"
[ -n "$PY" ] || { echo "Python 3.11-3.13 is needed (e.g. brew install python@3.12)"; exit 1; }
[ -x .venv/bin/python ] || "$PY" -m venv .venv
.venv/bin/python -m pip install --upgrade --quiet pip
.venv/bin/python -m pip install --quiet -e ".[dev]"
if ! command -v ollama >/dev/null; then
  echo "Ollama isn't installed: https://ollama.com/download (or: brew install ollama). Skipping the language model."
  SETUP_ARGS=(--no-llm)
else
  SETUP_ARGS=()
fi
PYTHONPATH=src .venv/bin/localtc setup "${SETUP_ARGS[@]+"${SETUP_ARGS[@]}"}"
echo "Done. Try: PYTHONPATH=src .venv/bin/localtc voice test"
