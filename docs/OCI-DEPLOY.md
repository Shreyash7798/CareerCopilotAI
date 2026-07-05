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

## After bootstrap — GitHub auto-deploy (optional)

Bootstrap prints an SSH private key. Add GitHub secrets:

- `OCI_HOST` = your public IP
- `OCI_USER` = `ubuntu`
- `OCI_SSH_KEY` = private key from bootstrap output

Or rely on **cron** (bootstrap installs auto-pull every 5 minutes).
