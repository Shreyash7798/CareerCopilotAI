#!/usr/bin/env bash
# Deploy latest main to this host. Safe to run manually, from cron, or via GitHub Actions.
set -euo pipefail

APP_DIR="${CAREERCOPILOT_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
BRANCH="${CAREERCOPILOT_BRANCH:-main}"
SERVICE="${CAREERCOPILOT_SERVICE:-careercopilot}"
VENV="${CAREERCOPILOT_VENV:-$APP_DIR/.venv}"
HEALTH_URL="${CAREERCOPILOT_HEALTH_URL:-http://127.0.0.1:8000/api/version}"
HEALTH_RETRIES="${CAREERCOPILOT_HEALTH_RETRIES:-15}"
HEALTH_INTERVAL="${CAREERCOPILOT_HEALTH_INTERVAL:-2}"

log() { echo "[deploy] $*"; }

wait_for_service() {
  local attempt=1
  while (( attempt <= HEALTH_RETRIES )); do
    if curl -fsS "$HEALTH_URL" 2>/dev/null; then
      return 0
    fi
    sleep "$HEALTH_INTERVAL"
    (( attempt++ )) || true
  done
  return 1
}

show_service_logs() {
  if systemctl list-unit-files "${SERVICE}.service" 2>/dev/null | grep -q "$SERVICE"; then
    if systemctl is-active --quiet "$SERVICE" 2>/dev/null; then
      log "Service unit is active but app not responding — recent logs:"
    else
      log "Service unit is NOT active — recent logs:"
    fi
    sudo systemctl status "$SERVICE" --no-pager -l 2>/dev/null | tail -25 || true
    sudo journalctl -u "$SERVICE" -n 40 --no-pager 2>/dev/null || true
  fi
}

cd "$APP_DIR"

if [[ ! -d .git ]]; then
  log "ERROR: $APP_DIR is not a git repository"
  exit 1
fi

BEFORE="$(git rev-parse HEAD 2>/dev/null || echo none)"
log "Fetching origin/$BRANCH (was $BEFORE)"
git fetch origin "$BRANCH"
git reset --hard "origin/$BRANCH"
AFTER="$(git rev-parse --short HEAD)"
echo "$AFTER" > "$APP_DIR/REVISION"
log "Now at $AFTER (wrote REVISION)"

if [[ ! -x "$VENV/bin/python" ]]; then
  log "Creating virtualenv at $VENV"
  python3 -m venv "$VENV"
fi

# shellcheck disable=SC1091
source "$VENV/bin/activate"
pip install -q -r requirements.txt

SETTINGS="$APP_DIR/config/settings.yaml"
if [[ -f "$SETTINGS" ]]; then
  if ! "$VENV/bin/python" -c "import yaml; yaml.safe_load(open('$SETTINGS'))" 2>/dev/null; then
    log "ERROR: $SETTINGS has invalid YAML — fix indentation/quotes before restarting"
    exit 1
  fi
fi

if command -v playwright >/dev/null 2>&1 || [[ -x "$VENV/bin/playwright" ]]; then
  "$VENV/bin/playwright" install chromium 2>/dev/null || log "playwright chromium skipped (optional)"
fi

if [[ -x "$APP_DIR/scripts/setup-crawl4ai.sh" ]]; then
  bash "$APP_DIR/scripts/setup-crawl4ai.sh" || log "Crawl4AI setup skipped or failed (optional)"
fi

if systemctl is-active --quiet "$SERVICE" 2>/dev/null; then
  log "Restarting systemd service $SERVICE"
  sudo systemctl restart "$SERVICE"
elif systemctl list-unit-files "$SERVICE.service" 2>/dev/null | grep -q "$SERVICE"; then
  log "Starting systemd service $SERVICE"
  sudo systemctl start "$SERVICE"
else
  log "No systemd unit $SERVICE — reload manually if needed"
fi

UNIT="/etc/systemd/system/${SERVICE}.service"
if [[ -f "$UNIT" ]] && grep -q 'Restart=on-failure' "$UNIT" 2>/dev/null; then
  log "Upgrading systemd unit to Restart=always"
  sudo sed -i 's/Restart=on-failure/Restart=always/' "$UNIT"
  sudo sed -i 's/RestartSec=5/RestartSec=10/' "$UNIT" 2>/dev/null || true
  sudo systemctl daemon-reload
fi

log "Waiting for $HEALTH_URL (up to $((HEALTH_RETRIES * HEALTH_INTERVAL))s)..."
if VERSION="$(wait_for_service)"; then
  log "Live version: $VERSION"
else
  log "WARN: Service not responding — attempting one more restart"
  if systemctl list-unit-files "${SERVICE}.service" 2>/dev/null | grep -q "$SERVICE"; then
    sudo systemctl restart "$SERVICE" 2>/dev/null || true
    sleep "$HEALTH_INTERVAL"
    if VERSION="$(wait_for_service)"; then
      log "Live version after retry: $VERSION"
    fi
  fi
fi

if [[ -z "${VERSION:-}" ]]; then
  log "ERROR: Service not responding on :8000 after $((HEALTH_RETRIES * HEALTH_INTERVAL))s"
  show_service_logs
  log "Deploy pulled $AFTER but health check failed — see logs above"
  exit 1
fi

log "Deploy complete: $AFTER"
