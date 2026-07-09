# AGENTS.md

## Cursor Cloud specific instructions

CareerCopilot AI is a single, local-first Python service: FastAPI + Jinja2
dashboard + JSON API, backed by SQLite. There is no separate frontend build
step (templates are server-rendered) and no external datastore. See `README.md`
for the full product overview and CLI modes.

### Environment
- Python deps are installed into a virtualenv at `.venv` (gitignored). The
  startup update script keeps it in sync with `requirements.txt`. Activate it
  before running anything: `source .venv/bin/activate`.
- No linter is configured in this repo (no ruff/flake8/black/pyproject). The
  only automated check is `pytest`.

### Run the app (dev)
- `python run.py` starts uvicorn on `0.0.0.0:8000` (host/port come from
  `config/settings.yaml`, falling back to `config/settings.example.yaml`).
  Startup calls `init_db()` which creates the SQLite file and tables.
- CLI modes: `python run.py --once` (one discovery cycle) and
  `python run.py --summary`. See `README.md`.
- Config is optional: a fresh clone runs out of the box using the
  `config/*.example.yaml` fallbacks. Companies are managed from the dashboard
  (Companies page), not config files.

### Tests
- `pytest tests/ -q`.
- GOTCHA: `tests/test_jobs_filters.py` constructs `TestClient(app)` without a
  `with` block, so the FastAPI lifespan (`init_db()`) never runs. Those 3 tests
  hit the real SQLite DB and fail with `no such table: jobs` on a clean checkout
  that has never created `data/careercopilot.db`. Initialize the DB once first —
  either start the app (`python run.py`) or run
  `python -c "from app.db import init_db; init_db()"` — after which the full
  suite (81 tests) passes. This is a pre-existing test-isolation quirk, not a
  code bug.

### Discovery / networking
- Job discovery fetches from public job-board APIs (Greenhouse, Lever,
  SmartRecruiters, Workday, careers pages). Outbound egress to these hosts works
  in this VM, so a full discovery run (`POST /api/pipeline/run-sync` or the
  dashboard "Run discovery" button) succeeds end-to-end and can take ~1-2 min
  across all enabled companies.
- Playwright/Chromium (`playwright install chromium`) and LibreOffice are
  optional — only needed for JavaScript-rendered careers pages and PDF resume
  export respectively. Core discovery, scoring, and DOCX generation work without
  them.
