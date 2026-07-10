#!/usr/bin/env bash
# Tune production settings for Oracle Always Free (~1 GB RAM).
# Safe to re-run. Does not overwrite Telegram tokens or passwords.
set -euo pipefail

APP_DIR="${CAREERCOPILOT_DIR:-$HOME/CareerCopilotAI}"
SETTINGS="$APP_DIR/config/settings.yaml"

log() { echo "[free-tier] $*"; }

if [[ ! -f "$SETTINGS" ]]; then
  log "ERROR: $SETTINGS not found"
  exit 1
fi

cd "$APP_DIR"
# shellcheck disable=SC1091
source .venv/bin/activate 2>/dev/null || true

python3 <<'PY'
from pathlib import Path
import yaml

p = Path("config/settings.yaml")
cfg = yaml.safe_load(p.read_text()) or {}
pipe = cfg.setdefault("pipeline", {})
pipe["source_timeout_seconds"] = min(int(pipe.get("source_timeout_seconds") or 120), 60)
pipe["max_sources_per_run"] = min(int(pipe.get("max_sources_per_run") or 25), 15)
boot = cfg.setdefault("bootstrap", {})
boot["run_discovery_on_startup"] = False
boot["backfill_locations_on_startup"] = False
boot["discovery_startup_delay_seconds"] = 300
cfg["pipeline"] = pipe
cfg["bootstrap"] = boot
p.write_text(yaml.dump(cfg, default_flow_style=False, sort_keys=False))
print("Updated pipeline/bootstrap for free-tier")
PY

# Stop staging if running — frees RAM for production discovery
if systemctl is-active --quiet careercopilot-staging 2>/dev/null; then
  log "Stopping staging service to free RAM"
  sudo systemctl stop careercopilot-staging || true
fi

log "Restarting production"
sudo systemctl restart careercopilot 2>/dev/null || true
sleep 3
curl -fsS http://127.0.0.1:8000/api/version && echo ""
log "Done. Staging stays stopped until you: sudo systemctl start careercopilot-staging"
