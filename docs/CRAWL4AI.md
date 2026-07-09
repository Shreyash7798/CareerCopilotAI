# Crawl4AI integration (optional)

Crawl4AI improves job fetching from **JavaScript-heavy career portals** (Indian EPC sites, Oracle/Taleo, bot-protected pages) without replacing your existing connectors.

## What changes when disabled (default)

Nothing. Greenhouse, Lever, Workday, LinkedIn guest, and plain `careers_page` HTTP scraping work exactly as before.

## When to enable

- `careers_page` companies with **Render with browser** keep failing or return 0 jobs on your Oracle VM
- Playwright OOMs or is too slow on a 1 GB VM
- You want better JS rendering via a dedicated browser pool

Crawl4AI does **not** fix LinkedIn IP blocks — keep using **paste-import** for individual LinkedIn roles.

## Quick setup

### Oracle Always Free (1 GB RAM) — use this, not Docker

Crawl4AI Docker needs **2 GB+ RAM** and will hang or crash a 1 GB Always Free VM.

On the VM (SSH):

```bash
cd ~/CareerCopilotAI
git fetch origin main && git reset --hard origin/main
bash scripts/recover-free-tier-vm.sh
```

This stops Docker Crawl4AI, adds free swap, installs Playwright in the app, and restarts CareerCopilot. JS careers pages still work via built-in Playwright.

### Laptop / SSH — Crawl4AI Docker (2 GB+ VM only)

From your laptop terminal (replace the path to your Oracle `.pem` key):

```bash
ssh -i ~/Downloads/your-oracle-key.pem ubuntu@161.118.184.228
cd ~/CareerCopilotAI
git fetch origin main && git reset --hard origin/main
bash scripts/install-docker-crawl4ai.sh
```

The installer will:

1. Install Docker if missing
2. Pick the normal or **low-memory** compose file based on VM RAM
3. Start Crawl4AI on `127.0.0.1:11235`
4. Set `crawl4ai.enabled: true` in `config/settings.yaml`
5. Restart the `careercopilot` service

Verify:

```bash
curl http://127.0.0.1:11235/health
curl http://127.0.0.1:8000/api/crawl4ai/health
```

Public check (no login): `http://161.118.184.228/api/crawl4ai/health` should show `"ok":true`.

### Manual steps

#### 1. Start Crawl4AI (Docker)

On the VM (2 GB+ RAM recommended; 1 GB uses low-memory profile automatically):

```bash
cd ~/CareerCopilotAI
docker compose -f scripts/docker-compose.crawl4ai.yml up -d
curl http://127.0.0.1:11235/health
```

On **1 GB** Oracle free tier:

```bash
docker compose -f scripts/docker-compose.crawl4ai-lowmem.yml up -d
```

#### 2. Enable in settings

Edit `config/settings.yaml`:

```yaml
crawl4ai:
  enabled: true
  base_url: http://127.0.0.1:11235
  prefer_over_playwright: true
  fallback_on_playwright_failure: true
```

Restart CareerCopilot: `sudo systemctl restart careercopilot`

#### 3. Use on companies

**Option A — automatic (no company changes)**  
Companies already using `careers_page` with **Render with browser** will use Crawl4AI when enabled (`prefer_over_playwright`).

**Option B — explicit ATS type**  
On the Companies page, set ATS type to **crawl4ai** for stubborn portals (same Career URL + link selector as `careers_page`).

#### 4. Verify

- Companies page → **Test connection** on a failing employer
- Or: `curl -X POST http://localhost:8000/api/crawl4ai/health` (when logged in)

## Architecture

```
Discovery pipeline
  ├── greenhouse / lever / workday  → unchanged (JSON APIs)
  ├── linkedin                      → unchanged (guest API)
  ├── careers_page (render:false)   → unchanged (HTTP)
  ├── careers_page (render:true)    → Crawl4AI if enabled, else Playwright
  └── crawl4ai                      → always via Crawl4AI sidecar
```

## Resource notes

| VM RAM | Recommendation |
|--------|----------------|
| 1 GB (Oracle free) | Crawl4AI + app may be tight; try enabling for 1–2 companies only |
| 2 GB+ | Comfortable for Crawl4AI sidecar + CareerCopilot |

## Official docs

- Crawl4AI: https://github.com/unclecode/crawl4ai
- Docker API: https://github.com/unclecode/crawl4ai/tree/main/deploy/docker
