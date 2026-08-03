# Railway Transcriber Implementation Plan

**Date:** 2026-08-04
**Design:** `docs/superpowers/specs/2026-08-03-railway-transcriber-design.md`

## Objective

Implement the approved private Railway transcriber as a focused repository with a React browser client, FastAPI web/API service, PostgreSQL-backed durable workflow, private Railway Bucket integration, and a separate `faster-whisper` worker.

The work is complete only after local automated verification and a Railway smoke test prove login, resumable upload, chunked transcription, restart recovery, history, playback, TXT download, retry, and deletion.

## Execution rules

- Implement tasks in order because later tasks depend on contracts and migrations created earlier.
- Add failing tests before or alongside each behavior, then make the smallest implementation that passes.
- Keep commits scoped to the task boundaries below.
- Never commit real audio, transcripts, credentials, PINs, presigned URLs, or downloaded Whisper models.
- Reuse only the narrow `faster-whisper` and FFmpeg patterns from `proark1/podcastautomatisierung`; do not copy its unrelated podcast platform.
- Keep all worker actions idempotent and all user-facing errors free of paths, provider responses, and transcript text.
- Use decimal bytes for the product limit: `5_000_000_000`.
- Run the complete backend and frontend verification suites before Railway deployment.

## Planned repository shape

```text
transcriber/
├── backend/
│   ├── alembic/
│   ├── src/transcriber/
│   │   ├── api/
│   │   ├── worker/
│   │   ├── assembly.py
│   │   ├── auth.py
│   │   ├── config.py
│   │   ├── database.py
│   │   ├── media.py
│   │   ├── models.py
│   │   ├── repositories.py
│   │   ├── storage.py
│   │   └── whisper_engine.py
│   ├── tests/
│   └── pyproject.toml
├── frontend/
│   ├── src/
│   ├── tests/
│   ├── package.json
│   └── vite.config.ts
├── infra/
│   └── compose.yaml
├── scripts/
├── tests/e2e/
├── Dockerfile
├── railway.web.json
├── railway.worker.json
└── README.md
```

## Task 1: Scaffold the development and verification baseline

### Files

- Create `.gitignore`
- Create `.editorconfig`
- Create `.env.example`
- Create `backend/pyproject.toml`
- Create `backend/src/transcriber/__init__.py`
- Create `backend/tests/test_import.py`
- Create `frontend/package.json`
- Create `frontend/tsconfig.json`
- Create `frontend/vite.config.ts`
- Create `frontend/src/main.tsx`
- Create `frontend/src/App.tsx`
- Create `frontend/src/app.css`
- Create `frontend/src/App.test.tsx`
- Create `pnpm-workspace.yaml`
- Create `package.json`
- Create `infra/compose.yaml`
- Create `scripts/dev.ps1`
- Create `README.md`

### Work

1. Ignore `.env`, `.superpowers/`, Python caches, `.venv`, Node modules, build output, test output, local media, model caches, and scratch files.
2. Configure Python 3.12 with FastAPI, Uvicorn, SQLAlchemy 2, psycopg, Alembic, Pydantic Settings, boto3, Argon2, and `faster-whisper`. Add pytest, Ruff, mypy, and HTTPX development dependencies.
3. Configure Node 22, pnpm, React 19, TypeScript, Vite, Vitest, Testing Library, and Playwright.
4. Add a minimal accessible React shell and import smoke tests without product behavior.
5. Add local PostgreSQL and MinIO services for development only. Railway deployment must not depend on Docker Compose.
6. Add PowerShell helpers to start dependencies, run backend/frontend development servers, and execute checks without embedding secrets.
7. Document prerequisites and the approved architecture at a high level.

### Verification

```powershell
uv sync --project backend
uv run --project backend pytest backend/tests/test_import.py
uv run --project backend ruff check backend
pnpm install
pnpm --dir frontend test -- --run
pnpm --dir frontend build
```

### Commit

`chore: scaffold transcriber application`

