#!/usr/bin/env bash
# Create an isolated staging clone on the same VM (separate port, DB, and service).
#
# Production stays at ~/CareerCopilotAI on port 8000.
# Staging runs at ~/CareerCopilotAI-staging on port 8001.
#
# Usage (Oracle Console SSH):
#   bash ~/CareerCopilotAI/scripts/setup-staging-clone.sh
#   # or from repo root:
#   bash scripts/setup-staging-clone.sh
#
# Test staging: http://YOUR_IP:8001/
# Promote when ready: bash scripts/promote-staging-to-production.sh
set -euo pipefail

PROD_DIR="${CAREERCOPILOT_PROD_DIR:-$HOME/CareerCopilotAI}"
STAGING_DIR="${CAREERCOPILOT_STAGING_DIR:-$HOME/CareerCopilotAI-staging}"
REPO="${CAREERCOPILOT_REPO:-https://github.com/Shreyash7798/CareerCopilotAI.git}"
BRANCH="${CAREERCOPILOT_STAGING_BRANCH:-staging}"
STAGING_PORT="${CAREERCOPILOT_STAGING_PORT:-8001}"
SERVICE="careercopilot-staging"

log() { echo "[staging-setup] $*"; }

if [[ ! -d "$PROD_DIR/.git" ]]; then
  log "ERROR: Production clone not found at $PROD_DIR"
  exit 1
fi

if [[ ! -d "$STAGING_DIR/.git" ]]; then
  log "Cloning repository → $STAGING_DIR"
  git clone "$REPO" "$STAGING_DIR"
fi

cd "$STAGING_DIR"
git fetch origin "$BRANCH" main 2>/dev/null || git fetch origin main
if git show-ref --verify --quiet "refs/remotes/origin/$BRANCH"; then
  git checkout -B "$BRANCH" "origin/$BRANCH"
else
  log "Branch origin/$BRANCH not found — using main (create staging branch on GitHub when ready)"
  git checkout -B staging origin/main
fi
git reset --hard "HEAD"
chmod +x scripts/*.sh 2>/dev/null || true

if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -q -r requirements.txt

SETTINGS="$STAGING_DIR/config/settings.yaml"
EXAMPLE="$STAGING_DIR/config/settings.staging.example.yaml"
if [[ ! -f "$SETTINGS" ]]; then
  if [[ -f "$EXAMPLE" ]]; then
    cp "$EXAMPLE" "$SETTINGS"
  elif [[ -f "$PROD_DIR/config/settings.yaml" ]]; then
    cp "$PROD_DIR/config/settings.yaml" "$SETTINGS"
    # Patch port and paths for staging
    python3 <<'PY'
import yaml
from pathlib import Path
p = Path("config/settings.yaml")
cfg = yaml.safe_load(p.read_text()) or {}
app = cfg.setdefault("app", {})
app["port"] = 8001
app["base_url"] = app.get("base_url", "").replace(":8000", ":8001") or "http://127.0.0.1:8001"
app["data_dir"] = "data-staging"
cfg["app"] = app
boot = cfg.setdefault("bootstrap", {})
boot["run_discovery_on_startup"] = False
p.write_text(yaml.dump(cfg, default_flow_style=False, sort_keys=False))
PY
  else
    cp config/settings.example.yaml "$SETTINGS"
  fi
  log "Created staging settings.yaml (separate data-staging/ database)"
fi

export CAREERCOPILOT_DIR="$STAGING_DIR"
export CAREERCOPILOT_SERVICE="$SERVICE"
export CAREERCOPILOT_HEALTH_URL="http://127.0.0.1:${STAGING_PORT}/api/version"
export CAREERCOPILOT_ENV=staging

UNIT=/etc/systemd/system/${SERVICE}.service
if [[ ! -f "$UNIT" ]]; then
  log "Installing systemd unit $SERVICE on port $STAGING_PORT"
  sudo tee "$UNIT" >/dev/null <<EOF
[Unit]
Description=CareerCopilot AI (staging)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$(whoami)
WorkingDirectory=$STAGING_DIR
Environment=CAREERCOPILOT_DIR=$STAGING_DIR
Environment=CAREERCOPILOT_ENV=staging
ExecStart=$STAGING_DIR/.venv/bin/python run.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF
  sudo systemctl daemon-reload
  sudo systemctl enable "$SERVICE"
fi

bash "$STAGING_DIR/scripts/deploy.sh"

PUBLIC_IP=$(curl -fsS -H Metadata:true http://169.254.169.254/opc/v1/instance/metadata/public_ip 2>/dev/null || echo "YOUR_IP")

echo ""
echo "================================================================"
echo " STAGING CLONE READY"
echo "================================================================"
echo " Staging URL:  http://${PUBLIC_IP}:${STAGING_PORT}/"
echo " Production:   http://${PUBLIC_IP}/"
echo " Directory:    $STAGING_DIR"
echo " Branch:       $BRANCH"
echo ""
echo " Workflow:"
echo "  1. Push features to branch 'staging' on GitHub"
echo "  2. On VM: cd $STAGING_DIR && git pull && bash scripts/deploy.sh"
echo "  3. Test on port ${STAGING_PORT}"
echo "  4. Promote: bash $STAGING_DIR/scripts/promote-staging-to-production.sh"
echo "================================================================"
