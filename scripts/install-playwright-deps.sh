#!/usr/bin/env bash
# Install OS libraries required for Playwright Chromium on Ubuntu (Oracle VM).
# Fixes: libatk-1.0.so.0: cannot open shared object file
set -euo pipefail

APP_DIR="${CAREERCOPILOT_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
VENV="${CAREERCOPILOT_VENV:-$APP_DIR/.venv}"
PW="${VENV}/bin/playwright"

log() { echo "[playwright-deps] $*"; }

run_root() {
  if [[ "$(id -u)" -ne 0 ]]; then sudo "$@"; else "$@"; fi
}

if [[ ! -x "$PW" ]]; then
  log "WARN: $PW not found — run deploy.sh or create venv first"
  exit 0
fi

log "Installing Chromium browser + Ubuntu system dependencies"
"$PW" install chromium

if run_root "$PW" install-deps chromium 2>/dev/null; then
  log "System dependencies installed via playwright install-deps"
else
  log "playwright install-deps failed — installing core packages via apt"
  run_root apt-get update -qq
  run_root apt-get install -y -qq \
    libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 libxkbcommon0 \
    libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libgbm1 libasound2t64 \
    libpango-1.0-0 libcairo2 libnss3 libnspr4 libx11-6 libx11-xcb1 \
    libxcb1 libxext6 libxi6 libglib2.0-0 fonts-liberation \
    2>/dev/null || run_root apt-get install -y -qq \
    libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 libxkbcommon0 \
    libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libgbm1 libasound2 \
    libpango-1.0-0 libcairo2 libnss3 libnspr4 libx11-6 libx11-xcb1 \
    libxcb1 libxext6 libxi6 libglib2.0-0 fonts-liberation
fi

log "Playwright ready"