## Task 2: Implement configuration, database foundations, and migrations

### Files

- Create `backend/src/transcriber/config.py`
- Create `backend/src/transcriber/database.py`
- Create `backend/src/transcriber/models.py`
- Create `backend/src/transcriber/repositories.py`
- Create `backend/alembic.ini`
- Create `backend/alembic/env.py`
- Create `backend/alembic/versions/0001_initial_schema.py`
- Create `backend/tests/test_config.py`
- Create `backend/tests/test_models.py`
- Create `backend/tests/test_recording_repository.py`

### Work

1. Add strict startup settings for database, bucket, authentication, upload limits, session lifetime, chunking, Whisper, scratch storage, and logging.
2. Reject missing production secrets, malformed Argon2 hashes, unsupported model settings, limits different from the approved four hours and `5_000_000_000` bytes, and unsafe cookie settings.
3. Define SQLAlchemy models for `auth_sessions`, `login_attempts`, `recordings`, `upload_sessions`, `upload_parts`, and `transcription_chunks`, including separate immutable-original and browser-playback object keys.
4. Encode recording and chunk statuses as constrained database enums or check constraints.
5. Add foreign keys, ordered unique chunk indexes, session-expiry indexes, lease indexes, and a PostgreSQL partial unique index that permits only one active recording globally.
6. Implement repository methods for status transitions with expected-state checks so invalid or duplicate transitions fail safely.
7. Add Alembic migration and migration tests against PostgreSQL, not SQLite.

### Verification

```powershell
uv run --project backend alembic -c backend/alembic.ini upgrade head
uv run --project backend pytest backend/tests/test_config.py backend/tests/test_models.py backend/tests/test_recording_repository.py
uv run --project backend mypy backend/src
```

### Commit

`feat: add durable transcriber data model`

## Task 3: Add single-user authentication and request security

### Files

- Create `backend/src/transcriber/auth.py`
- Create `backend/src/transcriber/api/__init__.py`
- Create `backend/src/transcriber/api/app.py`
- Create `backend/src/transcriber/api/dependencies.py`
- Create `backend/src/transcriber/api/routes_auth.py`
- Create `backend/src/transcriber/api/security.py`
- Create `backend/tests/test_auth.py`
- Create `backend/tests/test_auth_routes.py`
- Create `backend/tests/test_security_headers.py`

### Work

1. Verify the configured username and 6-to-12-digit PIN with Argon2 without storing the raw PIN.
2. Persist only an HMAC of each random session token. Store expiry, revocation, credential-version fingerprint, and a hashed security key for rate limiting.
3. Implement five-failures-per-15-minutes login lockout with generic invalid-credential responses.
4. Issue a seven-day `Secure`, `HttpOnly`, `SameSite=Lax` cookie and a server-stored CSRF token returned only to the authenticated client.
5. Require allowed Origin/Referer and the CSRF header for state-changing authenticated requests.
6. Add `/api/auth/login`, `/api/auth/session`, and `/api/auth/logout`.
7. Add production security headers, safe request IDs, JSON error envelopes, and log redaction.
8. Add `/healthz` without dependency checks and `/readyz` with a database check.

### Verification

```powershell
uv run --project backend pytest backend/tests/test_auth.py backend/tests/test_auth_routes.py backend/tests/test_security_headers.py
```

### Commit

`feat: secure transcriber authentication`

## Task 4: Implement private multipart storage and upload APIs

### Files

- Create `backend/src/transcriber/storage.py`
- Create `backend/src/transcriber/api/contracts.py`
- Create `backend/src/transcriber/api/routes_uploads.py`
- Create `backend/tests/test_storage.py`
- Create `backend/tests/test_upload_routes.py`
- Create `scripts/configure_bucket_cors.py`
- Modify `backend/src/transcriber/api/app.py`
- Modify `.env.example`

### Work

