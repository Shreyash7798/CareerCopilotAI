#!/usr/bin/env bash
# Deploy latest main to this host. Safe to run manually, from cron, or via GitHub Actions.
set -euo pipefail

APP_DIR="${CAREERCOPILOT_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
BRANCH="${CAREERCOPILOT_BRANCH:-main}"
SERVICE="${CAREERCOPILOT_SERVICE:-careercopilot}"
VENV="${CAREERCOPILOT_VENV:-$APP_DIR/.venv}"

log() { echo "[deploy] $*"; }

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
log "Now at $AFTER"

if [[ ! -x "$VENV/bin/python" ]]; then
  log "Creating virtualenv at $VENV"
  python3 -m venv "$VENV"
fi

# shellcheck disable=SC1091
source "$VENV/bin/activate"
pip install -q -r requirements.txt

if command -v playwright >/dev/null 2>&1 || [[ -x "$VENV/bin/playwright" ]]; then
  "$VENV/bin/playwright" install chromium 2>/dev/null || log "playwright chromium skipped (optional)"
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

sleep 2
if curl -fsS "http://127.0.0.1:8000/api/version" >/dev/null 2>&1; then
  VERSION="$(curl -fsS "http://127.0.0.1:8000/api/version")"
  log "Live version: $VERSION"
else
  log "Service not responding on :8000 yet (may still be starting)"
fi

log "Deploy complete: $AFTER"
