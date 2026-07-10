# Production checklist (v1)

Use this after deploying to Oracle Cloud or any public host.

## Security

- [ ] Set `app.auth_password` in `config/settings.yaml` (public IP protection)
- [ ] Set `app.base_url` to your real URL (e.g. `http://161.118.184.228` or HTTPS domain)
- [ ] Optional: HTTPS via Cloudflare Tunnel or `bash scripts/setup-https.sh` — see [HTTPS-SETUP.md](HTTPS-SETUP.md)

## Notifications

- [ ] Enable Telegram in `config/settings.yaml` (`notifications.telegram.enabled: true`)
- [ ] Set `scheduler.timezone: Asia/Kolkata` (or your timezone)
- [ ] Settings → **Send test notification**

## Deploy verification

- [ ] `curl http://<your-ip>/api/version` returns a git revision (not `unknown` or 404)
- [ ] Footer shows **build &lt;revision&gt;** on the dashboard
- [ ] Cron auto-pull running: `crontab -l` shows `scripts/deploy.sh`
- [ ] Optional GitHub Actions secrets: `OCI_HOST`, `OCI_USER`, `OCI_SSH_KEY` (or run `bash scripts/print-github-secrets.sh` on the VM)

## Discovery quality

- [ ] Upload CV on **Profile** (improves scoring)
- [ ] Companies page: enable **Accenture India**, **PwC**, **EY India**, etc.
- [ ] Click **Get results now** once after deploy
- [ ] Jobs page shows posting age; stale/removed roles drop off after each discovery run

## Optional

- [ ] Google Sheets: `exports.google_sheets.enabled: true` + service account JSON
- [ ] `playwright install chromium` on server for BCG / JS careers pages
- [ ] LibreOffice on server for resume PDF export

## Known limits (v1)

- McKinsey, Bain, Kearney, Alvarez & Marsal: bot-protected from cloud IPs — catalog entries start **disabled**
- KPMG India, Grant Thornton Bharat: custom JS portals — test before enabling
- SAP careers pages (Deloitte, EY, Capgemini): descriptions improve after careers_page detail fetch (v1.1)
- JS-heavy portals: optional [Crawl4AI sidecar](CRAWL4AI.md) improves `careers_page` with render enabled

## Optional: Crawl4AI (better JS career pages)

- [ ] `docker compose -f scripts/docker-compose.crawl4ai.yml up -d`
- [ ] `config/settings.yaml` → `crawl4ai.enabled: true`
- [ ] Test Connection on a failing EPC/consulting company
