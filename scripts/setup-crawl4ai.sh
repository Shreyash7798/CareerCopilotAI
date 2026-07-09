#!/usr/bin/env bash
# Start optional Crawl4AI Docker sidecar (safe to run from deploy.sh / cron).
set -euo pipefail

APP_DIR="${CAREERCOPILOT_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
COMPOSE_FILE="$APP_DIR/scripts/docker-compose.crawl4ai.yml"
HEALTH_URL="${CRAWL4AI_HEALTH_URL:-http://127.0.0.1:11235/health}"

log() { echo "[crawl4ai] $*"; }

total_mb() {
  free -m 2>/dev/null | awk '/^Mem:/{print $2}' || echo 0
}

MIN_RAM_MB="${CAREERCOPILOT_MIN_CRAWL4AI_RAM_MB:-1800}"
RAM="$(total_mb)"
if [[ "$RAM" -gt 0 && "$RAM" -lt "$MIN_RAM_MB" ]]; then
  log "skip: ${RAM}MB RAM < ${MIN_RAM_MB}MB — use Playwright in app venv (see scripts/recover-free-tier-vm.sh)"
  exit 0
fi

if ! command -v docker >/dev/null 2>&1; then
  log "docker not installed — skip (install Docker on VM for JS career pages)"
  exit 0
fi

if ! docker compose version >/dev/null 2>&1; then
  log "docker compose not available — skip"
  exit 0
fi

if [[ ! -f "$COMPOSE_FILE" ]]; then
  log "compose file missing — skip"
  exit 0
fi

log "Starting Crawl4AI sidecar (if not already running)"
docker compose -f "$COMPOSE_FILE" up -d 2>&1 || {
  log "WARN: could not start Crawl4AI (VM may need 2GB+ RAM)"
  exit 0
}

for i in $(seq 1 20); do
  if curl -fsS "$HEALTH_URL" >/dev/null 2>&1; then
    log "Crawl4AI healthy at $HEALTH_URL"
    if [[ -x "$APP_DIR/.venv/bin/python" ]]; then
      "$APP_DIR/.venv/bin/python" "$APP_DIR/scripts/enable-crawl4ai-settings.py" || true
    else
      python3 "$APP_DIR/scripts/enable-crawl4ai-settings.py" || true
    fi
    exit 0
  fi
  sleep 3
done

log "WARN: Crawl4AI started but health check timed out"
