# Railway Transcriber Design

**Date:** 2026-08-03
**Status:** Approved for implementation planning

## Goal

Build a private Railway-hosted application that accepts one audio recording at a time, transcribes it with an accuracy-focused `faster-whisper` pipeline, and keeps both the original recording and a clean, readable transcript until the user deletes them.

The app must work well for two-to-three-hour recordings, including iPhone Voice Memos, and must recover from worker restarts without repeating every completed transcription step.

## Approved product decisions

- The complete application runs on Railway rather than on the user's computer.
- A single user signs in with a username and numeric PIN.
- The app accepts one active upload or transcription at a time.
- The language selector contains English, German, and Turkish. There is no automatic-language option in version 1.
- Version 1 produces clean plain text, not speaker labels, visible timestamps, subtitles, or translations.
- Completed transcripts have Copy and Download `.txt` actions.
- The app keeps a permanent history containing both the original audio and transcript.
- A history item includes audio playback and manual deletion.
- Long recordings are divided into durable chunks. Completed chunks are not repeated after a failure.
- Transcription prioritizes accuracy over speed and compute cost.

## Scope

### Included

- Login and logout for one configured user
- Direct, resumable multipart uploads to a private Railway Storage Bucket
- M4A, MP3, WAV, AAC, FLAC, OGG, OPUS, and MP4 files with a decodable audio stream
- A 5 GB (5,000,000,000-byte) and four-hour limit per recording
- English (`en`), German (`de`), and Turkish (`tr`) transcription
- Accuracy-focused `faster-whisper` transcription with voice-activity detection
- Durable upload, preparation, chunk, and assembly state in PostgreSQL
- Processing progress based on real stages and completed chunks
- Browser-compatible audio playback through short-lived authorized URLs while preserving the immutable original
- Permanent history, clean transcript display, copy, TXT download, retry, and deletion
- Railway deployment, migrations, health endpoints, and operational documentation

### Not included

- Multiple simultaneous uploads or batch queues
- Multiple users, invitations, teams, or sharing links
- Speaker diarization or speaker names
- Transcript editing
- Visible timestamps or synchronized transcript playback
- SRT, VTT, or other subtitle exports
- Translation or AI rewriting
- Live microphone transcription
- Native iOS or Android applications
- Podcast editing, clip generation, publishing, or other features from the source repository

## Reuse from `proark1/podcastautomatisierung`

The private podcast repository already contains a working CPU `faster-whisper` path in `workers/media/src/value_media/runner.py` and safe FFmpeg invocation patterns in `workers/media/src/value_media/media.py`.

The new application will extract and adapt only the narrow transcription behavior:

- `WhisperModel` loading with CPU `int8` computation
- Beam size 5
- Voice-activity detection
- Explicit language codes
- Safe iteration and filtering of returned segments
- Empty-transcript detection
- Fixed-vector FFmpeg and FFprobe calls with captured output
- Safe errors that never log transcript text

The larger podcast monorepo will not be copied. Its PostgreSQL schema, S3 workflow, Keycloak integration, video-only validation, editing tools, and publishing services are unrelated to this product. The original uploader also accepts only constrained MP4 video, so the new upload and media-validation boundary must be audio-focused.

## System architecture

One repository contains a browser client, a web/API service, a transcription worker, shared Python modules, migrations, and tests.

### Browser client

A React, TypeScript, and Vite single-page interface provides login, upload, language selection, durable progress, history, playback, transcript reading, copying, downloading, retry, and deletion.

The built static client is served by the web service. The browser never receives Railway bucket credentials. It receives only short-lived, object-specific upload or playback URLs after an authenticated API request.

### Web/API service

A FastAPI service using SQLAlchemy 2, psycopg, and Alembic owns:

- Authentication and server-side sessions
- Recording and history APIs
- Multipart-upload creation, part authorization, completion, and resume state
- Playback authorization
- Copy/download transcript responses
- Retry and deletion commands
- Health and readiness endpoints

