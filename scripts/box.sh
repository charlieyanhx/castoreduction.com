#!/bin/bash
# Is this machine fit to run a real report right now?
#
# Answers in one screen what otherwise takes six commands: how loaded the box is,
# how much swap is left, what is eating the CPU, whether the server and tunnel are
# up, whether the running server predates the code on disk, and what is in flight.
# Ends with a verdict: fit, marginal, or unfit.
#
#     bash scripts/box.sh            report and exit 0 (or 1 when unfit)
#     bash scripts/box.sh --quiet    the verdict line only
#
# The server-is-stale check is the one that bites. launchd keeps the old process
# alive across a commit, so a fix that is green in the tests can be absent from the
# thing you are testing against. Restart with:
#
#     launchctl kickstart -k gui/$(id -u)/com.castor.server

set -u
cd "$(dirname "$0")/.." || exit 2

# ps prints elapsed time as [[dd-]hh:]mm:ss. Read it back as seconds. This is the
# one silent-wrong path in the script: a parser that returns too small a number
# makes a stale server look current, which is the exact mistake box.sh exists to
# catch. scripts/box.sh --parse-etime <str> prints the seconds, and the test drives
# that flag rather than a copy of the arithmetic.
etime_seconds() {
  printf '%s' "$1" | tr -d ' ' | awk -F'[-:]' '{
    if (NF==4) print $1*86400+$2*3600+$3*60+$4;
    else if (NF==3) print $1*3600+$2*60+$3;
    else print $1*60+$2 }'
}

if [ "${1:-}" = "--parse-etime" ]; then
  etime_seconds "${2:-}"
  exit 0
fi

QUIET=0
[ "${1:-}" = "--quiet" ] && QUIET=1
say() { [ "$QUIET" = 1 ] || printf '%s\n' "$*"; }
head_() { [ "$QUIET" = 1 ] || printf '\n%s\n' "$*"; }

WORST=0            # 0 fit, 1 marginal, 2 unfit
REASONS=()
note() {           # note <level> <reason>
  [ "$1" -gt "$WORST" ] && WORST="$1"
  [ "$1" -gt 0 ] && REASONS+=("$2")
  return 0
}

# ---- load ----------------------------------------------------------------
CORES=$(sysctl -n hw.ncpu)
LOAD1=$(sysctl -n vm.loadavg | awk '{print $2}')
PER_CORE=$(awk -v l="$LOAD1" -v c="$CORES" 'BEGIN{printf "%.2f", l/c}')
head_ "load"
say "  $LOAD1 over $CORES cores, $PER_CORE per core"
awk -v p="$PER_CORE" 'BEGIN{exit !(p>2.0)}' && note 2 "load is ${PER_CORE} per core, the box is saturated"
awk -v p="$PER_CORE" 'BEGIN{exit !(p>1.0 && p<=2.0)}' && note 1 "load is ${PER_CORE} per core, runs will drag"

# ---- memory and swap -----------------------------------------------------
SWAP=$(sysctl -n vm.swapusage)
SWAP_FREE=$(printf '%s' "$SWAP" | sed -n 's/.*free = \([0-9.]*\)M.*/\1/p')
SWAP_USED=$(printf '%s' "$SWAP" | sed -n 's/.*used = \([0-9.]*\)M.*/\1/p')
FREE_PCT=$(memory_pressure -Q 2>/dev/null | sed -n 's/.*free percentage: \([0-9]*\)%.*/\1/p')
head_ "memory"
say "  swap ${SWAP_USED:-?}M used, ${SWAP_FREE:-?}M free"
say "  system memory free ${FREE_PCT:-?}%"
if [ -n "$SWAP_FREE" ]; then
  awk -v f="$SWAP_FREE" 'BEGIN{exit !(f<512)}'  && note 2 "only ${SWAP_FREE}M of swap left, this is how the box reboots"
  awk -v f="$SWAP_FREE" 'BEGIN{exit !(f>=512 && f<2048)}' && note 1 "swap is down to ${SWAP_FREE}M"
