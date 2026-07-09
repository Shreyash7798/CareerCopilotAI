#!/usr/bin/env bash
# Recover a hung Oracle Always Free VM (~1 GB RAM).
#
# Crawl4AI Docker is too heavy for 1 GB — this script:
#   - stops Docker Crawl4AI and stuck builds
#   - adds 1 GB swap (free, no Oracle resize needed)
#   - uses Playwright in the app venv for JS careers pages instead
#   - deploys latest main and restarts CareerCopilot
#
# Run on the VM (SSH or Oracle Console):
#   cd ~/CareerCopilotAI && git fetch origin main && git reset --hard origin/main
#   bash scripts/recover-free-tier-vm.sh
set -euo pipefail

APP_DIR="${CAREERCOPILOT_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
SERVICE="${CAREERCOPILOT_SERVICE:-careercopilot}"
VENV="${CAREERCOPILOT_VENV:-$APP_DIR/.venv}"
MIN_RAM_MB="${CAREERCOPILOT_MIN_CRAWL4AI_RAM_MB:-1800}"

log() { echo "[recover] $*"; }

run_root() {
  if [[ "$(id -u)" -ne 0 ]]; then sudo "$@"; else "$@"; fi
}

total_mb() {
  free -m 2>/dev/null | awk '/^Mem:/{print $2}' || echo 0
}

stop_crawl4ai_docker() {
  log "Stopping Crawl4AI Docker (not suitable for $(total_mb)MB RAM)"
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
}

ensure_swap() {
  if swapon --show 2>/dev/null | grep -q .; then
    log "Swap already active"
    return
  fi
  if [[ -f /swapfile ]]; then
    log "Enabling existing /swapfile"
    run_root swapon /swapfile 2>/dev/null || true
    return
  fi
  log "Adding 1 GB swap file (free — helps Playwright on 1 GB VMs)"
  run_root fallocate -l 1G /swapfile || run_root dd if=/dev/zero of=/swapfile bs=1M count=1024 status=progress
  run_root chmod 600 /swapfile
  run_root mkswap /swapfile
  run_root swapon /swapfile
  if ! grep -q '^/swapfile ' /etc/fstab 2>/dev/null; then
    echo '/swapfile none swap sw 0 0' | run_root tee -a /etc/fstab >/dev/null
  fi
}

cd "$APP_DIR"

log "=== CareerCopilot free-tier recovery ==="
log "RAM: $(total_mb)MB (Crawl4AI Docker needs ${MIN_RAM_MB}MB+)"

stop_crawl4ai_docker
ensure_swap

log "Pulling latest main"
git fetch origin main
git reset --hard origin/main
chmod +x scripts/*.sh 2>/dev/null || true

if [[ ! -x "$VENV/bin/python" ]]; then
  python3 -m venv "$VENV"
fi
# shellcheck disable=SC1091
source "$VENV/bin/activate"
pip install -q -r requirements.txt

"$VENV/bin/python" "$APP_DIR/scripts/disable-crawl4ai-settings.py"

log "Installing Playwright Chromium in app venv (lightweight JS rendering)"
bash "$APP_DIR/scripts/install-playwright-deps.sh" || log "WARN: playwright deps install failed"

rm -f "$APP_DIR/data/.discovery.lock" 2>/dev/null || true
pkill -f "${APP_DIR}/run.py --once" 2>/dev/null || true

if systemctl list-unit-files "${SERVICE}.service" 2>/dev/null | grep -q "$SERVICE"; then
  log "Restarting $SERVICE"
  run_root systemctl restart "$SERVICE"
else
  bash "$APP_DIR/scripts/deploy.sh"
fi

log "Waiting for app..."
for i in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:8000/api/version >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

log "=== Status ==="
curl -fsS http://127.0.0.1:8000/api/version && echo "" || log "WARN: app not responding yet"
curl -fsS http://127.0.0.1:8000/api/crawl4ai/health && echo "" || true
free -h | head -3

echo ""
log "Done. Dashboard should load at http://$(curl -fsS -H Metadata:true http://169.254.169.254/opc/v1/instance/metadata/public_ip 2>/dev/null || echo 'YOUR_IP')/"
log "JS careers pages use Playwright (not Docker Crawl4AI). Tap Run discovery on the dashboard."
log "To use Docker Crawl4AI later, resize VM to 2 GB+ then: bash scripts/install-docker-crawl4ai.sh"
