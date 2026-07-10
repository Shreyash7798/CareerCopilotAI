#!/usr/bin/env bash
# Restart CareerCopilot if the app stops responding (fixes nginx 502 Bad Gateway).
# Installed by bootstrap-oci.sh — runs every minute via cron.
set -euo pipefail

APP_DIR="${CAREERCOPILOT_DIR:-$HOME/CareerCopilotAI}"
SERVICE="${CAREERCOPILOT_SERVICE:-careercopilot}"
HEALTH_URL="${CAREERCOPILOT_HEALTH_URL:-http://127.0.0.1:8000/api/version}"
LOG="$APP_DIR/data/watchdog.log"
STAMP_FILE="$APP_DIR/data/.watchdog-restart"

mkdir -p "$APP_DIR/data"

PORT="$(echo "$HEALTH_URL" | grep -oE ':[0-9]+' | head -1 | tr -d ':')"
PORT="${PORT:-8000}"

BODY="$(curl -fsS --connect-timeout 5 --max-time 10 "$HEALTH_URL" 2>/dev/null || true)"

STALE=0
if [[ -n "$BODY" ]]; then
  # App responds — but is it running the deployed code? A survivor process
  # (stray nohup holding the port) serves the new REVISION file with old code
  # while systemd's fresh instance crash-loops on 'address already in use'.
  DEPLOYED="$(cat "$APP_DIR/REVISION" 2>/dev/null || true)"
  RUNTIME="$(echo "$BODY" | grep -o '"runtime_revision":"[^"]*"' | cut -d'"' -f4 || true)"
  if [[ -z "$RUNTIME" && -n "$DEPLOYED" ]]; then
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Stale process (no runtime_revision; deployed $DEPLOYED) — killing port $PORT holder" >>"$LOG"
    STALE=1
  elif [[ -n "$RUNTIME" && -n "$DEPLOYED" && "$RUNTIME" != "$DEPLOYED" ]]; then
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Stale process (running $RUNTIME, deployed $DEPLOYED) — killing port $PORT holder" >>"$LOG"
    STALE=1
  else
    exit 0
  fi
fi

if [[ "$STALE" == "1" ]]; then
  # Kill whatever holds the port — stray processes survive systemctl restart.
  fuser -k -9 "${PORT}/tcp" 2>/dev/null || true
  pkill -9 -f "run.py" 2>/dev/null || true
  sleep 2
fi

# Avoid restart storms: at most once per 2 minutes.
if [[ -f "$STAMP_FILE" ]]; then
  last=$(stat -c %Y "$STAMP_FILE" 2>/dev/null || echo 0)
  now=$(date +%s)
  if (( now - last < 120 )); then
    exit 0
  fi
fi

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Health check failed — restarting $SERVICE" >>"$LOG"
touch "$STAMP_FILE"

pkill -f "${APP_DIR}/run.py --once" 2>/dev/null || true
rm -f "$APP_DIR/data/.discovery.lock" 2>/dev/null || true

if systemctl list-unit-files "${SERVICE}.service" 2>/dev/null | grep -q "$SERVICE"; then
  sudo systemctl restart "$SERVICE" 2>/dev/null || true
  sleep 5
  # If something still holds the port with stale/no code, kill hard and retry.
  BODY2="$(curl -fsS --connect-timeout 3 "$HEALTH_URL" 2>/dev/null || true)"
  RUNTIME2="$(echo "$BODY2" | grep -o '"runtime_revision":"[^"]*"' | cut -d'"' -f4 || true)"
  DEPLOYED2="$(cat "$APP_DIR/REVISION" 2>/dev/null || true)"
  if [[ -z "$BODY2" || ( -n "$DEPLOYED2" && "$RUNTIME2" != "$DEPLOYED2" ) ]]; then
    fuser -k -9 "${PORT}/tcp" 2>/dev/null || true
    pkill -9 -f "run.py" 2>/dev/null || true
    sleep 2
    sudo systemctl restart "$SERVICE" 2>/dev/null || true
  fi
else
  cd "$APP_DIR"
  fuser -k -9 "${PORT}/tcp" 2>/dev/null || true
  pkill -9 -f "run.py" 2>/dev/null || true
  # shellcheck disable=SC1091
  [[ -x .venv/bin/python ]] && nohup .venv/bin/python run.py >>"$LOG" 2>&1 &
fi

sleep 5
if curl -fsS --connect-timeout 5 "$HEALTH_URL" >/dev/null 2>&1; then
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Recovered" >>"$LOG"
else
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Still down after restart — run: bash $APP_DIR/scripts/recover-free-tier-vm.sh" >>"$LOG"
fi
