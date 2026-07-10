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

if curl -fsS --connect-timeout 5 --max-time 10 "$HEALTH_URL" >/dev/null 2>&1; then
  exit 0
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
else
  cd "$APP_DIR"
  # shellcheck disable=SC1091
  [[ -x .venv/bin/python ]] && nohup .venv/bin/python run.py >>"$LOG" 2>&1 &
fi

sleep 5
if curl -fsS --connect-timeout 5 "$HEALTH_URL" >/dev/null 2>&1; then
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Recovered" >>"$LOG"
else
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Still down after restart — run: bash $APP_DIR/scripts/recover-free-tier-vm.sh" >>"$LOG"
fi
