# Transcriber

Private, restart-safe audio transcription for Railway. Upload one recording, choose English,
German, or Turkish, and keep the original audio plus a clean text transcript in a permanent
history.

The application is being built from the approved
[design](docs/superpowers/specs/2026-08-03-railway-transcriber-design.md) and
[implementation plan](docs/superpowers/plans/2026-08-04-railway-transcriber-implementation-plan.md).

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
```

Run the frontend:

```powershell
pnpm dev
```

Run the current checks:

```powershell
uv run --project backend pytest
uv run --project backend ruff check backend
pnpm test
pnpm build
```

Do not commit recordings, transcripts, model files, `.env`, or generated presigned URLs.