fi
[ -n "$FREE_PCT" ] && [ "$FREE_PCT" -lt 10 ] && note 2 "system memory is ${FREE_PCT}% free"

# ---- who is eating it ----------------------------------------------------
head_ "top consumers"
ps -eo pcpu,rss,comm,args | sort -k1 -nr | head -4 | while read -r cpu rss _ args; do
  say "  $(printf '%6s' "$cpu")% cpu  $(printf '%6s' $((rss/1024)))M  $(printf '%.90s' "$args")"
done

# ---- server --------------------------------------------------------------
head_ "server"
SRV_PID=$(pgrep -f "uvicorn api:app" | head -1)
if [ -z "$SRV_PID" ]; then
  say "  not running"
  note 2 "the server is not running"
else
  CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 http://127.0.0.1:8765/healthz)
  MS=$(curl -s -o /dev/null -w '%{time_total}' --max-time 15 http://127.0.0.1:8765/healthz)
  say "  pid $SRV_PID, healthz $CODE in ${MS}s"
  [ "$CODE" != "200" ] && note 2 "healthz answered $CODE"
  awk -v t="$MS" 'BEGIN{exit !(t>2.0)}' && note 1 "healthz took ${MS}s, the box is struggling"

  # Does the running process predate the code on disk?
  SECS=$(etime_seconds "$(ps -o etime= -p "$SRV_PID")")
  STARTED=$(( $(date +%s) - SECS ))
  HEAD_TS=$(git log -1 --format=%ct 2>/dev/null || echo 0)
  if [ "$HEAD_TS" -gt "$STARTED" ]; then
    AGE=$(( (HEAD_TS - STARTED) / 60 ))
    say "  started $(date -r "$STARTED" '+%F %H:%M'), HEAD committed $(date -r "$HEAD_TS" '+%F %H:%M')"
    say "  STALE: the running server predates HEAD by ${AGE} minutes"
    note 2 "the running server is older than HEAD, restart it before you trust a run"
  else
    say "  started $(date -r "$STARTED" '+%F %H:%M'), newer than HEAD, code is current"
  fi
fi

# ---- tunnel --------------------------------------------------------------
head_ "tunnel"
if launchctl list 2>/dev/null | grep -q com.castor.tunnel; then
  TCODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 https://app.castor-advisory.com/healthz)
  say "  app.castor-advisory.com answered $TCODE"
  [ "$TCODE" = "530" ] && note 1 "the tunnel returns 530, which it does when the box is overloaded"
  [ "$TCODE" != "200" ] && [ "$TCODE" != "530" ] && note 1 "the tunnel answered $TCODE"
else
  say "  com.castor.tunnel is not loaded"
fi

# ---- work in flight ------------------------------------------------------
head_ "jobs"
if [ -f .jobs.sqlite ]; then
  .venv/bin/python - <<'PY'
import sqlite3
c = sqlite3.connect("file:.jobs.sqlite?mode=ro", uri=True)
rows = dict(c.execute("select state, count(*) from jobs group by state"))
run = rows.get("running", 0) + rows.get("pending", 0)
print(f"  running or pending: {run}")
for state in ("complete", "error"):
    if rows.get(state):
        print(f"  {state}: {rows[state]}")
PY
else
  say "  no job database yet"
fi

# ---- verdict -------------------------------------------------------------
case "$WORST" in
  0) VERDICT="fit: a real run should behave" ;;
  1) VERDICT="marginal: a run will finish but the timings are not worth recording" ;;
  2) VERDICT="unfit: do not start a run" ;;
esac
head_ "verdict"
printf '%s\n' "  $VERDICT"
for r in ${REASONS+"${REASONS[@]}"}; do printf '%s\n' "    $r"; done

[ "$WORST" -ge 2 ] && exit 1
exit 0
