#!/usr/bin/env bash
# One-shot setup script. Safe to re-run.
set -euo pipefail

cd "$(dirname "$0")"

PYTHON=${PYTHON:-python3.12}
if ! command -v "$PYTHON" >/dev/null 2>&1; then
  PYTHON=python3
fi

echo "▶ using $($PYTHON --version 2>&1) at $(which $PYTHON)"

if [ ! -d .venv ]; then
  echo "▶ creating venv"
  "$PYTHON" -m venv .venv
fi

echo "▶ upgrading pip + installing requirements"
.venv/bin/pip install -q -U pip
.venv/bin/pip install -q -r requirements.txt

if [ ! -f .env ]; then
  cp .env.example .env
  echo "▶ created .env — edit it and set ANTHROPIC_API_KEY"
fi

echo ""
echo "✓ install complete"
echo ""
echo "next steps:"
echo "  edit .env and set ANTHROPIC_API_KEY"
echo "  .venv/bin/python test_infra.py          # verify core (offline)"
echo "  .venv/bin/python test_integration.py    # verify pipeline (offline)"
echo "  .venv/bin/python test_api.py            # verify API (offline)"
echo ""
echo "run as CLI:"
echo "  .venv/bin/python cli.py discover 'protein bars'"
echo ""
echo "run as web service:"
echo "  .venv/bin/uvicorn api:app --reload --port 8000"
echo "  → open http://localhost:8000"
