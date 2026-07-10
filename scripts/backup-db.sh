#!/usr/bin/env bash
# Daily SQLite backup — safe to run from cron.
set -euo pipefail

APP_DIR="${CAREERCOPILOT_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
VENV="${CAREERCOPILOT_VENV:-$APP_DIR/.venv}"

cd "$APP_DIR"
# shellcheck disable=SC1091
source "$VENV/bin/activate"
python3 -c "from app.ops import backup_database; print('Backup:', backup_database())"
