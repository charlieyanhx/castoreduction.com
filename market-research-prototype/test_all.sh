#!/usr/bin/env bash
# Run every test suite. Exit non-zero if any fail.
set -e
cd "$(dirname "$0")"

echo "▶ infra tests"
.venv/bin/python test_infra.py

echo ""
echo "▶ integration tests"
.venv/bin/python test_integration.py

echo ""
echo "▶ api tests"
.venv/bin/python test_api.py

echo ""
echo "✓ all tests passed"