The service does not perform Whisper inference and does not proxy large upload bodies.

### Transcription worker

A separate private Railway service uses the same shared Python package and PostgreSQL database. It claims preparation and transcription work from PostgreSQL, downloads private objects, invokes FFmpeg and `faster-whisper`, checkpoints results, assembles final text, and deletes temporary working objects after success.

Only one worker replica runs in version 1. PostgreSQL row locking and leases keep the design safe if a second replica is added later. Redis is unnecessary for the first version.

### PostgreSQL

PostgreSQL stores authentication sessions, recording metadata, upload state, processing state, chunk definitions, chunk results, errors, and final transcript text. Large audio binaries are not stored as database blobs.

### Railway Storage Bucket

A private S3-compatible Railway Bucket stores original recordings, browser-compatible AAC/M4A playback copies, and temporary normalized chunks. The browser uploads directly with presigned multipart requests. Playback uses a short-lived presigned GET URL generated only after authorization.

Railway documents private buckets, presigned URLs, and multipart uploads as supported capabilities:

- <https://docs.railway.com/storage-buckets>
- <https://docs.railway.com/storage-buckets/uploading-serving>

## Data model

All identifiers exposed in object keys or APIs are random UUIDs. Original filenames remain metadata and are never used as filesystem paths or object-key path components.

### `auth_sessions`

- Session ID and hash of the opaque browser token
- Creation, last-use, and expiration timestamps
- Revocation timestamp
- Source IP fingerprint used only for security controls

### `recordings`

- Recording ID
- Original display filename and reported content type
- Verified byte size, duration, and detected container/codec details
- Selected language: `en`, `de`, or `tr`
- Original bucket object key
- Browser-compatible playback object key once preparation succeeds
- Current status, stage, completed-chunk count, and total-chunk count
- Safe error code and user-facing error category
- Final clean transcript text
- Created, updated, completed, and deletion timestamps
- Processing lease owner, heartbeat, and lease expiration

Recording statuses are:

`uploading`, `queued`, `validating`, `normalizing`, `chunking`, `transcribing`, `assembling`, `completed`, `failed`, and `deleting`.

### `upload_sessions` and `upload_parts`

- Railway bucket multipart upload ID
- Expected filename, content type, and byte size
- Expiration and completion state
- Confirmed part number, ETag, and byte size

The database is the source of truth for resuming an interrupted upload. Browser local storage remembers only the recording ID, upload-session ID, and a non-secret file signature so the same local file can be selected again.

### `transcription_chunks`

- Recording ID and zero-based ordered index
- Nominal core start and end time
- Actual overlapped audio start and end time
- Temporary bucket object key
- Status: `pending`, `running`, `completed`, or `failed`
- Attempt count, heartbeat, and lease expiration
- Internal timestamped Whisper segments required for assembly
- Clean chunk text and safe error code

Chunk results are written transactionally before a chunk is marked completed.

## Authentication and security

The public Railway domain is protected by application login.

- Railway secrets provide `APP_USERNAME`, an Argon2id hash of a 6-to-12-digit PIN, and a random secret used to HMAC session tokens and CSRF values.
- The raw PIN is never committed, logged, or stored in PostgreSQL.
- Login compares username and PIN in constant-time-compatible library paths and returns the same response for every invalid credential combination.
- Five failed attempts for one username and IP combination within 15 minutes trigger a 15-minute lockout.
- A successful login creates a random server-side session with a seven-day maximum lifetime.
- The browser receives only a `Secure`, `HttpOnly`, `SameSite=Lax` session cookie.
- Logout and PIN-secret rotation revoke existing sessions.
- State-changing requests require same-origin checks and CSRF protection.
- Every recording, upload, playback, transcript, retry, and deletion endpoint requires authentication.
- Bucket credentials are available only to Railway services. Presigned URLs are short-lived and limited to one object and operation.
- Transcript text, PINs, cookie values, presigned URLs, local scratch paths, and bucket credentials never appear in application logs.
- Uploaded filenames and content types are untrusted display metadata. Media acceptance depends on server-side FFprobe and decoding.

