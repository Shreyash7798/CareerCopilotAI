#!/usr/bin/env bash
# Enable HTTPS with Let's Encrypt (requires a domain pointing at this VM).
#
# Usage:
#   export CAREERCOPILOT_DOMAIN=careercopilot.example.com
#   export CAREERCOPILOT_ADMIN_EMAIL=admin@example.com
#   bash scripts/setup-https.sh
#
# See docs/HTTPS-SETUP.md
set -euo pipefail

DOMAIN="${CAREERCOPILOT_DOMAIN:-}"
EMAIL="${CAREERCOPILOT_ADMIN_EMAIL:-}"
APP_DIR="${CAREERCOPILOT_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"

log() { echo "[https] $*"; }

if [[ -z "$DOMAIN" || -z "$EMAIL" ]]; then
  log "ERROR: Set CAREERCOPILOT_DOMAIN and CAREERCOPILOT_ADMIN_EMAIL"
  log "Example: export CAREERCOPILOT_DOMAIN=careercopilot.example.com"
  exit 1
fi

if [[ "$(id -u)" -ne 0 ]]; then
  SUDO=sudo
else
  SUDO=""
fi

log "Installing nginx and certbot"
$SUDO apt-get update -qq
$SUDO apt-get install -y -qq nginx certbot python3-certbot-nginx

log "Installing HTTP reverse proxy"
$SUDO cp "$APP_DIR/scripts/nginx-careercopilot.conf" /etc/nginx/sites-available/careercopilot
$SUDO sed -i "s/server_name _;/server_name $DOMAIN;/" /etc/nginx/sites-available/careercopilot
$SUDO ln -sf /etc/nginx/sites-available/careercopilot /etc/nginx/sites-enabled/careercopilot
$SUDO rm -f /etc/nginx/sites-enabled/default
$SUDO nginx -t
$SUDO systemctl enable nginx
$SUDO systemctl reload nginx

log "Requesting TLS certificate for $DOMAIN"
$SUDO certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos -m "$EMAIL" --redirect

log "Done. Set app.base_url to https://$DOMAIN in config/settings.yaml and restart careercopilot."
log "Test: curl -fsS https://$DOMAIN/api/health"
