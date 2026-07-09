#!/usr/bin/env bash
# Install Docker (if needed), start Crawl4AI, enable it in settings.yaml, restart CareerCopilot.
#
# Run on the Oracle VM from your laptop:
#   ssh -i <your-key.pem> ubuntu@161.118.184.228
#   cd ~/CareerCopilotAI && git pull origin main && bash scripts/install-docker-crawl4ai.sh
#
# Requires sudo for Docker install and service restart.
set -euo pipefail

APP_DIR="${CAREERCOPILOT_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
SERVICE="${CAREERCOPILOT_SERVICE:-careercopilot}"
COMPOSE_FILE="${COMPOSE_FILE:-$APP_DIR/scripts/docker-compose.crawl4ai.yml}"
HEALTH_URL="${CRAWL4AI_HEALTH_URL:-http://127.0.0.1:11235/health}"

log() { echo "[install-crawl4ai] $*"; }

need_sudo() {
  [[ "$(id -u)" -ne 0 ]]
}

run_root() {
  if need_sudo; then
    sudo "$@"
  else
    "$@"
  fi
}

docker_cmd() {
  if docker info >/dev/null 2>&1; then
    docker "$@"
  else
    run_root docker "$@"
  fi
}

compose_cmd() {
  if docker compose version >/dev/null 2>&1; then
    if docker info >/dev/null 2>&1; then
      docker compose "$@"
    else
      run_root docker compose "$@"
    fi
  elif command -v docker-compose >/dev/null 2>&1; then
    if docker info >/dev/null 2>&1; then
      docker-compose "$@"
    else
      run_root docker-compose "$@"
    fi
  else
    return 1
  fi
}

pick_compose_file() {
  if [[ -n "${COMPOSE_FILE_OVERRIDE:-}" ]]; then
    COMPOSE_FILE="$COMPOSE_FILE_OVERRIDE"
    return
  fi
  local total_mb
  total_mb="$(free -m 2>/dev/null | awk '/^Mem:/{print $2}' || echo 0)"
  if [[ "$total_mb" -gt 0 && "$total_mb" -lt 1800 ]]; then
    log "ERROR: ${total_mb}MB RAM — Crawl4AI Docker needs 2 GB+ (Always Free 1 GB VMs cannot run it)."
    log "Use free-tier Playwright instead: bash scripts/recover-free-tier-vm.sh"
    exit 1
  fi
}

install_docker() {
  if command -v docker >/dev/null 2>&1 && compose_cmd version >/dev/null 2>&1; then
    log "Docker already installed"
    return
  fi

  log "Installing Docker (official get.docker.com script)..."
  curl -fsSL https://get.docker.com | run_root sh

  if need_sudo && ! groups | grep -q docker; then
    log "Adding $(whoami) to docker group (log out/in or run 'newgrp docker' to skip sudo)"
    run_root usermod -aG docker "$(whoami)" || true
  fi
}

wait_for_health() {
  local attempt=1
  while (( attempt <= 60 )); do
    if curl -fsS "$HEALTH_URL" >/dev/null 2>&1; then
      log "Crawl4AI healthy at $HEALTH_URL"
      return 0
    fi
    sleep 3
    (( attempt++ )) || true
  done
  return 1
}

cd "$APP_DIR"

if [[ ! -d .git ]]; then
  log "ERROR: $APP_DIR is not a git repo"
  exit 1
fi

log "Pulling latest main"
git fetch origin main
git reset --hard origin/main
chmod +x scripts/*.sh 2>/dev/null || true

pick_compose_file

if [[ ! -f "$COMPOSE_FILE" ]]; then
  log "ERROR: compose file not found: $COMPOSE_FILE"
  exit 1
fi

install_docker

if ! compose_cmd version >/dev/null 2>&1; then
  log "ERROR: docker compose not available after install"
  exit 1
fi

log "Building and starting Crawl4AI from $COMPOSE_FILE (first build may take several minutes)"
compose_cmd -f "$COMPOSE_FILE" up -d --build

if ! wait_for_health; then
  log "WARN: health check slow — trying in-container Playwright browser install"
  docker_cmd exec -u root careercopilot-crawl4ai bash -c \
    "playwright install chromium --with-deps || playwright install chromium; chown -R appuser:appuser /home/appuser/.cache" \
    2>/dev/null || true
  docker_cmd restart careercopilot-crawl4ai 2>/dev/null || true
fi

if ! wait_for_health; then
  log "ERROR: Crawl4AI did not become healthy in time"
  compose_cmd -f "$COMPOSE_FILE" logs --tail 40 crawl4ai 2>/dev/null || true
  log "TIP: 1 GB VMs are tight — resize to 2 GB in Oracle if this keeps failing"
  exit 1
fi

PYTHON="$APP_DIR/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
  log "Creating app virtualenv"
  python3 -m venv "$APP_DIR/.venv"
  # shellcheck disable=SC1091
  source "$APP_DIR/.venv/bin/activate"
  pip install -q -r "$APP_DIR/requirements.txt"
fi

log "Enabling crawl4ai in config/settings.yaml"
"$PYTHON" "$APP_DIR/scripts/enable-crawl4ai-settings.py"

if systemctl list-unit-files "${SERVICE}.service" 2>/dev/null | grep -q "$SERVICE"; then
  log "Restarting $SERVICE"
  run_root systemctl restart "$SERVICE"
  sleep 3
fi

log "Verification:"
curl -fsS "$HEALTH_URL" && echo ""
curl -fsS "http://127.0.0.1:8000/api/crawl4ai/health" && echo "" || log "WARN: app /api/crawl4ai/health not up yet"
curl -fsS "http://127.0.0.1:8000/api/version" && echo "" || log "WARN: app /api/version not up yet"

echo ""
log "Done. Crawl4AI is running and enabled."
log "Next: open the dashboard → Companies → Test connection on a JS careers page."
log "Then tap Run discovery and check Activity for Pipeline run."
