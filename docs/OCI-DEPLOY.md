# Deploy CareerCopilot on Oracle Cloud (OCI)

## Why `curl raw.githubusercontent.com` returned 404

The GitHub repo was **private**. Raw URLs only work for **public** repos.

## Step 1 — Make the repo public (use your phone, ~30 seconds)

GitHub mobile app:

1. Open **GitHub** app → **CareerCopilotAI** repo
2. Tap **Settings** (gear icon)
3. Scroll to **Danger zone** → **Change repository visibility**
4. Choose **Public** → confirm

Or on phone browser: `https://github.com/Shreyash7798/CareerCopilotAI/settings` → Danger zone → Change visibility → Public.

## Step 2 — Oracle Console SSH (type only, no paste needed)

Open **Oracle Cloud → Compute → Instances → your VM → Console connection**.

Type these lines **one at a time** (press Enter after each):

```
cd ~/CareerCopilotAI
```

```
git fetch origin main
```

```
git reset --hard origin/main
```

```
bash scripts/bootstrap-oci.sh
```

```
curl http://127.0.0.1:8000/api/version
```

Success: JSON with `"revision"` (not 404).

## Step 3 — Browser check

Open `http://161.118.184.228/` → click **Get results now**.

## Termius on your phone (SSH key, not password)

Oracle VMs use **SSH keys**, not password login. If Termius shows `auth password failed`:

1. Find the **`.pem` private key** you downloaded when you created the Oracle instance (e.g. `ssh-key-2024-01-15.key`).
2. In Termius → your host → **Key** → import that `.pem` file.
3. Set **Password** to empty (or remove it).
4. **Username** must be `ubuntu`, **Port** `22`, **Host** your public IP.

You cannot use `app.auth_password` from CareerCopilot for SSH — that only protects the web dashboard.

## Fix "Deploy to OCI" GitHub Actions email

The workflow needs deploy credentials. Your VM already auto-pulls every 5 minutes via **cron** (from bootstrap), so merges still deploy — the email is only about GitHub-triggered deploy.

**Option A — SSH (recommended)**

On the VM (Oracle Console SSH):

```
bash ~/CareerCopilotAI/scripts/print-github-secrets.sh
```

Copy the three values into GitHub → **CareerCopilotAI** → **Settings** → **Secrets and variables** → **Actions**:

| Secret | Value |
|--------|--------|
| `OCI_HOST` | `161.118.184.228` |
| `OCI_USER` | `ubuntu` |
| `OCI_SSH_KEY` | entire private key block from the script |

Then **Actions → Deploy to OCI → Re-run workflow**.

**Option B — HTTP hook (no SSH key in GitHub)**

On the VM:

```
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

Add to `config/settings.yaml` under `app:`:

```yaml
  deploy_token: "<paste-token-here>"
```

GitHub secrets:

| Secret | Value |
|--------|--------|
| `DEPLOY_HOOK_URL` | `http://161.118.184.228/api/deploy/hook` |
| `DEPLOY_TOKEN` | same token |

Restart: `sudo systemctl restart careercopilot`

## "Service not responding on :8000" after deploy

Often the app was still starting (first boot can take 10–30s). Check:

```
curl http://127.0.0.1:8000/api/version
```

If that works, deploy succeeded. If not:

```
sudo systemctl status careercopilot
sudo journalctl -u careercopilot -n 50 --no-pager
```

Common fixes:

- **Invalid YAML** in `config/settings.yaml` (bad indentation or unquoted `:` in passwords) — validate with `python3 -c "import yaml; yaml.safe_load(open('config/settings.yaml'))"`
- **Service not installed** — run `bash scripts/bootstrap-oci.sh`

## If `git fetch` still fails

Repo may still be private. Repeat Step 1 on your phone.

## Minimal deploy (without bootstrap script)

```
cd ~/CareerCopilotAI
```

```
git fetch origin main
```

```
git reset --hard origin/main
```

```
. .venv/bin/activate
```

```
pip install -r requirements.txt
```

```
sudo systemctl restart careercopilot
```

## After bootstrap — ongoing deploy

- **Cron** (automatic): bootstrap installs `scripts/deploy.sh` every 5 minutes.
- **GitHub Actions** (optional): add secrets above so merges trigger deploy immediately.
- **Manual**: `cd ~/CareerCopilotAI && ./scripts/deploy.sh`
