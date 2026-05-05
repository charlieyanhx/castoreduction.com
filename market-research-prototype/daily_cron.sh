#!/usr/bin/env bash
# Daily quality check wrapper for cron. Logs output to out/daily_cron.log
# Install: crontab -e → add:
#   0 8 * * * /Users/charlieyan/Downloads/castor-advisories/market-research-prototype/daily_cron.sh
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"
LOG="$DIR/out/daily_cron.log"
mkdir -p "$DIR/out"

{
  echo ""
  echo "=========================================="
  echo "Daily check — $(date)"
  echo "=========================================="
  .venv/bin/python daily_check.py
  echo "exit code: $?"
} >> "$LOG" 2>&1