## Upload flow

1. The user signs in and selects a file plus English, German, or Turkish.
2. The client rejects an empty file or a reported size above 5,000,000,000 bytes before creating an upload.
3. The API creates a recording and multipart upload session.
4. The client uploads 32 MiB parts directly to the private bucket with short-lived presigned URLs.
5. Each confirmed part and ETag is saved in PostgreSQL. The API can reissue URLs for missing parts without restarting the upload.
6. On completion, the API finalizes the multipart object, verifies its object size, marks the recording `queued`, and returns immediately.
7. The worker later performs authoritative media validation.

Only one recording may be in `uploading`, `queued`, or an active processing status. The New transcription action remains disabled until that recording is completed, failed, or deleted.

Manual Retry is also an active-processing action. It remains unavailable while another recording is uploading or processing.

The 5 GB multipart design avoids Railway's long-request constraint and keeps large binary traffic out of the web service. Railway currently documents a maximum public HTTP request duration of 15 minutes:

<https://docs.railway.com/networking/public-networking/specs-and-limits>

## Media validation and preparation

The worker downloads the original object into an isolated temporary directory and runs FFprobe with a fixed argument vector.

Acceptance requires:

- At least one decodable audio stream
- A positive duration no greater than four hours
- A verified size no greater than 5,000,000,000 bytes
- A container and codec that the installed FFmpeg build can decode safely

The browser's extension and MIME filters are usability hints, not the security boundary. This allows common iPhone `.m4a` Voice Memos while rejecting renamed or corrupt files cleanly.

The worker normalizes accepted input to mono 16 kHz FLAC working audio. It also creates a 128 kbps AAC/M4A playback copy so every accepted source has a browser-compatible player asset. The original object is immutable and is never overwritten.

## Chunk planning

The normalized timeline is divided into nominal 20-minute core intervals.

- For each nominal boundary, the worker searches up to 30 seconds before or after it for the best silence boundary.
- If no safe silence is found, the exact nominal boundary is used.
- Each actual chunk includes five seconds of audio before and after its core interval, bounded by the recording start and end.
- Core and actual boundaries are saved before transcription starts.
- Working chunks use FLAC to reduce bucket and transfer size without losing audio information.
- Prepared chunks remain available while the recording is incomplete or failed, allowing exact retry.

If preparation fails before chunk definitions are complete, preparation may repeat. Once chunk definitions and objects are complete, no finished Whisper chunk is repeated.

## Whisper transcription

Version 1 defaults to `large-v3` for accuracy. The model name remains an environment setting so operational benchmarking can change the deployment without a code migration.

The worker uses:

- `faster-whisper`
- CPU `int8` computation
- Beam size 5
- `vad_filter=True`
- The selected fixed language code for every chunk
- Timestamped segments retained internally for ordering and overlap handling

The model is loaded once and reused across chunks. A Railway volume mounted only on the worker caches model files between deployments and restarts; durable customer audio remains in the bucket, not on this volume.

Each chunk is checkpointed independently. The worker maintains a heartbeat while inference runs. A running chunk with no heartbeat for five minutes becomes reclaimable after a worker crash.

Each chunk receives at most three automatic attempts, with retry delays of one and five minutes after the first and second failures. If the third attempt fails, the recording becomes `failed` and exposes a manual Retry action. Manual retry resets only failed or incomplete chunks; completed chunk results remain unchanged.

## Transcript assembly

Assembly is deterministic and repeatable.

