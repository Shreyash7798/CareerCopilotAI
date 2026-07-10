# HTTPS setup (remove “Not secure” in the browser)

CareerCopilot serves **HTTP** by default (`http://YOUR_IP/`). Browsers show **Not secure** because there is no TLS certificate yet. Tier 1 added ops tooling and docs; **HTTPS is a one-time server setup** (not automatic from code alone).

You need **either a domain name** or **Cloudflare Tunnel**.

---

## Option A — Domain + Let’s Encrypt (recommended if you own a domain)

Example domain: `careercopilot.yourdomain.com`

### 1. DNS

In your domain registrar, add an **A record**:

| Type | Name | Value |
|------|------|--------|
| A | `careercopilot` (or `@`) | `161.118.184.228` |

Wait 5–30 minutes for DNS to propagate.

### 2. On the VM (SSH)

```bash
cd ~/CareerCopilotAI
git pull origin main   # or staging after promote
export CAREERCOPILOT_DOMAIN=careercopilot.yourdomain.com
export CAREERCOPILOT_ADMIN_EMAIL=you@example.com
bash scripts/setup-https.sh
```

### 3. Update settings

Edit `config/settings.yaml`:

```yaml
app:
  base_url: https://careercopilot.yourdomain.com
```

Restart:

```bash
sudo systemctl restart careercopilot
```

Open **https://careercopilot.yourdomain.com/** — padlock should appear.

---

## Option B — Cloudflare Tunnel (no domain purchase, free HTTPS URL)

Good if you only have the Oracle IP and want HTTPS quickly.

1. Create a free [Cloudflare](https://dash.cloudflare.com) account.
2. On the VM, install `cloudflared` and run a tunnel to `http://127.0.0.1:8000`.
3. Cloudflare gives you a URL like `https://something.trycloudflare.com` or a custom hostname on a domain you add to Cloudflare.

See [Cloudflare Tunnel docs](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/).

Set `app.base_url` in `settings.yaml` to that HTTPS URL.

---

## Why `http://161.118.184.228` stays “Not secure”

| URL | HTTPS? |
|-----|--------|
| `http://161.118.184.228` | No — raw IP over HTTP |
| `https://your-domain.com` | Yes — after Option A or B |

Let’s Encrypt **cannot** issue a certificate for a bare IP address. You must use a domain or a tunnel URL.

---

## Oracle firewall

Ensure ingress allows:

- **TCP 80** (HTTP — cert validation)
- **TCP 443** (HTTPS)

---

## Staging on :8001

HTTPS script configures production (port 8000 behind nginx). Staging on **8001** stays HTTP unless you add a separate nginx server block or stop staging when not testing.
