#!/usr/bin/env bash
# One-time setup: add GitHub Actions deploy secrets from your laptop.
# Requires: gh auth login (https://cli.github.com)
#
# Windows (Git Bash or WSL):
#   bash scripts/setup-github-deploy-laptop.sh "C:/Users/MY LAPTOP/Downloads/ssh-key-2026-07-05.key"
#
# Then merges auto-deploy CareerCopilot to Oracle on every push to main.
set -euo pipefail

REPO="${GITHUB_REPO:-Shreyash7798/CareerCopilotAI}"
OCI_HOST="${OCI_HOST:-161.118.184.228}"
OCI_USER="${OCI_USER:-ubuntu}"
KEY_FILE="${1:-}"

if [[ -z "$KEY_FILE" || ! -f "$KEY_FILE" ]]; then
  echo "Usage: bash scripts/setup-github-deploy-laptop.sh /path/to/oracle-private-key"
  echo "Example: bash scripts/setup-github-deploy-laptop.sh \"\$HOME/Downloads/ssh-key-2026-07-05.key\""
  exit 1
fi

if ! command -v gh >/dev/null 2>&1; then
  echo "Install GitHub CLI first: https://cli.github.com"
  exit 1
fi

echo "Setting GitHub Actions secrets for $REPO ..."
gh secret set OCI_HOST --body "$OCI_HOST" --repo "$REPO"
gh secret set OCI_USER --body "$OCI_USER" --repo "$REPO"
gh secret set OCI_SSH_KEY < "$KEY_FILE" --repo "$REPO"

echo "Secrets set. Triggering deploy workflow ..."
gh workflow run "Deploy to OCI" --repo "$REPO" --ref main

echo ""
echo "Done. Watch: https://github.com/$REPO/actions"
echo "When deploy succeeds, open: http://$OCI_HOST/"
