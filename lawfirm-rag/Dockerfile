# syntax=docker/dockerfile:1
#
# Law Firm RAG — production image for Oracle Cloud Infrastructure (OCI).
#
# Multi-stage:
#   1. frontend  — builds the React/Vite app into lawfirm-rag/static
#   2. runtime   — Python 3.11 serving FastAPI + the built frontend
#
# Build context is the REPO ROOT (the frontend and the backend are both needed):
#   docker build -f lawfirm-rag/Dockerfile -t lawfirm-rag:latest .
# or just use the repo-root docker-compose.yml.
#
# NOTE ON THE EMBEDDER: this image installs requirements.txt only, which does
# NOT include torch (the local HuggingFace embedder lives in
# requirements-local.txt). So the container MUST use a hosted embedder — set
# OPENAI_API_KEY, or REMOTE_EMBED_URL / REMOTE_EMBED_KEY / REMOTE_EMBED_MODEL.
# Without one, the first embed call tries to load the local model and fails.
#
# NOTE ON WORKERS: the app starts an in-process APScheduler job that polls
# Google Drive every 6h (app.py startup -> ingest/scheduler.start_scheduler).
# Run exactly ONE uvicorn worker, or every worker will poll Drive. To scale out,
# run more *containers* only after moving the Drive poll to its own single
# process (each extra app instance would otherwise poll Drive too).

# ---------------------------------------------------------------
# Stage 1 — build the frontend (vite build -> ../lawfirm-rag/static)
# ---------------------------------------------------------------
FROM node:22-slim AS frontend

WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
# vite.config.js writes to ../lawfirm-rag/static (repo convention).
RUN mkdir -p /build/lawfirm-rag/static && npm run build

# ---------------------------------------------------------------
# Stage 2 — Python runtime
# ---------------------------------------------------------------
FROM python:3.11-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000

# Mirror the checkout layout (/srv/lawfirm-rag is the repo root, the code is in
# its lawfirm-rag/ subdir) so config.py's ROOT_DIR = <repo root> and an optional
# mounted .env at /srv/lawfirm-rag/.env both resolve exactly like on the host.
WORKDIR /srv/lawfirm-rag

# curl is used by the container healthcheck.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY lawfirm-rag/requirements.txt ./lawfirm-rag/
RUN pip install --no-cache-dir -r lawfirm-rag/requirements.txt

# Backend source (config.py, app.py, agents/, ingest/, search/, draft/, static/).
COPY lawfirm-rag/ ./lawfirm-rag/

# Replace the committed static/ with the fresh build from stage 1.
RUN rm -rf /srv/lawfirm-rag/lawfirm-rag/static
COPY --from=frontend /build/lawfirm-rag/static ./lawfirm-rag/static

# Run as an unprivileged user.
RUN useradd --create-home --uid 10001 app && chown -R app:app /srv/lawfirm-rag
USER app
WORKDIR /srv/lawfirm-rag/lawfirm-rag

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${PORT}/healthz" || exit 1

# Single worker on purpose — see the scheduler note above.
CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]
