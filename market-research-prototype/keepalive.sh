#!/usr/bin/env bash
# Castor keepalive watchdog. Survives:
#   - cloudflared crashes      (auto-restart loop)
#   - uvicorn crashes          (auto-restart loop)
#   - Mac going to sleep       (caffeinate while running)
#   - Network blips            (cloudflared self-recovers)
# Does NOT survive:
#   - Hard reboot (re-run the script after)
#   - Closing this terminal/session (run with nohup or in a tmux/screen)
#
# Usage:
#   nohup bash keepalive.sh > /tmp/castor_keepalive.log 2>&1 &
#
# Read current public URL:
#   cat ~/.castor_url.txt
#
# Stop it cleanly:
#   pkill -f "keepalive.sh"; pkill -f cloudflared; pkill -f "uvicorn api:app"

set -u
PROJECT_DIR="/Users/charlieyan/Downloads/castor-advisories/market-research-prototype"
URL_FILE="$HOME/.castor_url.txt"
LOG_FILE="/tmp/castor_keepalive.log"
SERVER_LOG="/tmp/castor_server.log"
TUNNEL_LOG="/tmp/castor_tunnel.log"

cd "$PROJECT_DIR" || { echo "[$(date)] cannot cd to $PROJECT_DIR"; exit 1; }
echo "[$(date)] starting keepalive in $PROJECT_DIR"

# 1. Caffeinate the Mac so it doesn't sleep while we're running
#    -d: prevent display sleep, -i: prevent idle sleep, -m: prevent disk sleep, -s: prevent system sleep on AC
caffeinate -dims &
CAFFEINATE_PID=$!
echo "[$(date)] caffeinate PID $CAFFEINATE_PID"

# Cleanup on exit
trap 'echo "[$(date)] keepalive shutting down"; kill $CAFFEINATE_PID 2>/dev/null; pkill -f cloudflared 2>/dev/null; pkill -f "uvicorn api:app" 2>/dev/null; exit 0' INT TERM

# 2. Start (and watchdog) the uvicorn server
start_server() {
    if curl -sf http://127.0.0.1:8765/healthz > /dev/null 2>&1; then
        return 0  # already running
    fi
    echo "[$(date)] starting uvicorn..."
    nohup .venv/bin/uvicorn api:app --host 127.0.0.1 --port 8765 > "$SERVER_LOG" 2>&1 &
    sleep 6
    curl -sf http://127.0.0.1:8765/healthz > /dev/null 2>&1
}

# 3. Start (and watchdog) cloudflared tunnel
start_tunnel() {
    pkill -f cloudflared 2>/dev/null
    sleep 2
    echo "[$(date)] starting cloudflared..."
    nohup cloudflared tunnel --url http://localhost:8765 > "$TUNNEL_LOG" 2>&1 &
    # Wait up to 20s for cloudflared to print its URL
    for i in $(seq 1 20); do
        URL=$(grep -oE "https://[a-z0-9-]+\.trycloudflare\.com" "$TUNNEL_LOG" | head -1)
        if [ -n "$URL" ]; then
            echo "$URL" > "$URL_FILE"
            echo "[$(date)] tunnel URL: $URL"
            return 0
        fi
        sleep 1
    done
    echo "[$(date)] tunnel did not produce URL within 20s"
    return 1
}

# 4. Main watchdog loop — every 60s, verify both pieces are healthy
LAST_URL_CHECK=0
while true; do
    # Server check
    if ! curl -sf http://127.0.0.1:8765/healthz > /dev/null 2>&1; then
        echo "[$(date)] server unhealthy, restarting"
        pkill -f "uvicorn api:app" 2>/dev/null
        sleep 2
        start_server
    fi
    # Tunnel check — if URL file is missing OR the URL doesn't return 200
    NOW=$(date +%s)
    if [ ! -f "$URL_FILE" ] || [ $((NOW - LAST_URL_CHECK)) -gt 60 ]; then
        URL=$(cat "$URL_FILE" 2>/dev/null || echo "")
        if [ -z "$URL" ] || ! curl -sf -m 10 "$URL/healthz" > /dev/null 2>&1; then
            echo "[$(date)] tunnel unhealthy ($URL), restarting"
            start_tunnel
        fi
        LAST_URL_CHECK=$NOW
    fi
    sleep 30
done
