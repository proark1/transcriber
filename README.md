# Transcriber

Private, restart-safe audio transcription for Railway. Create an account with a username and
6-12 digit PIN, upload supported audio from any source, choose English, German, or Turkish, and
keep the original audio plus a clean text transcript in your private history.

The application follows the approved
[design](docs/superpowers/specs/2026-08-03-railway-transcriber-design.md) and
[implementation plan](docs/superpowers/plans/2026-08-04-railway-transcriber-implementation-plan.md),
plus the approved [private accounts design](docs/superpowers/specs/2026-08-05-self-registering-private-accounts-design.md)
and [private accounts plan](docs/superpowers/plans/2026-08-05-self-registering-private-accounts-implementation-plan.md).

## Stack

- React 19, TypeScript, and Vite
- FastAPI, SQLAlchemy, PostgreSQL, and Alembic
- `faster-whisper` with FFmpeg
- S3-compatible private object storage
- Separate web and worker services on Railway

## Local prerequisites

- Node 22.12 or newer and pnpm 10.32
- Python 3.12 and uv
- Docker Desktop
- FFmpeg for media tests and local worker development

## Bootstrap

```powershell
Copy-Item .env.example .env
./scripts/dev.ps1 Start
uv sync --project backend
pnpm install
uv run --project backend python scripts/configure_bucket_cors.py
```

Run the frontend:

```powershell
pnpm dev
```

Run the API locally in a second terminal:

```powershell
uv run --project backend uvicorn transcriber.api.app:create_app --factory --reload
```

Run every local check, including real supported-format decoding and browser journeys:

```powershell
./scripts/test-integration.ps1
```

Or run individual checks:

```powershell
uv run --project backend pytest -c backend/pyproject.toml
uv run --project backend ruff check backend
uv run --project backend mypy backend/src
pnpm test
pnpm build
pnpm test:e2e
```

## Railway deployment

The root `Dockerfile` builds one immutable image for both Railway services. Set the web service's
Config as Code path to `/railway.web.json` and the worker's to `/railway.worker.json`.

Follow the [Railway deployment runbook](docs/runbooks/railway-deployment.md) for resource creation,
private Bucket references, account behavior, CORS, the worker model-cache volume, and smoke tests.
Use the [operations runbook](docs/runbooks/operations.md) for sizing, monitoring, backup, retry,
deletion recovery, and safe worker restarts.

Do not commit recordings, transcripts, model files, `.env`, or generated presigned URLs.
