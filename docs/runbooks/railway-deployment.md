# Railway deployment runbook

This runbook deploys one private web service and one private transcription worker from the same
image. PostgreSQL keeps durable state, and a private Railway Bucket keeps original and playback
audio. Only the web service receives a public domain.

Railway configuration changes over time. The service files in this repository follow the current
[Config as Code reference](https://docs.railway.com/config-as-code/reference) and use a root
Dockerfile as described in Railway's [Dockerfile guide](https://docs.railway.com/builds/dockerfiles).

## 1. Create the Railway resources

Use a paid Railway plan and place all four resources in the same project and region:

1. PostgreSQL, named `Postgres` in the examples below.
2. A private Railway Bucket, named `Bucket` below.
3. A service from this GitHub repository named `transcriber-web`.
4. A second service from the same repository named `transcriber-worker`.

Do not add a public domain to the worker, PostgreSQL, or Bucket. Railway Buckets are private and
provide S3 credentials through reference variables; use `BUCKET`, not `RAILWAY_BUCKET_NAME`, as
the S3 bucket name. See [Storage Buckets](https://docs.railway.com/storage-buckets).

In each service's Settings, set the custom Config as Code path:

- Web: `/railway.web.json`
- Worker: `/railway.worker.json`

Both services build the same immutable `Dockerfile`. The web config runs migrations before start,
serves the compiled interface, and checks `/readyz`. The worker config runs one always-restarted
worker and allows 60 seconds for its SIGTERM checkpoint-safe shutdown.

The root `railway.json` and image command are a safe fallback for CLI uploads before those custom
paths are set: Railway's service name selects the web or worker process, and the web process applies
migrations before accepting traffic.

## 2. Generate owner credentials locally

Choose a username and a 6-12 digit PIN. Store only the Argon2id hash in Railway:

```powershell
uv run --project backend python -c "from argon2 import PasswordHasher; print(PasswordHasher().hash(input('PIN: ')))"
uv run --project backend python -c "import secrets; print(secrets.token_urlsafe(48))"
```

The first output becomes `APP_PIN_HASH`; the second becomes `APP_SESSION_SECRET`. Never add the
PIN, either output, or a Railway credential to Git or a screenshot.

## 3. Configure variables

Set these on both `transcriber-web` and `transcriber-worker`. Use Railway reference variables for
PostgreSQL and the Bucket so deployments wait for their dependencies.

| Variable | Value |
| --- | --- |
| `APP_ENV` | `production` |
| `APP_PUBLIC_ORIGIN` | `https://${{transcriber-web.RAILWAY_PUBLIC_DOMAIN}}` |
| `APP_USERNAME` | chosen owner username |
| `APP_PIN_HASH` | generated Argon2id hash |
| `APP_SESSION_SECRET` | generated random secret |
| `APP_SECURE_COOKIES` | `true` |
| `APP_LOG_LEVEL` | `INFO` |
| `DATABASE_URL` | `${{Postgres.DATABASE_URL}}` |
| `BUCKET_ENDPOINT` | `${{Bucket.ENDPOINT}}` |
| `BUCKET_NAME` | `${{Bucket.BUCKET}}` |
| `BUCKET_ACCESS_KEY_ID` | `${{Bucket.ACCESS_KEY_ID}}` |
| `BUCKET_SECRET_ACCESS_KEY` | `${{Bucket.SECRET_ACCESS_KEY}}` |
| `BUCKET_REGION` | `${{Bucket.REGION}}` |
| `BUCKET_URL_STYLE` | `virtual`, unless the Bucket Credentials tab says path-style |
| `WHISPER_MODEL` | `large-v3` |
| `WHISPER_DEVICE` | `cpu` |
| `WHISPER_COMPUTE_TYPE` | `int8` |
| `WHISPER_MODEL_CACHE` | `/data/model-cache` |
| `WORKER_SCRATCH_DIR` | `/tmp/transcriber-scratch` |
| `FFMPEG_PATH` | `ffmpeg` |
| `FFPROBE_PATH` | `ffprobe` |

Keep the fixed product limits from `.env.example`; they are validated at startup. Do not create a
manual `PORT` variable. Railway supplies it to the web service.

Set `RAILWAY_DEPLOYMENT_DRAINING_SECONDS=60` on the worker if the dashboard overrides the value in
`railway.worker.json`. Set the worker to exactly one replica. The web may use one replica for this
private application.

## 4. Attach the model-cache volume

Attach one Railway Volume to `transcriber-worker` at `/data/model-cache`. Start at 5 GB or larger
and expand it if the model cache approaches the limit. Railway mounts volumes only at runtime, not
during build or pre-deploy. The model may be downloaded again if the volume is intentionally
removed; recordings do not live on this volume.

See [Using Volumes](https://docs.railway.com/volumes). The image currently runs as root so the
mounted cache is writable. If the runtime UID is changed later, follow Railway's volume permission
guidance before redeploying.

## 5. Create the domain and Bucket CORS rule

Generate a Railway domain for `transcriber-web`, then confirm `APP_PUBLIC_ORIGIN` matches it exactly
with `https://` and no trailing slash. From a one-off shell in the web image, apply the minimal
Bucket rule:

```powershell
python scripts/configure_bucket_cors.py
```

The rule permits only the configured web origin and only `GET`, `HEAD`, and `PUT`, with the
`content-type` request header and `ETag` response header. Do not make the Bucket public.

## 6. Deploy and verify

Deploy `transcriber-web` first. Its pre-deploy command applies all Alembic migrations. Confirm:

- `/healthz` returns `ok`.
- `/readyz` returns `ready`.
- the public domain shows the login screen, not a transcript or API response.

Deploy `transcriber-worker`. Its first start downloads `large-v3` to the mounted cache and may take
several minutes. Wait for the structured `Worker started` log and normal CPU/memory activity.

Run an authentication smoke check without putting the PIN on the command line:

```powershell
$env:TRANSCRIBER_SMOKE_URL = "https://your-domain.up.railway.app"
$env:TRANSCRIBER_SMOKE_USERNAME = "owner"
$env:TRANSCRIBER_SMOKE_PIN = Read-Host "PIN"
./scripts/smoke_railway.ps1
```

For a full real transcription, add a short redistributable M4A, MP3, or WAV fixture:

```powershell
./scripts/smoke_railway.ps1 -AudioPath C:\path\to\short-smoke.m4a -Language en
```

The script uploads in restart-safe parts, waits for completion, checks playback, and verifies that
the displayed and downloaded transcript match. It never prints the transcript, signed URL, or PIN.

## 7. Required post-deploy recovery check

Use a synthetic recording long enough to produce at least two chunks. After History reports one
completed chunk, restart `transcriber-worker` from Railway. Confirm after recovery that:

1. The completed count never decreases.
2. The worker resumes the next unfinished chunk.
3. The final transcript has no duplicated overlap text.
4. The original and transcript remain after redeploying both services.

Then inspect web and worker logs for the smoke request. Logs may contain request IDs, stable status
codes, counts, and durations. They must not contain the PIN, transcript text, signed query strings,
Bucket credentials, filenames where unnecessary, or local audio paths.
