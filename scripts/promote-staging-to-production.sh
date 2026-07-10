#!/usr/bin/env bash
# Promote tested staging code to production (same VM).
#
# Copies git revision from staging clone → production clone, restarts production only.
# Staging keeps running for rollback comparison.
#
# Usage:
#   bash ~/CareerCopilotAI-staging/scripts/promote-staging-to-production.sh
set -euo pipefail

PROD_DIR="${CAREERCOPILOT_PROD_DIR:-$HOME/CareerCopilotAI}"
STAGING_DIR="${CAREERCOPILOT_STAGING_DIR:-$HOME/CareerCopilotAI-staging}"

log() { echo "[promote] $*"; }

for dir in "$STAGING_DIR" "$PROD_DIR"; do
  if [[ ! -d "$dir/.git" ]]; then
    log "ERROR: Missing git repo at $dir"
    exit 1
  fi
done

STAGING_REV=$(cd "$STAGING_DIR" && git rev-parse --short HEAD)
log "Staging revision: $STAGING_REV"

if ! curl -fsS --connect-timeout 5 "http://127.0.0.1:8001/api/version" >/dev/null 2>&1; then
  log "WARN: Staging :8001 not responding — continue anyway? (Ctrl+C to abort)"
  sleep 5
fi

log "Backing up production database"
export CAREERCOPILOT_DIR="$PROD_DIR"
bash "$PROD_DIR/scripts/backup-db.sh" || log "WARN: backup skipped"

log "Updating production to match staging commit"
STAGING_HEAD="$(cd "$STAGING_DIR" && git rev-parse HEAD)"
cd "$PROD_DIR"
export CAREERCOPILOT_DIR="$PROD_DIR"
export CAREERCOPILOT_SERVICE=careercopilot
export CAREERCOPILOT_HEALTH_URL="${CAREERCOPILOT_HEALTH_URL:-http://127.0.0.1:8000/api/version}"
export CAREERCOPILOT_GIT_REF="$STAGING_HEAD"
bash "$PROD_DIR/scripts/deploy.sh"

PROD_REV=$(git rev-parse --short HEAD)
log "Production now at $PROD_REV"
curl -fsS http://127.0.0.1:8000/api/version && echo ""
log "Done. Verify http://YOUR_IP/ in browser."
