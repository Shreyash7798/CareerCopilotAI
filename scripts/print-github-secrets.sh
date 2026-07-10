#!/usr/bin/env bash
# Re-print GitHub Actions deploy secrets (run on the Oracle VM).
# Use when bootstrap output was lost or you need to fix "Deploy to OCI" workflow failures.
set -euo pipefail

DEPLOY_KEY="${CAREERCOPILOT_DEPLOY_KEY:-$HOME/.ssh/github_actions_deploy}"
APP_DIR="${CAREERCOPILOT_DIR:-$HOME/CareerCopilotAI}"

PUBLIC_IP="$(curl -fsS -H Metadata:true http://169.254.169.254/opc/v1/instance/metadata/public_ip 2>/dev/null || true)"
if [[ -z "$PUBLIC_IP" ]] && [[ -f "$APP_DIR/config/settings.yaml" ]]; then
  PUBLIC_IP="$(grep -E '^\s*base_url:' "$APP_DIR/config/settings.yaml" | sed -E 's/.*https?:\/\/([^/:]+).*/\1/' | head -1 || true)"
fi
PUBLIC_IP="${PUBLIC_IP:-161.118.184.228}"

if [[ ! -f "$DEPLOY_KEY" ]]; then
  echo "Deploy key not found at $DEPLOY_KEY"
  echo "Run: bash $APP_DIR/scripts/bootstrap-oci.sh"
  exit 1
fi

echo ""
echo "================================================================"
echo " GitHub Actions secrets (repo → Settings → Secrets → Actions)"
echo "================================================================"
echo ""
echo "OCI_HOST     = $PUBLIC_IP"
echo "OCI_USER     = $(whoami)"
echo ""
echo "OCI_SSH_KEY  = paste everything below (private key from THIS file only):"
echo "               NOT your Oracle download ssh-key-*.pem unless you use it as deploy key."
echo "----------------------------------------------------------------"
cat "$DEPLOY_KEY"
echo "----------------------------------------------------------------"
echo ""
echo "OCI_SSH_KEY_B64  = single-line alternative (recommended if paste fails):"
echo "----------------------------------------------------------------"
base64 < "$DEPLOY_KEY" | tr -d '\n'
echo ""
echo "----------------------------------------------------------------"
echo ""
echo "After adding these three secrets, re-run: Actions → Deploy to OCI"
echo ""
echo "Alternative (no SSH): set app.deploy_token in settings.yaml and add"
echo "  DEPLOY_HOOK_URL = http://${PUBLIC_IP}/api/deploy/hook"
echo "  DEPLOY_TOKEN    = same token value"
echo "================================================================"
