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

- **Company management from the dashboard**: pick companies from a
  pre-verified, sector-grouped catalog (consulting, manufacturing,
  technology…) or add any company by its ATS details — Greenhouse, Lever,
  Workday, SmartRecruiters, SAP SuccessFactors, Oracle, Taleo or a generic
  careers page. Enable/disable, per-company keywords, refresh interval and a
  Test Connection button. No config files, no server access needed.
- **Job discovery** from Greenhouse boards, Lever boards, SmartRecruiters,
  Workday career sites (all via their public JSON APIs — no scraping fights)
  and generic careers pages (BeautifulSoup, with optional Playwright
  rendering for JavaScript-heavy pages).
- **Duplicate removal** with a deterministic company+title+location key plus
  a fuzzy pass for near-identical titles.
- **Explainable match scoring** (0–100) across six weighted components:
  role fit, location fit, experience fit, industry fit, skills fit and
  company preference. Every job shows *why* it got its score. All jobs are
  re-scored automatically whenever you upload a CV or change your profile,
  so the ranking always reflects the current user.
- **Job lifecycle**: postings that disappear from complete-list sources
  (Greenhouse, Lever) are marked closed automatically and reactivated if
  they return.
- **CV parsing**: upload a PDF/DOCX/TXT resume; name, contacts, experience
  years, skills and employers are extracted locally to seed your profile.
- **Resume engine**: generates a tailored DOCX per job by reordering your
  existing skills and bullet points to match the JD. It never fabricates
  experience. PDF is produced automatically when LibreOffice is installed.
- **Cover letter engine**: tailored DOCX from your profile/CV facts and the
  JD — local, no fabrication, no paid APIs.
- **Interview prep**: STAR prompts, skills to emphasise, gap areas, elevator
  pitch and questions for the interviewer — per job, generated locally.
- **Application tracker**: company, role, status, dates, follow-ups,
  interview stages, outcome, notes.
- **Company & recruiter intelligence**: hiring activity per company; when
  *Extract recruiters from job postings* is enabled on a company, public
  names and emails found in JDs are stored automatically.
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

# personalize (optional — profile/notifications; companies are managed in the UI):
cp config/settings.example.yaml config/settings.yaml

python run.py
```

Open http://localhost:8000 and follow the Get Started card:

1. **Profile** → upload your CV (use DOCX to enable resume tailoring).
2. **Companies** → add companies from the sector catalog (one click each) or
   add any employer manually; use **Test Connection** to validate.
3. Press **Run discovery**. The scheduler keeps checking automatically after
   that, and a short Telegram/email check-in is sent after each scheduled run.

A legacy `config/sources.yaml` (from older installs) is imported into the
Companies table automatically on the first discovery run.

Command-line modes (useful for cron instead of the built-in scheduler):

```bash
python run.py --once      # one discovery cycle, then exit
python run.py --summary   # send the daily summary now, then exit
```

## Configuration

- **Companies to monitor** — managed entirely from the dashboard (Companies
  page), stored in the database. The curated catalog lives in
  `config/company_catalog.yaml` (ships with the app, extensible via PRs).
- `config/settings.yaml` — profile (locations, domains, skills, preferred
  companies), scoring weights and high-priority threshold, scheduler interval
  and timezone, Telegram/email credentials, export paths. Gitignored; the
  example file documents every option.

### Notifications

- **Telegram** (free): create a bot with [@BotFather](https://t.me/BotFather),
  put the token and your chat id in `settings.yaml`, set `enabled: true`.
- **Email**: any SMTP account works; for Gmail create an app password.
- Use **Settings → Send test notification** to verify a channel instantly.
- After every scheduled discovery run a short check-in is sent (disable with
  `notifications.run_summary: false`). Set `scheduler.timezone` (e.g.
  `Asia/Kolkata`) so the daily summary arrives at your local time.

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

If you expose the app beyond a private network, set `app.auth_password` in
`config/settings.yaml` — the dashboard and API then require login (sessions
survive 30 days; changing the password signs everyone out). For public
deployments also consider HTTPS via a reverse proxy or Cloudflare Tunnel.

### Oracle Cloud: keep the server in sync with GitHub

Merging a PR updates GitHub only — the VM does **not** auto-update unless you
wire up deploy. Pick **one** of these:

**Option A — GitHub Actions (deploy on every merge to `main`)**

1. On the VM, create a deploy key and authorize it:
   ```bash
   ssh-keygen -t ed25519 -f ~/.ssh/careercopilot_deploy -N ""
   cat ~/.ssh/careercopilot_deploy.pub >> ~/.ssh/authorized_keys
   ```
2. In GitHub → **Settings → Secrets and variables → Actions**, add:
   - `OCI_HOST` = your public IP (e.g. `161.118.184.228`)
   - `OCI_USER` = `ubuntu`
   - `OCI_SSH_KEY` = contents of `~/.ssh/careercopilot_deploy` (private key)
3. Merge a PR — the **Deploy to OCI** workflow runs `git pull` + restart.

**Option B — Cron on the VM (no GitHub secrets)**

SSH into the box once and run:
```bash
cd ~/CareerCopilotAI
bash scripts/setup-oci-deploy.sh
```
That installs systemd (if missing) and a cron job that runs `scripts/deploy.sh`
every 5 minutes when `main` has new commits.

**Manual deploy (any time)**
```bash
cd ~/CareerCopilotAI && ./scripts/deploy.sh
```

**Verify the live revision**
```bash
curl http://161.118.184.228/api/version
# {"revision":"a7734f4","project":"CareerCopilotAI"}
```
Compare `revision` to the latest commit on `main`. If `/api/quick-start` returns
404, the server is still on an old build.

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
