#!/usr/bin/env bash
# Run one discovery cycle now (on the VM). Use when dashboard jobs look stale.
set -euo pipefail
APP_DIR="${CAREERCOPILOT_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
cd "$APP_DIR"
# shellcheck disable=SC1091
source .venv/bin/activate
exec python run.py --once
