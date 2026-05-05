#!/usr/bin/env bash
# cycle31-r3 chain: wait for v8 to finish, then restart server with all
# cycle31-r3 fixes loaded, then fire ship-readiness bench.
set -e
cd /Users/charlieyan/Downloads/castor-advisories/market-research-prototype

# 1. Wait for v8 to finish
echo "[$(date +%H:%M:%S)] waiting for bench_v8 to finish..."
while pgrep -f "run_all.*bench_v8" > /dev/null; do sleep 30; done
echo "[$(date +%H:%M:%S)] v8 done."

# 2. Restart server (loads Bug G + Bug H + dashboard + alignment harness)
lsof -ti:8765 | xargs -r kill -9 2>/dev/null || true
sleep 2
nohup .venv/bin/uvicorn api:app --host 127.0.0.1 --port 8765 > /tmp/mrp.log 2>&1 &
echo "[$(date +%H:%M:%S)] server restarted, PID $!"
sleep 6

# Verify it's up
curl -s http://127.0.0.1:8765/healthz || { echo "server not healthy"; exit 1; }
echo

# 3. Fire ship-readiness bench: 3 NEW real ventures + 1 fix-validation case
set -a; source .env; set +a
nohup .venv/bin/python -m benchmarks.run_all \
  --cases tier3_vanta_real,tier3_glean_real,tier3_bland_real,tier3_speedline_real \
  --samples 1 --parallel 1 --with-prose \
  --out /tmp/bench_ship.json > /tmp/bench_ship.log 2>&1 &
echo "[$(date +%H:%M:%S)] ship-readiness bench fired, PID $!"
echo "Cases: tier3_vanta_real, tier3_glean_real, tier3_bland_real, tier3_speedline_real"
echo "ETA: ~25min"
