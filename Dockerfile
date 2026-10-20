# syntax=docker/dockerfile:1
#
# One image, two uses (same code, same dependencies, same config):
#   app (default):  docker run -p 8501:8501 --env-file .env runbook-assistant
#   ingest job:     docker run --env-file .env runbook-assistant python -m runbook_assistant.ingest
# All configuration comes from environment variables at run time; the image
# contains no secrets and no .env file.

# ---------------------------------------------------------------------------
# Stage 1: builder. Installs dependencies into /app/.venv with uv.
# Only its output (/app) is copied into the final image, so uv and the
# build caches never ship.
# ---------------------------------------------------------------------------
# Python 3.13 because pyproject.toml requires >=3.13 and uv.lock is resolved
# for it. Pin the exact patch version so every build uses the same Python.
FROM python:3.13.14-slim-trixie AS builder

# uv from its official image, pinned to the version that wrote uv.lock.
COPY --from=ghcr.io/astral-sh/uv:0.13.1 /uv /uvx /bin/

# Compile .py -> .pyc once at build time (faster container start-up);
# copy files instead of hard-linking; never download a separate Python: use
# the one in this image; keep no uv download cache in the layer.
# (No BuildKit cache mounts: Azure ACR Tasks builds with the classic builder,
# which doesn't support RUN --mount. Docker's layer cache still applies.)
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0 \
    UV_NO_CACHE=1

WORKDIR /app

# Layer caching: dependencies change rarely, code changes often. Copying
# only the dependency files first means this slow layer is reused from the
# cache until pyproject.toml or uv.lock change.
COPY pyproject.toml uv.lock ./
# --frozen: install exactly what uv.lock says (fail instead of re-resolving);
# --no-dev: no pytest/ruff; --no-install-project: dependencies only for now.
RUN uv sync --frozen --no-dev --no-install-project

# Now the project itself (README.md is needed to build the package metadata).
COPY README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev

# ---------------------------------------------------------------------------
# Stage 2: runtime. Same base image, so the venv's Python links still work.
# ---------------------------------------------------------------------------
FROM python:3.13.14-slim-trixie

# Print logs immediately (no buffering, so `docker logs` / Azure Log
# Analytics see them live); don't write .pyc files at run time.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH" \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false \
    RUNBOOKS_DIR=/app/data/runbooks \
    CHROMA_DIR=/app/chroma_db

# Non-root user: if the app is ever compromised, the attacker is not root in
# the container. Fixed UID/GID so volume permissions are predictable.
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --create-home --home-dir /home/app app

WORKDIR /app

# The installed app from the builder (venv + source). Owned by root, so the
# app user can read and run it but not modify it.
COPY --from=builder /app /app
# The runbooks the ingest job reads (can be overridden with a mounted folder).
COPY data/runbooks ./data/runbooks
# Where Chroma stores its data (mount a volume here). Created and owned by
# the app user, so a new named volume inherits that ownership.
RUN mkdir -p /app/chroma_db && chown app:app /app/chroma_db

USER app

# Documents the port Streamlit listens on (publishing it is done at run time).
EXPOSE 8501

# Docker marks the container unhealthy if Streamlit stops answering.
# Python instead of curl, which the slim image doesn't have.
# (Azure Container Apps ignores this and uses its own probes, set in Phase B.)
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import urllib.request, sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8501/_stcore/health', timeout=4).status == 200 else 1)"]

# Default: the web app. 0.0.0.0 so it's reachable from outside the
# container; headless = don't try to open a browser.
CMD ["streamlit", "run", "src/runbook_assistant/app.py", \
     "--server.address=0.0.0.0", "--server.port=8501", "--server.headless=true"]
