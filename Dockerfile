# syntax=docker/dockerfile:1.7

FROM node:22-bookworm-slim AS frontend-build

WORKDIR /build
RUN corepack enable && corepack prepare pnpm@10.32.1 --activate
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY frontend/package.json ./frontend/package.json
RUN pnpm install --frozen-lockfile --filter @transcriber/web...
COPY frontend ./frontend
RUN pnpm --dir frontend build


FROM python:3.12-slim-bookworm AS runtime

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH=/app/backend/.venv/bin:$PATH \
    PYTHONPATH=/app/backend/src \
    FRONTEND_DIST=/app/frontend/dist \
    WHISPER_MODEL_CACHE=/data/model-cache \
    HF_HOME=/data/model-cache/huggingface \
    XDG_CACHE_HOME=/data/model-cache \
    WORKER_SCRATCH_DIR=/tmp/transcriber-scratch \
    FFMPEG_PATH=ffmpeg \
    FFPROBE_PATH=ffprobe

RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates curl ffmpeg \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.10.11 /uv /uvx /usr/local/bin/

WORKDIR /app
COPY backend/pyproject.toml backend/uv.lock ./backend/
RUN uv sync --project backend --frozen --no-dev --no-install-project
COPY backend ./backend
COPY scripts/configure_bucket_cors.py ./scripts/configure_bucket_cors.py
COPY --from=frontend-build /build/frontend/dist ./frontend/dist

EXPOSE 8000
CMD ["uvicorn", "transcriber.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