1. Read completed chunks in index order.
2. Offset internal segment times onto the original recording timeline.
3. Normalize whitespace without changing words.
4. Compare the normalized word suffix and prefix around each five-second overlap and remove the longest matching repeated boundary sequence.
5. Join sentences into paragraphs. Start a new paragraph after a pause of at least 2.5 seconds or, at the next sentence boundary, when the paragraph exceeds approximately 800 characters.
6. Preserve Whisper's words and punctuation. Do not summarize, paraphrase, translate, or silently correct content.
7. Save the final text to PostgreSQL and mark the recording completed in one transaction.

The result shown and downloaded is plain UTF-8 text with blank lines between paragraphs and a trailing newline. The TXT download content must exactly match the transcript displayed in the app.

After successful assembly, temporary normalized chunks are removed from the bucket through idempotent cleanup. Their database rows and transcript results remain as processing evidence. The immutable original audio and final transcript remain until manual deletion.

## User interface

### Login

A compact login screen asks for username and PIN. It does not reveal whether the username or PIN was wrong. A successful login opens History.

### History and recording view

The desktop layout uses a history sidebar and a main recording area. Mobile layouts stack the same content.

History items show filename, selected language, creation time, duration when known, and status. An active item shows the real stage or completed-chunk count, such as `Transcribing 3 of 8`.

A completed recording view provides:

- Original filename, date, language, and duration
- Browser audio playback of the derived AAC/M4A copy using a short-lived authorized URL
- Complete clean transcript
- Copy text
- Download `.txt`
- Delete recording

### New transcription

The new-recording form contains:

- Drag-and-drop and file-picker input
- Accepted-format guidance including iPhone M4A
- English, German, or Turkish selector
- Upload and transcribe action
- Multipart upload progress and resume guidance

The selected file is never auto-submitted. The user sees its filename and size before starting.

### Processing and failure states

The recording page distinguishes upload, validation, normalization, chunk preparation, transcription, and assembly. It explicitly states that the page may be closed while background processing continues.

Expected failures use actionable language:

- Unsupported or corrupt audio: choose another file
- Recording exceeds four hours or 5 GB: use a shorter or smaller source
- Interrupted upload: reselect the same file and resume
- Transcription retries exhausted: retry incomplete chunks
- Storage or service temporarily unavailable: retry later without losing completed work

Safe error codes support diagnostics without exposing provider responses, paths, or transcript text.

## Deletion and retention

Recordings have no automatic expiry in version 1.

Deletion is an idempotent workflow rather than a single database cascade:

1. Mark the recording `deleting` so new playback, retry, and processing cannot begin.
2. Revoke active multipart uploads.
3. Delete the original object, playback copy, and any remaining working objects from the bucket.
4. Delete upload parts, chunk results, and recording metadata from PostgreSQL.
5. Retry object cleanup safely if Railway storage is temporarily unavailable.

The UI shows deletion in progress until both storage and database cleanup succeed. A failed cleanup remains retryable and is never presented as fully deleted.

## Railway deployment

The Railway project contains:

- One public web/API service with `/healthz` and `/readyz`
- One private transcription worker with an accuracy-oriented CPU and memory allocation
- One PostgreSQL service
- One private Storage Bucket
- One worker volume used only for the Whisper model cache

Version 1 requires a paid Railway plan. Railway currently provides 100 GB of ephemeral storage per paid service deployment, which is sufficient for the bounded 5 GB original plus normalized scratch data and prepared chunks; Free and Trial storage is too small for the approved limit. The worker deletes scratch data at the end of every job and on process shutdown where possible.

<https://docs.railway.com/services#ephemeral-storage>

Both application services deploy from the same repository and version. Database migrations run as a Railway pre-deploy command before the web service switches traffic. The worker starts only after migrations are compatible.

The worker uses an `Always` restart policy on a paid Railway plan. Web health checks verify serving readiness; worker heartbeats and stale-lease recovery provide continuous job-level health because Railway deployment health checks are not continuous monitoring.

Environment configuration includes database and bucket references, authentication hashes/secrets, model name, upload limits, chunk settings, session lifetime, and log level. Production defaults match the constants in this design, and startup rejects missing or invalid security configuration.