1. Define a storage protocol and boto3 implementation for multipart create, presigned part upload, list parts, complete, abort, HEAD, download, upload, delete, and presigned GET.
2. Generate opaque object keys under `recordings/{recording_uuid}/`; never put the display filename in a key.
3. Create 32 MiB multipart sessions after validating authentication, language, filename length, nonzero size, size limit, and absence of another active recording.
4. Provide APIs to authorize missing parts, reconcile confirmed parts against bucket state, complete an upload, and abort it.
5. Treat browser ETags and content types as untrusted. Verify bucket parts and final object size before queuing processing.
6. Make create and completion idempotent so page reloads do not create duplicate recordings or jobs.
7. Add a CORS configuration script limited to the deployed web origin, required methods, and required upload headers.
8. Test multipart resume, expired URLs, missing parts, wrong sizes, duplicate completion, cancellation, and unauthorized access against MinIO-compatible behavior.

### Verification

```powershell
uv run --project backend pytest backend/tests/test_storage.py backend/tests/test_upload_routes.py
```

### Commit

`feat: add resumable private audio uploads`

## Task 5: Extract safe media validation and durable chunk preparation

### Files

- Create `backend/src/transcriber/media.py`
- Create `backend/src/transcriber/worker/preparation.py`
- Create `backend/tests/fixtures/README.md`
- Create short synthetic audio fixtures through test helpers at runtime
- Create `backend/tests/test_media.py`
- Create `backend/tests/test_chunk_planning.py`
- Create `backend/tests/test_preparation.py`

### Work

1. Adapt the fixed-vector FFprobe and FFmpeg invocation patterns from `podcastautomatisierung` without importing its video constraints or database worker.
2. Validate at least one decodable audio stream, positive duration, four-hour maximum, and `5_000_000_000`-byte maximum.
3. Normalize accepted input to mono 16 kHz FLAC in an isolated temporary directory.
4. Create and persist an idempotent 128 kbps AAC/M4A playback copy so every accepted source is playable in supported browsers without changing the original.
5. Detect silence with a fixed FFmpeg filter configuration and parse only expected timing records.
6. Build nominal 20-minute core intervals, select the best silence within plus or minus 30 seconds, and add bounded five-second overlaps.
7. Persist all chunk definitions before marking preparation complete.
8. Render and upload each working FLAC chunk idempotently. Skip chunks whose stored object size and database record already verify.
9. Map corrupt, unsupported, oversized, and too-long inputs to stable safe error codes.
10. Generate all test audio during tests; do not commit customer media or large binary fixtures.

### Verification

```powershell
uv run --project backend pytest backend/tests/test_media.py backend/tests/test_chunk_planning.py backend/tests/test_preparation.py
```

### Commit

`feat: prepare restart-safe audio chunks`

## Task 6: Extract Whisper inference and deterministic text assembly

### Files

- Create `backend/src/transcriber/whisper_engine.py`
- Create `backend/src/transcriber/assembly.py`
- Create `backend/tests/test_whisper_engine.py`
- Create `backend/tests/test_assembly.py`

### Work

1. Extract the existing `faster-whisper` model setup, language forcing, beam size, VAD, segment filtering, and empty-result error behavior behind an injectable `Transcriber` protocol.
2. Load `large-v3` with CPU `int8` by default and reuse one model instance for all claimed chunks.
3. Support only `en`, `de`, and `tr`; reject any other stored value before inference.
4. Store internal timestamped segments and whitespace-normalized chunk text without logging either.
5. Implement deterministic overlap removal using the longest normalized suffix/prefix match within the configured boundary window.
6. Build readable paragraphs at pauses of at least 2.5 seconds or the next sentence boundary after approximately 800 characters.
7. Preserve words and punctuation; do not correct, summarize, translate, or introduce an LLM dependency.
8. Produce UTF-8 text with blank lines between paragraphs and one trailing newline.
9. Test model calls with fakes and test assembly with punctuation, Unicode, German compounds, Turkish casing, repeated phrases, no-overlap boundaries, empty segments, and idempotent reruns.

### Verification

