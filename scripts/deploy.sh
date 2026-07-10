#!/usr/bin/env bash
# Deploy latest main to this host. Safe to run manually, from cron, or via GitHub Actions.
set -euo pipefail

APP_DIR="${CAREERCOPILOT_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
BRANCH="${CAREERCOPILOT_BRANCH:-main}"
SERVICE="${CAREERCOPILOT_SERVICE:-careercopilot}"
VENV="${CAREERCOPILOT_VENV:-$APP_DIR/.venv}"
HEALTH_URL="${CAREERCOPILOT_HEALTH_URL:-http://127.0.0.1:8000/api/version}"
HEALTH_RETRIES="${CAREERCOPILOT_HEALTH_RETRIES:-30}"
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

kill_stale_workers() {
  log "Stopping stale discovery / browser workers"
  pkill -f "${APP_DIR}/run.py --once" 2>/dev/null || true
  pkill -f "playwright.*chromium" 2>/dev/null || true
  pkill -f "chromium.*--headless" 2>/dev/null || true
  rm -f "$APP_DIR/data/.discovery.lock" 2>/dev/null || true
}

free_tier_hardening() {
  local ram_mb="$1"
  if [[ "$ram_mb" -le 0 || "$ram_mb" -ge 1800 ]]; then
    return
  fi
  log "Free-tier hardening (${ram_mb}MB RAM)"
  pkill -f "docker compose.*crawl4ai" 2>/dev/null || true
  pkill -f "docker build.*crawl4ai" 2>/dev/null || true
  if command -v docker >/dev/null 2>&1; then
    docker stop careercopilot-crawl4ai 2>/dev/null || true
    docker rm careercopilot-crawl4ai 2>/dev/null || true
    for f in "$APP_DIR/scripts/docker-compose.crawl4ai.yml" "$APP_DIR/scripts/docker-compose.crawl4ai-lowmem.yml"; do
      if [[ -f "$f" ]] && docker compose version >/dev/null 2>&1; then
        docker compose -f "$f" down --remove-orphans 2>/dev/null || true
      fi
    done
  fi
  if ! swapon --show 2>/dev/null | grep -q .; then
    if [[ ! -f /swapfile ]]; then
      log "Creating 1 GB swap file"
      sudo fallocate -l 1G /swapfile 2>/dev/null || sudo dd if=/dev/zero of=/swapfile bs=1M count=1024 status=none
      sudo chmod 600 /swapfile
      sudo mkswap /swapfile
      grep -q '^/swapfile ' /etc/fstab 2>/dev/null || echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
    fi
    sudo swapon /swapfile 2>/dev/null || true
  fi
}

cd "$APP_DIR"

if [[ ! -d .git ]]; then
  log "ERROR: $APP_DIR is not a git repository"
  exit 1
fi

BEFORE="$(git rev-parse HEAD 2>/dev/null || echo none)"
if [[ -n "${CAREERCOPILOT_GIT_REF:-}" ]]; then
  log "Checking out $CAREERCOPILOT_GIT_REF (was $BEFORE)"
  git fetch origin
  git reset --hard "$CAREERCOPILOT_GIT_REF"
else
  log "Fetching origin/$BRANCH (was $BEFORE)"
  git fetch origin "$BRANCH"
  git reset --hard "origin/$BRANCH"
fi
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

RAM_MB="$(free -m 2>/dev/null | awk '/^Mem:/{print $2}' || echo 0)"
free_tier_hardening "$RAM_MB"

if command -v playwright >/dev/null 2>&1 || [[ -x "$VENV/bin/playwright" ]]; then
  bash "$APP_DIR/scripts/install-playwright-deps.sh" 2>/dev/null || log "playwright deps skipped"
fi

if [[ "$RAM_MB" -gt 0 && "$RAM_MB" -lt 1800 ]]; then
  log "Free-tier VM (${RAM_MB}MB): skipping Crawl4AI Docker"
  "$VENV/bin/python" "$APP_DIR/scripts/disable-crawl4ai-settings.py" 2>/dev/null || true
elif [[ -x "$APP_DIR/scripts/setup-crawl4ai.sh" ]]; then
  bash "$APP_DIR/scripts/setup-crawl4ai.sh" || log "Crawl4AI setup skipped or failed (optional)"
fi

kill_stale_workers

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
if [[ -f "$UNIT" ]]; then
  if grep -q 'Restart=on-failure' "$UNIT" 2>/dev/null; then
    log "Upgrading systemd unit to Restart=always"
    sudo sed -i 's/Restart=on-failure/Restart=always/' "$UNIT"
    sudo sed -i 's/RestartSec=5/RestartSec=10/' "$UNIT" 2>/dev/null || true
    sudo systemctl daemon-reload
  fi
  if ! grep -q 'StartLimitBurst' "$UNIT" 2>/dev/null; then
    log "Adding systemd start limits (recover from crash loops)"
    sudo sed -i '/\[Service\]/a StartLimitIntervalSec=300\nStartLimitBurst=10' "$UNIT"
    sudo systemctl daemon-reload
  fi
fi

log "Waiting for $HEALTH_URL (up to $((HEALTH_RETRIES * HEALTH_INTERVAL))s)..."
if VERSION="$(wait_for_service)"; then
  log "Live version: $VERSION"
  # Verify the RUNNING process picked up the new code — a survivor process
  # serves the fresh REVISION file while executing stale code.
  RUNTIME="$(echo "$VERSION" | grep -o '"runtime_revision":"[^"]*"' | cut -d'"' -f4 || true)"
  if [[ -n "$RUNTIME" && "$RUNTIME" != "$AFTER" ]]; then
    log "WARN: runtime=$RUNTIME but deployed=$AFTER — killing stale process"
    pkill -9 -f "$APP_DIR/.venv/bin/python run.py" 2>/dev/null || true
    sudo systemctl restart "$SERVICE" 2>/dev/null || true
    sleep "$HEALTH_INTERVAL"
    VERSION="$(wait_for_service || true)"
    log "After forced restart: ${VERSION:-no response}"
  fi
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

chmod +x "$APP_DIR/scripts/health-watchdog.sh" 2>/dev/null || true
chmod +x "$APP_DIR/scripts/backup-db.sh" 2>/dev/null || true
WATCHDOG_LINE="* * * * * bash $APP_DIR/scripts/health-watchdog.sh >> $APP_DIR/data/watchdog.log 2>&1"
if ! crontab -l 2>/dev/null | grep -qF "health-watchdog.sh"; then
  log "Installing health watchdog (auto-recover from 502)"
  (crontab -l 2>/dev/null || true; echo "$WATCHDOG_LINE") | crontab -
fi
BACKUP_LINE="15 3 * * * CAREERCOPILOT_DIR=$APP_DIR bash $APP_DIR/scripts/backup-db.sh >> $APP_DIR/data/backup.log 2>&1"
if ! crontab -l 2>/dev/null | grep -qF "backup-db.sh"; then
  log "Installing nightly database backup (03:15)"
  (crontab -l 2>/dev/null || true; echo "$BACKUP_LINE") | crontab -
fi
