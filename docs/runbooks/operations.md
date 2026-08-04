# Operations runbook

## Normal service shape

- `transcriber-web`: one public web/API replica, with `/readyz` health checking.
- `transcriber-worker`: one private worker replica with restart policy `ALWAYS`.
- PostgreSQL: sessions, upload metadata, leases, checkpoints, safe errors, and final text.
- Private Bucket: immutable originals, browser playback copies, and temporary chunk audio.
- Worker Volume: only the downloadable Whisper model cache.

Railway gives paid deployments substantially more ephemeral disk than Free/Trial, which is needed
for the approved 5 GB upload plus normalized scratch files. Current platform behavior is documented
under [ephemeral storage](https://docs.railway.com/deployments/reference). Scratch files are
disposable; PostgreSQL and the Bucket are the durable sources of truth.

## Capacity and cold starts

Start the worker at 8 vCPU and 16 GB memory for CPU `large-v3` transcription, then measure real
recordings before reducing resources. A two-to-three-hour recording can take a long time on CPU.
More CPU improves speed, not recognition quality. Keep only one worker replica because the product
allows one active recording and the database lease is designed for a single durable job stream.

The first worker start downloads the model and warms CTranslate2. Keep `/data/model-cache` mounted
to avoid repeating this cost. Alert on volume capacity above 80%, but do not back up the model cache;
it can be recreated.

## What to monitor

Use Railway service metrics and logs for:

- web readiness failures, HTTP 5xx rate, and repeated 401/403/429 responses;
- queue age and a recording remaining in one stage longer than expected;
- worker CPU, memory, ephemeral disk, restarts, and model-cache volume usage;
- upload completion, completed/total chunks, retry count, and safe failure code;
- PostgreSQL storage/connections and Bucket storage growth;
- recordings remaining in `deleting` because cleanup has not reconciled.

Application logs are structured JSON on standard output. Search by request ID and safe error code. Never add
audio bytes, transcript text, PINs, session/CSRF values, signed URLs, credentials, or absolute media
paths to logs.

## Common recovery actions

### Worker restarts during a long recording

Wait for the five-minute lease to expire, or restart the worker once. The next claim starts from the
first incomplete chunk. Do not reset database rows or delete prepared chunks. Completed chunk text
must remain unchanged.

### A recording reports Needs attention

Open it and use **Retry unfinished parts**. Completed chunks are preserved. If another recording is
active, finish or delete that recording first. Repeated `media_unreadable` or
`duration_limit_exceeded` errors require a different input, not infrastructure retry.

### Model download fails

Confirm worker egress, volume free space, and write access to `/data/model-cache`. Restart after the
underlying problem is fixed. Do not delete the model volume during an active transcription.

### Upload pauses

The user chooses the same local file again. The browser retains only the upload ID and file
signature; the API reconciles Bucket-confirmed parts. Never instruct the user to clear browser
storage unless the upload is deliberately abandoned.

### Deletion remains in progress

Do not delete the PostgreSQL row manually. The worker's idle cleanup retries multipart aborts and
object deletion, then removes the row only after the Bucket confirms cleanup. Check Bucket access
and worker health, then restart the worker if needed.

### PostgreSQL or Bucket outage

Keep the worker stopped if either durable dependency is unstable. Restore PostgreSQL first, then
verify the Bucket and restart the web and worker. Leases and idempotent checkpoints make replay safe.

## Backups and retention

Enable automated PostgreSQL backups and periodically perform a restore drill in a non-production
environment. The Bucket contains the immutable original audio; establish an organization-level
export or replication policy if those files require a second backup beyond Railway's durability.
The app intentionally retains originals and transcripts until confirmed manual deletion.

Back up configuration separately: variable names, reference wiring, service config paths, domain,
and runbook. Never put secret values in the backup document or repository.

## Deployments and rollback

The web overlaps deployments for 20 seconds and drains for 30. The volume-backed worker uses no
overlap and gets 60 seconds after SIGTERM. Railway's teardown behavior is documented in
[Deployment Teardown](https://docs.railway.com/deployments/deployment-teardown).

Before a production deploy:

1. Run `./scripts/test-integration.ps1` locally or require the GitHub CI checks.
2. Review Alembic changes for forward compatibility.
3. Confirm the worker has enough scratch and model-cache space.

Rollback uses the prior immutable image. Do not downgrade the database in production. If a schema
change is not backward compatible, ship an explicit forward repair migration.

## Cost controls

- Keep one web and one worker replica.
- Size the web modestly; allocate most CPU and memory to the worker.
- Keep the model volume, because repeated downloads cost time and bandwidth.
- Remove test recordings through the app so Bucket and database state stay consistent.
- Track original-audio retention growth and set a manual review cadence.
- Do not scale the worker to zero during an active recording; a manual restart is safe, but it adds
  lease-expiry delay.

## Quarterly recovery drill

1. Restore a PostgreSQL backup into a separate Railway environment with its isolated Bucket.
2. Upload a synthetic multi-chunk recording.
3. Restart the worker after one completed chunk and verify no repeated inference.
4. Inject or simulate a transient deletion failure and verify visible `deleting` recovery.
5. Confirm login is required for every private endpoint and all Bucket objects remain private.
6. Compare displayed transcript text with downloaded UTF-8 TXT bytes.
7. Review logs for private-data leakage and rotate any credential if leakage is suspected.
