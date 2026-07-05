# CareerCopilot AI

A personal career intelligence and automation platform. It continuously
discovers jobs from public sources, removes duplicates, scores every role
against your profile with a fully explainable formula, keeps an application
tracker, generates tailored resumes from your master CV, and notifies you of
high-priority opportunities — all local-first, with zero recurring cost.

Built against the frozen v1 specification: Python, FastAPI, SQLite,
Playwright, local-first, public job sources only, no automated actions on
your accounts.

## What it does

- **Job discovery** from Greenhouse boards, Lever boards, Workday career
  sites (all via their public JSON APIs — no scraping fights) and generic
  careers pages (BeautifulSoup, with optional Playwright rendering for
  JavaScript-heavy pages).
- **Duplicate removal** with a deterministic company+title+location key plus
  a fuzzy pass for near-identical titles.
- **Explainable match scoring** (0–100) across six weighted components:
  role fit, location fit, experience fit, industry fit, skills fit and
  company preference. Every job shows *why* it got its score.
- **CV parsing**: upload a PDF/DOCX/TXT resume; name, contacts, experience
  years, skills and employers are extracted locally to seed your profile.
- **Resume engine**: generates a tailored DOCX per job by reordering your
  existing skills and bullet points to match the JD. It never fabricates
  experience. PDF is produced automatically when LibreOffice is installed.
- **Application tracker**: company, role, status, dates, follow-ups,
  interview stages, outcome, notes.
- **Company & recruiter intelligence**: hiring activity per company, and a
  place to store *publicly available* recruiter details.
- **Notifications**: instant Telegram/email alerts for high-priority jobs
  plus a daily summary (new jobs, top matches, companies hiring, follow-ups
  due).
- **Exports**: SQLite is the primary store; an Excel workbook (Jobs,
  Companies, Recruiters, Applications sheets) is regenerated on every run and
  downloadable from the dashboard.
- **Dashboard**: a responsive web app that works on desktop and iOS (Safari →
  Share → *Add to Home Screen* installs it like an app). A JSON API mirrors
  everything at `/docs`.

## Quick start

Requires Python 3.11+.

```bash
git clone <this-repo> && cd CareerCopilotAI
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# optional, only needed for JavaScript-rendered careers pages:
playwright install chromium

# personalize (both files have commented examples):
cp config/settings.example.yaml config/settings.yaml
cp config/sources.example.yaml config/sources.yaml

python run.py
```

Open http://localhost:8000, go to **Profile** and upload your CV (use DOCX to
enable resume tailoring), review **Settings**, then press **Run discovery**.

Command-line modes (useful for cron instead of the built-in scheduler):

```bash
python run.py --once      # one discovery cycle, then exit
python run.py --summary   # send the daily summary now, then exit
```

## Configuration

Everything lives in two YAML files — no user data is hardcoded:

- `config/settings.yaml` — profile (locations, domains, skills, preferred
  companies), scoring weights and high-priority threshold, scheduler
  interval, Telegram/email credentials, export paths.
- `config/sources.yaml` — the list of job sources and title/location
  filters. Adding a Greenhouse or Lever company is one 4-line entry.

Both are gitignored; the `*.example.yaml` files document every option.

### Notifications

- **Telegram** (free): create a bot with [@BotFather](https://t.me/BotFather),
  put the token and your chat id in `settings.yaml`, set `enabled: true`.
- **Email**: any SMTP account works; for Gmail create an app password.

## Accessing from iPhone/iPad and other devices

The dashboard is a mobile-first PWA. Two ways to reach it from iOS:

1. **Same Wi-Fi**: run the server on your desktop and open
   `http://<desktop-ip>:8000` in Safari, then *Add to Home Screen*.
2. **From anywhere, desktop off**: run CareerCopilot on any always-on box —
   this is the recommended setup because discovery then runs 24/7 without
   your desktop being on. Zero/low-cost options:
   - a Raspberry Pi or any spare machine at home (pair with
     [Tailscale](https://tailscale.com) — free — to reach it securely from
     your phone anywhere without exposing ports);
   - an Oracle Cloud "Always Free" VM or similar free-tier host;
   - a free [Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/)
     if you want a public URL.

   On the host, install as above and keep it running with systemd:

```ini
# /etc/systemd/system/careercopilot.service
[Unit]
Description=CareerCopilot AI
After=network-online.target

[Service]
WorkingDirectory=/home/you/CareerCopilotAI
ExecStart=/home/you/CareerCopilotAI/.venv/bin/python run.py
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

If you expose the app beyond a private network (Tailscale/VPN), put it behind
authentication (e.g. Cloudflare Access, an authenticated reverse proxy) —
v1 has no built-in login because it is designed to be private by default.

## Architecture

```
Scheduler (APScheduler, in-process)
  └─ Discovery: app/sources/* (greenhouse, lever, workday, careers_page)
       └─ Normalizer (app/normalize.py)
            └─ Duplicate removal (app/dedup.py)
                 └─ Match scoring (app/scoring.py — deterministic, explainable)
                      └─ SQLite (app/models.py: jobs, companies, recruiters,
                         applications, resumes, user_profile, settings, activity_logs)
                           ├─ Excel export (app/exporter.py)
                           ├─ Notifications (app/notifications.py: Telegram, email)
                           └─ Dashboard + JSON API (app/routers/, FastAPI + Jinja2)
```

Every source is an isolated module registered in `app/sources/__init__.py`;
adding a new job board means writing one `fetch(entry) -> list[RawJob]`
function and one YAML entry.

## Boundaries (by design)

- No automatic application submission, no recruiter messaging.
- No login-walled scraping, no bypassing platform restrictions; LinkedIn is
  handled only via public pages you link yourself.
- No mandatory cloud services or paid APIs; your data stays in `data/` on
  your machine.

## Tests

```bash
pytest tests/ -q
```

Covers scoring (determinism, explanations, weight normalization), dedup and
normalization, CV parsing (TXT + DOCX), resume tailoring (reorders without
fabricating) and an end-to-end pipeline run against a fake source.