```powershell
uv run --project backend pytest backend/tests/test_whisper_engine.py backend/tests/test_assembly.py
```

### Commit

`feat: transcribe and assemble readable text`

## Task 7: Build the leased background worker and recovery behavior

### Files

- Create `backend/src/transcriber/worker/__init__.py`
- Create `backend/src/transcriber/worker/__main__.py`
- Create `backend/src/transcriber/worker/runner.py`
- Create `backend/src/transcriber/worker/leases.py`
- Create `backend/src/transcriber/worker/cleanup.py`
- Create `backend/tests/test_worker_repository.py`
- Create `backend/tests/test_worker_runner.py`
- Create `backend/tests/test_worker_recovery.py`

### Work

1. Claim preparation and chunk work with PostgreSQL `FOR UPDATE SKIP LOCKED` and expected-state transitions.
2. Maintain processing and chunk heartbeats from a separate database connection while FFmpeg or Whisper runs.
3. Reclaim work after five minutes without a heartbeat.
4. Execute preparation, then pending chunks in order, then deterministic assembly.
5. Give each chunk three total automatic attempts with one- and five-minute retry delays.
6. Mark the recording failed after a third chunk failure while preserving completed chunks and prepared objects.
7. Implement manual retry repository behavior that resets only incomplete chunks and rejects retry while another recording is active.
8. Make commit-after-inference, duplicate claims, process termination, and restart safe.
9. Remove temporary normalized chunks after successful assembly but retain database chunk evidence.
10. Handle SIGTERM by stopping new claims, maintaining the current heartbeat during a bounded drain, and leaving unfinished work reclaimable.

### Verification

```powershell
uv run --project backend pytest backend/tests/test_worker_repository.py backend/tests/test_worker_runner.py backend/tests/test_worker_recovery.py
```

### Commit

`feat: add recoverable transcription worker`

## Task 8: Complete recording, playback, transcript, retry, and deletion APIs

### Files

- Create `backend/src/transcriber/api/routes_recordings.py`
- Create `backend/src/transcriber/deletion.py`
- Create `backend/tests/test_recording_routes.py`
- Create `backend/tests/test_playback_routes.py`
- Create `backend/tests/test_deletion.py`
- Modify `backend/src/transcriber/api/app.py`

### Work

1. Add authenticated list and detail endpoints with safe progress derived from actual stage and completed chunks.
2. Add a short-lived presigned playback URL only for the verified browser-compatible playback object; retain the immutable original separately.
3. Add plain transcript and attachment responses. The displayed API text and downloaded UTF-8 TXT bytes must match exactly.
4. Add manual retry using the worker recovery contract.
5. Implement deletion as `deleting` state, multipart abort, object cleanup, database cleanup, and retryable reconciliation.
6. Reject playback, transcript, retry, and processing after deletion begins.
7. Ensure object-storage failure never presents a recording as fully deleted.
8. Cover unauthorized, missing, active, failed, completed, and deleting states.

### Verification

```powershell
uv run --project backend pytest backend/tests/test_recording_routes.py backend/tests/test_playback_routes.py backend/tests/test_deletion.py
```

### Commit

`feat: expose secure recording history APIs`

## Task 9: Build the login and application shell

### Files

- Create `frontend/src/api/client.ts`
- Create `frontend/src/api/contracts.ts`
- Create `frontend/src/auth/AuthProvider.tsx`
- Create `frontend/src/auth/LoginPage.tsx`
- Create `frontend/src/layout/AppShell.tsx`
- Create `frontend/src/components/StatusMessage.tsx`
- Create `frontend/tests/LoginPage.test.tsx`
- Create `frontend/tests/AppShell.test.tsx`
- Modify `frontend/src/App.tsx`
- Modify `frontend/src/app.css`

### Work

