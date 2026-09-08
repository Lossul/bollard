# bollard

An agent that geolocates street-level photos and defends its reasoning, scored against ground truth. See `CLAUDE.md` for the project's shape, conventions, and working agreements.

`webapp/` is a small interactive playground on top of the agent loop -- guess where a photo was taken, compare against the agent and the truth. This section covers deploying *that* app.

## Deployment

The playground is a single Dockerized FastAPI service (`webapp/main.py`). It needs two secrets (`ANTHROPIC_API_KEY`, `MAPILLARY_TOKEN`) and one piece of writable storage for its daily request counter (`runs/webapp_daily_counter.json`) -- the same file `webapp/rate_limit.py` already uses locally.

### Render (primary) vs. Railway (paid alternative)

**Render is the primary target.** Render's free tier has no persistent disk at all -- confirmed from Render's own docs: "any changes you make to a service's local files are lost every time the service redeploys or restarts." That means the daily request counter (`runs/webapp_daily_counter.json`) resets to 0 on every redeploy and every restart there, including the free tier's own spin-down-on-idle cycling. This is a deliberate, accepted tradeoff for staying on a genuinely free, no-card-required tier -- not worked around. Verified it degrades safely rather than breaking (see below): the app never crashes or 500s because of this, it just quietly starts counting from 0 again.

If you actually want the 50/day cap to hold across redeploys, Railway's free tier includes a real persistent volume (500 MB) -- see **Railway (paid alternative)** below. "Paid" because Railway's ongoing free plan requires a card on file even at $0 baseline; Render's free tier needs no card at all.

### Deploy to Render

1. Push this repo to GitHub if it isn't already there.
2. At [render.com](https://render.com), **New -> Web Service** -> connect this repo. Render auto-detects the `Dockerfile` at the repo root and builds from it -- no other build config needed.
3. Under **Environment**, add:
   - `ANTHROPIC_API_KEY`
   - `MAPILLARY_TOKEN`
   - Leave `BOLLARD_RUNS_DIR` unset -- it defaults to a path inside the container, which is fine since nothing persists there anyway on the free tier.
4. Choose the **Free** instance type. Deploy. Render assigns a public URL automatically and routes its injected `$PORT` to the container, which the app already reads.

**Redeploying after a code change:** push to the connected branch (auto-deploy is on by default), or use **Manual Deploy -> Deploy latest commit** in the dashboard.

**What actually happens on redeploy/restart, verified locally with Docker** (simulating Render's ephemeral filesystem by running the image with no volume at all):
- Fresh container, first request ever: `GET /api/status` -> `200`, `used_today: 0`.
- Normal use increments the counter normally; hitting the cap returns a clean `429` with a clear message, not a crash -- confirmed the app stays fully responsive (`GET /` still `200`) immediately after.
- Replacing the container (simulating a redeploy) while the counter was sitting at the limit: back to `used_today: 0` immediately, predictions succeed again right away. No exceptions, no 500s, at any point in that cycle.

### Railway (paid alternative, for a counter that survives redeploys)

1. Push this repo to GitHub if it isn't already there.
2. At [railway.app](https://railway.app), sign in and start a new project -> **Deploy from GitHub repo** -> select this repo. Railway also auto-detects the `Dockerfile`.
3. Open the service's **Variables** tab and add:
   - `ANTHROPIC_API_KEY`
   - `MAPILLARY_TOKEN`
   - `BOLLARD_RUNS_DIR` = `/data`
4. Create a volume and attach it to this service (Command Palette -> "Create Volume", or right-click the service card), with mount path `/data`. This is what makes `/data` (and the counter file inside it) survive redeploys -- without it, `BOLLARD_RUNS_DIR=/data` just points at ephemeral container storage, same as Render.
5. Under **Settings -> Networking**, generate a public domain.
6. Deploy.

**Redeploying after a code change:** push to the branch Railway is watching -- it redeploys automatically. To redeploy without a new commit, use the **Deploy** button in the dashboard, or `railway up` from the CLI.

### Testing the container locally

To match Render (no persistence -- confirms the app degrades gracefully):

```bash
docker build -t bollard-webapp .
docker run -p 8000:8000 \
  -e ANTHROPIC_API_KEY=sk-ant-... \
  -e MAPILLARY_TOKEN=... \
  bollard-webapp
```

Stop and re-run that same command and `/api/status`'s count resets to 0 -- this is the expected, accepted behavior on Render, not a bug.

To match Railway (persistent, via a bind mount standing in for a volume):

```bash
docker run -p 8000:8000 \
  -e ANTHROPIC_API_KEY=sk-ant-... \
  -e MAPILLARY_TOKEN=... \
  -e BOLLARD_RUNS_DIR=/data \
  -v "$(pwd)/.local-data:/data" \
  bollard-webapp
```

Stop and re-run this version and the count *does* carry over, proving the persistence path works the same way it will on Railway.

### Secrets

`ANTHROPIC_API_KEY` and `MAPILLARY_TOKEN` are read the same way locally and in production: `os.environ.get(...)`, falling back to a local `.env` file only when the real environment variable isn't set (see `agent/predict.py`'s `load_dotenv_value`). In production there is no `.env` file at all -- `.dockerignore` excludes it from the build context, so it's never baked into the image -- the values come entirely from whatever you set in Render's or Railway's dashboard.
