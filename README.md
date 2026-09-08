# bollard

An agent that geolocates street-level photos and defends its reasoning, scored against ground truth. See `CLAUDE.md` for the project's shape, conventions, and working agreements.

`webapp/` is a small interactive playground on top of the agent loop -- guess where a photo was taken, compare against the agent and the truth. This section covers deploying *that* app.

## Deployment

The playground is a single Dockerized FastAPI service (`webapp/main.py`). It needs two secrets (`ANTHROPIC_API_KEY`, `MAPILLARY_TOKEN`) and one piece of writable storage for its daily request counter (`runs/webapp_daily_counter.json`) -- the same file `webapp/rate_limit.py` already uses locally.

### Railway vs. Render: which one, and why

**Use Railway.** Checked both platforms' current docs directly before writing this:

- **Railway's free tier includes a persistent volume** (500 MB, on both the no-card 30-day/$5 trial and the ongoing $0/month Free plan -- the Free plan does require a payment method on file, just isn't charged unless you exceed its $1/month credit). Mount one and the counter survives redeploys and restarts.
- **Render's free tier has no persistent disk at all** -- confirmed from Render's own docs: "any changes you make to a service's local files are lost every time the service redeploys or restarts." Persistent disks are a paid-plan feature there. On Render's free tier, the daily counter resets on every redeploy and every restart (including the free tier's own spin-down-on-idle behavior) -- it would not actually enforce a rolling 50/day cap. Documented here rather than silently working around it.

If you want to use Render anyway (e.g. just to demo it, where a resetting counter doesn't matter), the same Dockerfile works there too -- see below.

### Deploy to Railway

1. Push this repo to GitHub if it isn't already there.
2. At [railway.app](https://railway.app), sign in and start a new project -> **Deploy from GitHub repo** -> select this repo. Railway detects the `Dockerfile` at the repo root automatically and builds from it -- no other config needed for the build itself.
3. Open the service's **Variables** tab and add:
   - `ANTHROPIC_API_KEY`
   - `MAPILLARY_TOKEN`
   - `BOLLARD_RUNS_DIR` = `/data`
4. Create a volume and attach it to this service (Command Palette -> "Create Volume", or right-click the service card), with mount path `/data`. This is what makes `/data` (and the counter file inside it) survive redeploys -- without it, `BOLLARD_RUNS_DIR=/data` just points at ephemeral container storage.
5. Under **Settings -> Networking**, generate a public domain. The app already reads Railway's injected `$PORT` and binds to it, so no port configuration is needed.
6. Deploy. First build takes a couple of minutes (installing dependencies via `uv`); later deploys reuse the cached dependency layer and are faster.

**Redeploying after a code change:** push to the branch Railway is watching -- it redeploys automatically. To redeploy without a new commit (e.g. to pick up a changed environment variable), use the **Deploy** button in the Railway dashboard, or `railway up` from the CLI.

### Deploy to Render (no persistent counter, otherwise identical)

1. At [render.com](https://render.com), **New -> Web Service** -> connect this repo. Render also auto-detects the `Dockerfile`.
2. Under **Environment**, add `ANTHROPIC_API_KEY` and `MAPILLARY_TOKEN`. Leave `BOLLARD_RUNS_DIR` unset (it'll default to a path inside the container, which is fine since nothing persists there anyway on the free tier).
3. Deploy. Render assigns a public URL automatically and routes `$PORT` the same way Railway does.
4. If you later want the counter to actually persist here, you need a paid instance type with a **Persistent Disk** attached, mounted at some path, with `BOLLARD_RUNS_DIR` set to that path -- Render does not offer this on the free tier at all.

**Redeploying after a code change:** push to the connected branch (auto-deploy is on by default), or use **Manual Deploy -> Deploy latest commit** in the dashboard.

### Testing the container locally

```bash
docker build -t bollard-webapp .
docker run -p 8000:8000 \
  -e ANTHROPIC_API_KEY=sk-ant-... \
  -e MAPILLARY_TOKEN=... \
  -e BOLLARD_RUNS_DIR=/data \
  -v "$(pwd)/.local-data:/data" \
  bollard-webapp
```

Then open `http://localhost:8000`. The `-v` bind mount stands in for a Railway volume -- stop and re-run the container and `/api/status`'s count should still reflect what you used before, proving the persistence path works the same way it will in production.

### Secrets

`ANTHROPIC_API_KEY` and `MAPILLARY_TOKEN` are read the same way locally and in production: `os.environ.get(...)`, falling back to a local `.env` file only when the real environment variable isn't set (see `agent/predict.py`'s `load_dotenv_value`). In production there is no `.env` file at all -- `.dockerignore` excludes it from the build context, so it's never baked into the image -- the values come entirely from whatever you set in Railway's or Render's dashboard.