1. Implement a typed fetch client with credentials, CSRF headers, JSON error parsing, abort support, and session-expiry handling.
2. Build the username/PIN login screen with generic errors, lockout feedback, keyboard operation, and correct autocomplete attributes.
3. Build the signed-in shell with product identity, username, logout, responsive navigation, and live-region status feedback.
4. Establish the approved visual direction: focused utility, readable typography, restrained color, obvious progress, and no podcast-platform styling.
5. Respect reduced motion, visible focus, touch targets, and mobile stacking.

### Verification

```powershell
pnpm --dir frontend test -- --run LoginPage AppShell
pnpm --dir frontend build
```

### Commit

`feat: add private transcriber app shell`

## Task 10: Build resumable upload and durable progress UI

### Files

- Create `frontend/src/recordings/NewTranscription.tsx`
- Create `frontend/src/recordings/useMultipartUpload.ts`
- Create `frontend/src/recordings/ProcessingStatus.tsx`
- Create `frontend/src/recordings/uploadPersistence.ts`
- Create `frontend/tests/NewTranscription.test.tsx`
- Create `frontend/tests/useMultipartUpload.test.tsx`
- Create `frontend/tests/ProcessingStatus.test.tsx`
- Modify `frontend/src/api/contracts.ts`
- Modify `frontend/src/app.css`

### Work

1. Add drag-and-drop and file-picker input with M4A, MP3, WAV, AAC, FLAC, OGG, OPUS, and MP4 guidance.
2. Require English, German, or Turkish and show filename and size before submission.
3. Upload 32 MiB parts with bounded concurrency, confirmed-part persistence, cancellation, and progress based on confirmed bytes.
4. Persist only upload/session IDs and the local file signature in local storage; never persist URLs, credentials, audio, or transcript text there.
5. Resume after reload by asking the user to reselect the same file and uploading only missing parts.
6. Poll active recording state and show uploading, validating, normalizing, chunking, `Transcribing N of M`, assembling, completed, and failed.
7. State clearly that the page may close while server-side processing continues.
8. Disable new upload and retry actions while another recording is active.

### Verification

```powershell
pnpm --dir frontend test -- --run NewTranscription useMultipartUpload ProcessingStatus
pnpm --dir frontend build
```

### Commit

`feat: add resumable transcription workflow`

## Task 11: Build history, playback, transcript, retry, and deletion UI

### Files

- Create `frontend/src/recordings/HistorySidebar.tsx`
- Create `frontend/src/recordings/RecordingPage.tsx`
- Create `frontend/src/recordings/AudioPlayer.tsx`
- Create `frontend/src/recordings/TranscriptView.tsx`
- Create `frontend/src/recordings/DeleteRecordingDialog.tsx`
- Create `frontend/tests/HistorySidebar.test.tsx`
- Create `frontend/tests/RecordingPage.test.tsx`
- Create `frontend/tests/TranscriptView.test.tsx`
- Create `frontend/tests/DeleteRecordingDialog.test.tsx`
- Modify `frontend/src/App.tsx`
- Modify `frontend/src/app.css`

### Work

1. Build the approved history-sidebar layout and responsive stacked mobile layout.
2. Show filename, language, creation date, verified duration, and current status.
3. Refresh short-lived playback URLs when needed without exposing bucket credentials.
4. Render the clean transcript in a readable long-form container.
5. Implement clipboard copy with accessible success/failure feedback and a fallback when clipboard permission is unavailable.
6. Download the server-provided TXT rather than regenerating text in the browser.
7. Show manual Retry only for failed recordings and explain that completed chunks remain saved.
8. Require explicit deletion confirmation, show `deleting`, and keep the item until cleanup succeeds.

### Verification

```powershell
pnpm --dir frontend test -- --run HistorySidebar RecordingPage TranscriptView DeleteRecordingDialog
pnpm --dir frontend build
```

### Commit

`feat: add recording history and transcript tools`

## Task 12: Add full integration, browser, security, and recovery coverage

### Files