## Error handling and observability

- User errors and retryable infrastructure errors have separate safe codes.
- API errors contain no stack traces in production.
- Every request and worker claim receives a correlation ID.
- Logs include recording UUID, stage, chunk index, attempt count, elapsed time, and safe error code.
- Logs exclude filenames when unnecessary and always exclude audio content and transcript text.
- Metrics cover upload completion, queue age, stage duration, chunk duration, retry count, completion, failure, and deletion cleanup.
- Progress is derived from completed stages and chunks. The app does not fabricate remaining-time percentages for Whisper.
- Worker operations and assembly are idempotent so duplicate delivery or a crash after commit cannot duplicate text.

## Verification strategy

### Unit tests

- Language mapping for English, German, and Turkish
- File-size and duration limits
- FFprobe validation and corrupt-input handling
- Silence-boundary selection and exact-boundary fallback
- Five-second overlap calculations at recording edges
- Boundary word deduplication without lost or repeated text
- Paragraph formatting and exact TXT output
- Authentication hashing, session expiry, CSRF checks, and lockout behavior
- Status transitions, lease expiry, retry delays, and retry limits
- Idempotent assembly and deletion state transitions

### Integration tests

- PostgreSQL migrations and constraints
- Multipart create, part confirmation, resume, completion, and abort against an S3-compatible test service
- Private playback authorization and expired URL behavior
- Worker claim and heartbeat behavior with PostgreSQL locking
- Upload-to-completed flow with short English, German, and Turkish fixtures
- M4A iPhone-style AAC input plus MP3, WAV, FLAC, OGG/OPUS, and MP4-audio fixtures
- Failed chunk retry that preserves completed chunks
- Worker termination during transcription followed by stale-lease recovery
- Deletion with transient bucket failure followed by successful cleanup retry

Tests use short synthetic or redistributable fixtures and never commit private recordings or real transcripts.

### Browser end-to-end tests

- Invalid and valid login
- Choose file and language, upload, and observe durable progress
- Reload or close/reopen during upload and processing
- History navigation and completed audio playback
- Copy transcript and download byte-identical UTF-8 TXT
- Failed recording and manual retry
- Permanent history across a fresh browser session
- Confirmed deletion removes the history item and prevents playback or download
- Unauthorized API, upload, playback, and transcript access are denied

### Railway smoke verification

- Deploy migrations, web service, and worker successfully
- Confirm health and readiness endpoints
- Confirm the bucket is private
- Upload and transcribe one short fixture through the public domain
- Restart the worker during a multi-chunk test and verify completed chunks remain complete
- Confirm logs contain safe metadata but no PIN, presigned URL, audio path, or transcript text

## Acceptance criteria

The first version is complete when all of the following are true:

1. A user can sign in with the configured username and PIN, and unauthenticated users cannot access recordings or storage URLs.
2. An iPhone M4A recording and every other listed common format can be uploaded directly to Railway storage.
3. Upload interruption can resume without retransmitting confirmed parts.
4. A recording up to four hours and 5 GB is validated, normalized, divided into durable chunks, and transcribed in the chosen language.
5. Restarting the worker after completed chunks does not repeat those chunks.
6. A failed chunk can be retried without restarting the entire recording.
7. The final transcript is clean readable text with no overlap duplication, visible timestamps, or AI rewriting.
8. The displayed transcript can be copied and downloaded as byte-equivalent UTF-8 TXT.
9. The recording can be played from History through an authenticated, short-lived URL for its browser-compatible playback copy while the immutable original remains stored.
10. Audio and transcript survive application redeployments and remain until manual deletion.
11. Manual deletion removes the original audio, temporary objects, transcript, and associated database records through a verifiable retry-safe workflow.
12. Automated tests and a Railway smoke test cover the complete happy path, recovery path, security boundary, and deletion path.
