#!/usr/bin/env bash
# Configure GitHub Actions deploy secrets FROM THE VM (no Windows paths needed).
# Run while SSH'd into ubuntu@CareerCopilotAI:
#   bash scripts/setup-github-deploy-on-vm.sh
set -euo pipefail

REPO="${GITHUB_REPO:-Shreyash7798/CareerCopilotAI}"
APP_DIR="${CAREERCOPILOT_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
DEPLOY_KEY="${CAREERCOPILOT_DEPLOY_KEY:-$HOME/.ssh/github_actions_deploy}"
PUBLIC_IP="$(curl -fsS -H Metadata:true http://169.254.169.254/opc/v1/instance/metadata/public_ip 2>/dev/null || echo 161.118.184.228)"

echo "[setup] Installing GitHub CLI if needed..."
if ! command -v gh >/dev/null 2>&1; then
  sudo snap install gh --classic
fi

if ! gh auth status >/dev/null 2>&1; then
  echo "[setup] Log in to GitHub (follow the browser/device prompt):"
  gh auth login --hostname github.com --git-protocol https --web
fi

if [[ ! -f "$DEPLOY_KEY" ]]; then
  echo "ERROR: Deploy key missing at $DEPLOY_KEY — run: bash $APP_DIR/scripts/bootstrap-oci.sh"
  exit 1
fi

echo "[setup] Setting repository secrets for $REPO ..."
gh secret set OCI_HOST --body "$PUBLIC_IP" --repo "$REPO"
gh secret set OCI_USER --body "$(whoami)" --repo "$REPO"
gh secret set OCI_SSH_KEY < "$DEPLOY_KEY" --repo "$REPO"
gh secret set OCI_SSH_KEY_B64 --body "$(base64 < "$DEPLOY_KEY" | tr -d '\n')" --repo "$REPO"

echo "[setup] Triggering Deploy to OCI workflow ..."
gh workflow run "Deploy to OCI" --repo "$REPO" --ref main

echo ""
echo "Done. Future merges to main will auto-deploy."
echo "Watch: https://github.com/$REPO/actions"
