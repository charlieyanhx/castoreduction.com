# Deploy

Two paths to host this somewhere stable. Pick **Render** for simplest (web UI only, no CLI). Pick **Fly** for cheaper / faster cold-starts (CLI install required).

---

## Option A — Render.com (recommended, no CLI needed)

1. **Sign up** at https://dashboard.render.com (free, GitHub auth works).

2. **Create a Blueprint:** click "New +" → "Blueprint" → connect your GitHub → select `charlieyanhx/castoreduction.com`.

3. **Render auto-reads `render.yaml`** and provisions:
   - 1 web service (`castor-research`) running our Dockerfile
   - 5 GB persistent disk for SQLite caches
   - Health check at `/healthz`
   - Auto-redeploy on every push to `master`

4. **Set the secret env vars** in the Render dashboard:
   ```
   Dashboard → castor-research → Environment → Add Environment Variable
       GEMINI_API_KEY      = <your key>
       GROQ_API_KEY        = <your key>
       ANTHROPIC_API_KEY   = <your key>
   ```

5. **Deploy** runs automatically. First build takes ~5 min (Docker image bake).

6. **Get your stable URL** — Render gives you `https://castor-research.onrender.com` (or pick a custom name in step 3).

**Cost:** $7/mo for the `starter` plan in `render.yaml`. The free plan sleeps after 15 min idle, which breaks long-running `/plan` jobs.

---

## Option B — Fly.io (cheaper, requires CLI)

1. **Install flyctl:**
   ```bash
   curl -L https://fly.io/install.sh | sh
   ```

2. **Authenticate:**
   ```bash
   flyctl auth login
   ```

3. **From the `market-research-prototype/` directory:**
   ```bash
   cd market-research-prototype

   # Create the app (reads fly.toml)
   flyctl launch --name castor-research --region sjc --no-deploy

   # Create persistent volume for SQLite caches
   flyctl volumes create castor_data --size 5 --region sjc

   # Set LLM API keys
   flyctl secrets set \
     GEMINI_API_KEY=your_key \
     GROQ_API_KEY=your_key \
     ANTHROPIC_API_KEY=your_key

   # Deploy
   flyctl deploy

   # Open in browser
   flyctl open
   ```

4. **Stable URL:** `https://castor-research.fly.dev`

**Cost:** Free tier gives you 3 shared-cpu-1x VMs (1 GB each) and 3 GB persistent storage. Our setup fits in the free tier.

---

## Operational notes

### Long-running `/plan` jobs

A single `/plan` call takes 5-10 minutes because the pipeline runs ~22 LLM-heavy steps. Both Render `starter` and Fly Free are configured to NOT auto-stop the service, so the worker thread can finish.

If you ever hit a request-timeout error, increase `gracePeriod` in `render.yaml` or `grace_period` in `fly.toml`.

### Secrets in source control

This repo intentionally does NOT commit any LLM API keys. They live in:

- Render: Dashboard → Environment
- Fly: `flyctl secrets set ...`

Local dev: `.env` (gitignored).

### Custom domain

Both platforms support custom domains in the dashboard. Add a CNAME → `castor-research.onrender.com` (or `castor-research.fly.dev`) and you're done.

### Memory

If you see OOM errors during heavy bench runs, bump VM memory:
- Render: change `plan: starter` → `plan: standard` ($25/mo, 2 GB)
- Fly: change `memory_mb = 1024` → `memory_mb = 2048` in `fly.toml`

### Cold starts

- Render `starter`: never sleeps (always on) — no cold start
- Fly with `min_machines_running = 1`: same

---

## Sanity-check after first deploy

```bash
# Replace HOST with your real deploy URL
HOST=https://castor-research.onrender.com

curl $HOST/healthz                # → {"ok":true,...}
curl $HOST/api/tools | jq '.count'    # → 9
curl $HOST/api/skills | jq '.count'   # → 11
curl $HOST/architecture > /dev/null   # HTTP 200
```

Then open `$HOST/architecture` in a browser. You should see all 9 tools + 11 skills listed.
