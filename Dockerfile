# Runs the webapp (webapp/main.py) -- the FastAPI playground, not the batch
# eval CLI. See README > Deployment.

FROM python:3.11-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.10 /uv /uvx /usr/local/bin/

WORKDIR /app

# Install deps first so this layer caches across code-only changes.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY . .

# Invoke the venv's uvicorn directly, not `uv run` -- `uv run` re-checks the
# lock at every startup and will pull dev-only deps back in over the network
# (confirmed: it re-fetched pygments here despite --no-dev at build time).
# Railway/Render inject PORT at runtime; 8000 is only a local-`docker run` fallback.
EXPOSE 8000
CMD ["sh", "-c", ".venv/bin/uvicorn webapp.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
