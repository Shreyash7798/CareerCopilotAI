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

BODY="$(curl -fsS --connect-timeout 5 --max-time 10 "$HEALTH_URL" 2>/dev/null || true)"

if [[ -n "$BODY" ]]; then
  # App responds — but is it running the deployed code? A process that
  # survived a deploy serves the new REVISION file with old code.
  DEPLOYED="$(cat "$APP_DIR/REVISION" 2>/dev/null || true)"
  RUNTIME="$(echo "$BODY" | grep -o '"runtime_revision":"[^"]*"' | cut -d'"' -f4 || true)"
  if [[ -z "$RUNTIME" && -n "$DEPLOYED" ]]; then
    # Old build (no runtime_revision field) still running after a deploy.
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Stale process detected (no runtime_revision; deployed $DEPLOYED) — forcing restart" >>"$LOG"
  elif [[ -n "$RUNTIME" && -n "$DEPLOYED" && "$RUNTIME" != "$DEPLOYED" ]]; then
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Stale process detected (running $RUNTIME, deployed $DEPLOYED) — forcing restart" >>"$LOG"
  else
    exit 0
  fi
  # Fall through to the restart logic below.
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
  sleep 3
  # If a stray non-systemd process still holds the port, kill it hard.
  if ! curl -fsS --connect-timeout 3 "$HEALTH_URL" >/dev/null 2>&1; then
    pkill -9 -f "$APP_DIR/.venv/bin/python run.py" 2>/dev/null || true
    pkill -9 -f "uvicorn" 2>/dev/null || true
    sudo systemctl restart "$SERVICE" 2>/dev/null || true
  fi
else
  cd "$APP_DIR"
  pkill -9 -f "$APP_DIR/.venv/bin/python run.py" 2>/dev/null || true
  # shellcheck disable=SC1091
  [[ -x .venv/bin/python ]] && nohup .venv/bin/python run.py >>"$LOG" 2>&1 &
fi

sleep 5
if curl -fsS --connect-timeout 5 "$HEALTH_URL" >/dev/null 2>&1; then
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Recovered" >>"$LOG"
else
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Still down after restart — run: bash $APP_DIR/scripts/recover-free-tier-vm.sh" >>"$LOG"
fi
