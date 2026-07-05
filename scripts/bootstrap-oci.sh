#!/usr/bin/env bash
# Run once in Oracle Cloud Console → Instance → Console connection (browser SSH).
#
# The repo must be PUBLIC for curl-from-GitHub, OR run from an existing clone:
#   cd ~/CareerCopilotAI && git fetch origin main && git reset --hard origin/main
#   bash scripts/bootstrap-oci.sh
#
# See docs/OCI-DEPLOY.md for step-by-step instructions (phone + type-only commands).
set -euo pipefail

APP_DIR="${CAREERCOPILOT_DIR:-$HOME/CareerCopilotAI}"
REPO="${CAREERCOPILOT_REPO:-https://github.com/Shreyash7798/CareerCopilotAI.git}"
DEPLOY_KEY="$HOME/.ssh/github_actions_deploy"

log() { echo "[bootstrap] $*"; }

if [[ ! -d "$APP_DIR/.git" ]]; then
  log "Cloning $REPO → $APP_DIR"
  git clone "$REPO" "$APP_DIR"
fi

cd "$APP_DIR"
log "Pulling latest main"
git fetch origin main
git reset --hard origin/main
chmod +x scripts/*.sh 2>/dev/null || true

if [[ ! -d .venv ]]; then
  log "Creating Python virtualenv"
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -q -r requirements.txt

# GitHub Actions deploy key (generated on this VM — add private key to GitHub secrets)
if [[ ! -f "$DEPLOY_KEY" ]]; then
  log "Creating GitHub Actions deploy key"
  ssh-keygen -t ed25519 -f "$DEPLOY_KEY" -N "" -C "careercopilot-github-actions"
fi
grep -qF "$(cat "${DEPLOY_KEY}.pub")" ~/.ssh/authorized_keys 2>/dev/null \
  || cat "${DEPLOY_KEY}.pub" >> ~/.ssh/authorized_keys

SUDOERS="/etc/sudoers.d/careercopilot-deploy"
if [[ ! -f "$SUDOERS" ]]; then
  log "Enabling passwordless service restart"
  echo "$(whoami) ALL=(ALL) NOPASSWD: /bin/systemctl restart careercopilot, /bin/systemctl start careercopilot" \
    | sudo tee "$SUDOERS" >/dev/null
  sudo chmod 440 "$SUDOERS"
fi

UNIT=/etc/systemd/system/careercopilot.service
if [[ ! -f "$UNIT" ]]; then
  log "Installing systemd unit"
  sudo tee "$UNIT" >/dev/null <<EOF
[Unit]
Description=CareerCopilot AI
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$(whoami)
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/.venv/bin/python run.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
  sudo systemctl daemon-reload
  sudo systemctl enable careercopilot
fi

log "Restarting app"
sudo systemctl restart careercopilot || sudo systemctl start careercopilot
sleep 3

log "Verifying"
curl -fsS http://127.0.0.1:8000/api/version || { log "WARN: /api/version not up yet"; }
curl -fsS -X POST http://127.0.0.1:8000/api/quick-start >/dev/null \
  && log "quick-start OK" || log "WARN: quick-start not available yet (retry after pull)"

# Cron auto-pull fallback (every 5 min)
CRON_LINE="*/5 * * * * cd $APP_DIR && ./scripts/deploy.sh >> $APP_DIR/data/deploy.log 2>&1"
if ! crontab -l 2>/dev/null | grep -qF "scripts/deploy.sh"; then
  log "Installing cron auto-deploy"
  (crontab -l 2>/dev/null || true; echo "$CRON_LINE") | crontab -
fi

PUBLIC_IP=$(curl -fsS -H Metadata:true http://169.254.169.254/opc/v1/instance/metadata/public_ip 2>/dev/null || echo "161.118.184.228")

echo ""
echo "================================================================"
echo " BOOTSTRAP COMPLETE"
echo "================================================================"
echo ""
echo "1) Verify in your browser:"
echo "   http://${PUBLIC_IP}/api/version"
echo "   (should return JSON with git revision, not 404)"
echo ""
echo "2) Add GitHub Actions secrets (repo → Settings → Secrets → Actions):"
echo "   OCI_HOST     = ${PUBLIC_IP}"
echo "   OCI_USER     = $(whoami)"
echo "   OCI_SSH_KEY  = paste everything below (private key):"
echo "----------------------------------------------------------------"
cat "$DEPLOY_KEY"
echo "----------------------------------------------------------------"
echo ""
echo "After adding secrets, re-run 'Deploy to OCI' workflow on GitHub."
echo "Lost the key? Run: bash $APP_DIR/scripts/print-github-secrets.sh"
echo "================================================================"
