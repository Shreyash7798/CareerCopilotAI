#!/usr/bin/env bash
# One-time setup on the Oracle Cloud VM: systemd unit + auto-pull cron.
# Run on the server as the ubuntu user:
#   curl -fsSL https://raw.githubusercontent.com/Shreyash7798/CareerCopilotAI/main/scripts/setup-oci-deploy.sh | bash
set -euo pipefail

APP_DIR="${CAREERCOPILOT_DIR:-$HOME/CareerCopilotAI}"
REPO="${CAREERCOPILOT_REPO:-https://github.com/Shreyash7798/CareerCopilotAI.git}"

log() { echo "[setup] $*"; }

if [[ ! -d "$APP_DIR/.git" ]]; then
  log "Cloning into $APP_DIR"
  git clone "$REPO" "$APP_DIR"
fi

cd "$APP_DIR"
git fetch origin main
git reset --hard origin/main
chmod +x scripts/deploy.sh

if [[ ! -d .venv ]]; then
  python3 -m venv .venv
  .venv/bin/pip install -q -r requirements.txt
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
Environment=CAREERCOPILOT_DIR=$APP_DIR
ExecStart=$APP_DIR/.venv/bin/python run.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
  sudo systemctl daemon-reload
  sudo systemctl enable careercopilot
fi

./scripts/deploy.sh

CRON_LINE="*/5 * * * * cd $APP_DIR && ./scripts/deploy.sh >> $APP_DIR/data/deploy.log 2>&1"
if ! crontab -l 2>/dev/null | grep -qF "scripts/deploy.sh"; then
  log "Installing cron auto-deploy (every 5 minutes)"
  (crontab -l 2>/dev/null || true; echo "$CRON_LINE") | crontab -
fi

log "Done. Verify: curl http://127.0.0.1:8000/api/version"

# Passwordless restart for deploy hook / deploy.sh
SUDOERS="/etc/sudoers.d/careercopilot-deploy"
if [[ ! -f "$SUDOERS" ]]; then
  log "Allowing passwordless service restart for $(whoami)"
  echo "$(whoami) ALL=(ALL) NOPASSWD: /bin/systemctl restart careercopilot, /bin/systemctl start careercopilot" | sudo tee "$SUDOERS" >/dev/null
  sudo chmod 440 "$SUDOERS"
fi

SETTINGS="$APP_DIR/config/settings.yaml"
if [[ -f "$SETTINGS" ]]; then
  if grep -q 'deploy_token:' "$SETTINGS"; then
    log "deploy_token already set in config/settings.yaml"
  else
    TOKEN=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
    log "Add under app: in config/settings.yaml:"
    echo "  deploy_token: $TOKEN"
    log "Then add GitHub secrets DEPLOY_HOOK_URL and DEPLOY_TOKEN (same value)"
  fi
fi
