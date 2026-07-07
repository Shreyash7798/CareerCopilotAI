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

### 1. Start Crawl4AI (Docker)

On the VM (2 GB+ RAM recommended):

```bash
cd ~/CareerCopilotAI
docker compose -f scripts/docker-compose.crawl4ai.yml up -d
curl http://127.0.0.1:11235/health
```

### 2. Enable in settings

Edit `config/settings.yaml`:

```yaml
crawl4ai:
  enabled: true
  base_url: http://127.0.0.1:11235
  prefer_over_playwright: true
  fallback_on_playwright_failure: true
```

Restart CareerCopilot: `sudo systemctl restart careercopilot`

### 3. Use on companies

**Option A — automatic (no company changes)**  
Companies already using `careers_page` with **Render with browser** will use Crawl4AI when enabled (`prefer_over_playwright`).

**Option B — explicit ATS type**  
On the Companies page, set ATS type to **crawl4ai** for stubborn portals (same Career URL + link selector as `careers_page`).

### 4. Verify

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