- Create `tests/e2e/auth.spec.ts`
- Create `tests/e2e/transcription.spec.ts`
- Create `tests/e2e/recovery.spec.ts`
- Create `tests/e2e/deletion.spec.ts`
- Create `tests/e2e/accessibility.spec.ts`
- Create `playwright.config.ts`
- Create `scripts/test-integration.ps1`
- Create `.github/workflows/ci.yml`
- Modify backend and frontend test helpers as required

### Work

1. Start isolated PostgreSQL, MinIO, API, fake deterministic worker, and built frontend for end-to-end tests.
2. Cover invalid and valid login, upload, resume, progress, history, playback, copy, byte-identical TXT, retry, and deletion.
3. Kill the worker after one completed chunk and prove restart does not repeat it.
4. Inject a transient chunk failure and bucket-deletion failure, then prove bounded recovery.
5. Verify every unauthenticated recording, upload, playback, transcript, retry, and deletion request is denied.
6. Verify CSRF, Origin, lockout, content-type, object-key, path, and safe-error boundaries.
7. Run automated accessibility checks plus keyboard-only critical flows.
8. Add CI jobs for Ruff, mypy, pytest, frontend tests/build, Playwright, and migration checks.

### Verification

```powershell
uv run --project backend ruff check backend
uv run --project backend mypy backend/src
uv run --project backend pytest
pnpm --dir frontend test -- --run
pnpm --dir frontend build
pnpm exec playwright test
```

### Commit

`test: verify complete transcriber workflow`

## Task 13: Package and deploy both Railway services

### Files

- Create `Dockerfile`
- Create `railway.web.json`
- Create `railway.worker.json`
- Create `scripts/smoke_railway.ps1`
- Create `docs/runbooks/railway-deployment.md`
- Create `docs/runbooks/operations.md`
- Modify `.env.example`
- Modify `README.md`

### Work

1. Build the React client in a Node stage and install the locked Python environment plus FFmpeg in the runtime stage.
2. Serve the compiled single-page client from FastAPI with a safe non-API fallback route, and run the web and worker from the same immutable image with different Railway start commands.
3. Configure web health/readiness, migration pre-deploy, worker `Always` restart, bounded draining, and structured stderr logging.
4. Mount the worker model-cache volume and set Hugging Face/CTranslate2 cache paths there. Keep customer data in the bucket and scratch data on paid-plan ephemeral storage.
5. Document creation and reference wiring for PostgreSQL, private Bucket, web service, worker service, public domain, CORS, credentials, PIN hash, and session secret.
6. Document CPU/memory tuning, `large-v3` cold start, cost controls, backup expectations, monitoring, retry, and deletion recovery.
7. Configure Railway without placing secrets in repository files.
8. Run migrations and deploy both services.
9. Execute a short real Whisper smoke transcription, then restart the worker during a synthetic multi-chunk run and verify recovery.
10. Inspect logs to prove no PIN, presigned URL, local audio path, or transcript text was emitted.

### Verification

```powershell
docker build -t transcriber:local .
./scripts/smoke_railway.ps1
git status --short
```

### Commit

`docs: add Railway deployment and operations`

## Final acceptance pass

Before declaring implementation complete:

1. Run every command from Task 12 on a clean checkout.
2. Confirm database migrations apply from an empty PostgreSQL database and upgrade without destructive data loss.
3. Confirm repository history and working tree contain no audio, transcript, model, secret, or presigned URL.
4. Confirm iPhone-style M4A, MP3, WAV, AAC, FLAC, OGG/OPUS, and MP4-audio fixtures pass the upload-to-text path.
5. Confirm worker restart and failed-chunk retry preserve completed chunk rows and do not repeat their inference calls.
6. Confirm History retains original audio and final text across web and worker redeployments.
7. Confirm deletion removes bucket objects and database records and remains visibly retryable during injected storage failure.
8. Confirm the deployed public domain is unusable without login and all storage objects remain private.
9. Confirm the displayed transcript and downloaded TXT are byte-equivalent UTF-8 text.
10. Confirm the implementation matches all twelve acceptance criteria in the approved design.
