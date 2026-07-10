#!/usr/bin/env bash
# Deploy latest staging branch to the staging clone (port 8001).
set -euo pipefail

APP_DIR="${CAREERCOPILOT_DIR:-$HOME/CareerCopilotAI-staging}"

export CAREERCOPILOT_DIR="$APP_DIR"
export CAREERCOPILOT_BRANCH=staging
export CAREERCOPILOT_SERVICE=careercopilot-staging
export CAREERCOPILOT_HEALTH_URL=http://127.0.0.1:8001/api/version
export CAREERCOPILOT_ENV=staging

exec bash "$APP_DIR/scripts/deploy.sh"
