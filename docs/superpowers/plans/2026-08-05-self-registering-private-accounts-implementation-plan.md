# Self-Registering Private Accounts Implementation Plan

**Date:** 2026-08-05
**Design:** `docs/superpowers/specs/2026-08-05-self-registering-private-accounts-design.md`

## Objective

Replace the live single-owner login with a combined sign-in-or-registration flow backed by
PostgreSQL users, attach every session and recording to one user, enforce ownership at every API
boundary, and update the landing page to promise transcription of any supported audio file.

The work is complete only when local tests prove atomic account creation and cross-user isolation,
the complete regression suite remains green, and a guarded Railway rollout proves registration,
transcription, playback, transcript download, deletion, removal of the former owner credentials,
and complete cleanup of the temporary smoke account and its data.

## Execution rules

- Implement tasks in order. Authentication cannot safely change before the ownership migration,
  and Railway variables cannot be removed before compatible web and worker images are healthy.
- Add or update tests with each behavior instead of postponing isolation coverage until deployment.
- Keep commits scoped to the task boundaries below.
- Do not modify production data until Task 8. Stop the rollout if the production empty-state guard
  finds any recording, upload session, or Bucket object.
- Never commit or log usernames, PINs, PIN hashes, session values, transcript text, presigned URLs,
  Railway credentials, or real user audio.
- Treat the normalized lowercase username as the only identity and display representation.
- Add the authenticated user ID to the first database query for every user-facing recording or
  upload operation. A UUID owned by another user must look exactly like a missing UUID and return
  `404`.
- Preserve global worker claiming and sequential inference. Ownership limits what browser users can
  see and mutate; it must not fragment or duplicate the durable worker queue.
- Preserve the existing audio formats, English/German/Turkish selector, long-file chunking,
  restart recovery, playback, transcript, retry, retention, and deletion behavior.

## Task 1: Add users and per-user ownership to the data model

### Files

- Create `backend/alembic/versions/0004_private_accounts.py`
- Create `backend/tests/test_private_accounts_migration.py`
- Modify `backend/src/transcriber/models.py`
- Modify `backend/src/transcriber/config.py`
- Modify `backend/tests/conftest.py`
- Modify `backend/tests/test_config.py`
- Modify `backend/tests/test_models.py`
- Modify `.env.example`

### Work

1. Add a `User` model with a random UUID primary key, unique normalized username, Argon2id PIN hash,
   and creation timestamp. Use a bounded string length large enough for the approved 32-character
   normalized username and a complete encoded Argon2id hash.
2. Add `user_id` to `AuthSession` and `Recording`. Use `ON DELETE CASCADE` for sessions and a
   restrictive recording foreign key so an operator cannot delete a user while storage-backed
   recordings still exist.
3. Add ORM relationships for `User.sessions`, `User.recordings`, `AuthSession.user`, and
   `Recording.user` without changing upload-session or chunk ownership storage.
4. Replace the global `uq_recordings_one_active` partial index with a unique partial index on
   `recordings.user_id` for the existing active statuses.
5. Make migration `0004` fail before schema mutation when an upgrade from `0003` finds any
   recording or upload-session row. The guard protects the approved empty-production migration and
   makes an ownership decision mandatory if the assumption has changed.
6. In the guarded migration, delete old `auth_sessions` and `login_attempts`, create `users`, add the
   two non-null ownership foreign keys and indexes, and replace the active-recording index. Make the
   migration explicitly forward-only rather than recreating the single-owner model on downgrade.
7. Remove `APP_USERNAME` and `APP_PIN_HASH` from `AppSettings`, including their startup validation.
   Keep the HMAC session secret and all existing production safety checks.
8. Update test cleanup order so sessions and recordings are removed before users. Add a low-cost
   test user factory that still creates valid Argon2id hashes.
9. Test the exact tables, foreign keys, uniqueness rules, per-user active index, empty upgrade, data
   guard, and removal of the two obsolete environment variables.

### Verification

```powershell
uv run --project backend pytest backend/tests/test_private_accounts_migration.py backend/tests/test_models.py backend/tests/test_config.py
uv run --project backend alembic -c backend/alembic.ini upgrade head
uv run --project backend mypy backend/src
```

### Commit

`feat: add private account ownership model`

## Task 2: Implement atomic sign-in or account creation

### Files

- Modify `backend/src/transcriber/auth.py`
- Modify `backend/src/transcriber/api/app.py`
- Modify `backend/src/transcriber/api/dependencies.py`
- Modify `backend/src/transcriber/api/routes_auth.py`
- Modify `backend/src/transcriber/api/security.py`
- Modify `backend/tests/test_auth.py`
- Modify `backend/tests/test_auth_routes.py`
- Modify `backend/tests/test_security_headers.py`

### Work

1. Add one username normalization function: trim, apply Unicode NFKC, case-fold, and validate the
   normalized result as 3–32 Unicode letters/numbers or `.`, `_`, and `-`. Return the normalized
   lowercase form for both storage and display.
2. Define `owner` as a case-insensitive, code-level reserved normalized username. Reject it before
   database work, retain neither the former PIN nor its hash, and never create a reserved user row.
3. Validate the PIN as exactly 6–12 ASCII digits before opening the registration transaction. Use
   separate safe exceptions for an invalid username, invalid PIN shape, incorrect existing-user
   PIN, reserved username, and active lockout.
4. Rename the service operation to express its combined behavior and return the authenticated
   `User`, session secrets, expiry, and an `account_created` flag.
5. Apply login-attempt locking to the HMAC of normalized username plus client key. Failed PIN checks
   for existing users increment the existing five-attempt/15-minute window; successful login or
   registration clears that attempt row.
6. Inside one transaction, lock the normalized username. If it exists, verify its Argon2id PIN
   hash. If it does not, hash the PIN and insert the user before issuing the session.
7. Resolve simultaneous first requests with the username unique constraint inside a database
   savepoint. The losing request must reload the winning user and verify its submitted PIN; it must
   never overwrite the hash or create a duplicate account.
8. Store the session's `user_id`. Derive its credential-version HMAC from that user's stored PIN
   hash, and load the related user when resolving a session so future hash replacement revokes only
   that user's sessions.
9. Extend `RequestAuth` to carry both `User` and `AuthSession`. Return the normalized username from
   the authenticated user for both `/api/auth/login` and `/api/auth/session`.
10. Add a controlled API-problem path that permits only explicit safe code/message pairs. Use it to
   return:
   - `invalid_username`, status `422`, `Use 3–32 letters or numbers. You may also use ., _ or -.`
   - `username_unavailable`, status `422`, `That username is unavailable.`
   - `invalid_pin`, status `422`, `Use a 6–12 digit PIN.`
   - `incorrect_pin`, status `401`, `That PIN is incorrect for this username.`
   - the existing `rate_limited` response and `Retry-After` header for lockout.
11. Add `accountCreated` to the session response. It is `true` only for the registration response
    and `false` for existing login and later session inspection.
12. Test normalization, new-user creation, correct and incorrect existing PINs, no rows for invalid
    input, stored HMAC-only session secrets, per-user credential versions, expiry, CSRF rotation,
    logout, lockout, and a real two-connection concurrent registration race.
13. Prove `owner`, `Owner`, and `OWNER` always return the unavailable response, never create a user,
    and cannot authenticate with either the former PIN or a new PIN.

### Verification

```powershell
uv run --project backend pytest backend/tests/test_auth.py backend/tests/test_auth_routes.py backend/tests/test_security_headers.py
uv run --project backend ruff check backend/src/transcriber/auth.py backend/src/transcriber/api
uv run --project backend mypy backend/src
```

### Commit

`feat: add self-registering account authentication`

## Task 3: Scope uploads and recording creation to the authenticated user

### Files

- Modify `backend/src/transcriber/repositories.py`
- Modify `backend/src/transcriber/api/routes_uploads.py`
- Modify `backend/tests/test_recording_repository.py`
- Modify `backend/tests/test_upload_routes.py`

### Work

1. Require `user_id` when `RecordingRepository.create_uploading_recording` creates a browser
   recording. Populate ownership before the active-index check and translate only the new per-user
   active constraint into `ActiveRecordingExists`.
2. Test that one user cannot create a second active recording while two different users can each
   create one. Keep state-transition methods global by recording ID for trusted worker operations.
3. Scope upload idempotency lookup by joining the upload's recording to the authenticated user.
   Never return another user's upload state for a matching client request UUID.
4. Scope `_load_upload` by both upload-session ID and `Recording.user_id` before listing parts,
   issuing part URLs, completing, or aborting an upload. Return `404` for every ownership mismatch.
5. Pass the authenticated user ID into recording creation. Preserve UUID-based object keys and
   issue presigned part URLs only after the ownership query succeeds.
6. Keep `client_request_id` globally unique because it is a random UUID, but catch its unique
   constraint as a safe conflict so a theoretical cross-account collision cannot become a `500` or
   expose another account.
7. Add two-account route tests covering create, idempotent resume, upload inspection, part
   authorization, completion, abort, per-user active limits, and cross-user `404` responses.
8. Assert that a cross-user request never calls the fake storage authorization or mutation method.

### Verification

```powershell
uv run --project backend pytest backend/tests/test_recording_repository.py backend/tests/test_upload_routes.py
uv run --project backend ruff check backend/src/transcriber/repositories.py backend/src/transcriber/api/routes_uploads.py
```

### Commit

`feat: isolate multipart uploads by account`

## Task 4: Scope history, playback, transcripts, retry, and deletion

### Files

- Modify `backend/src/transcriber/api/routes_recordings.py`
- Modify `backend/src/transcriber/deletion.py`
- Modify `backend/src/transcriber/worker/repository.py`
- Modify `backend/tests/test_recording_routes.py`
- Modify `backend/tests/test_playback_routes.py`
- Modify `backend/tests/test_deletion.py`
- Modify `backend/tests/test_worker_repository.py`

### Work

1. Filter history by `Recording.user_id` and filter every detail lookup by both recording ID and the
   authenticated user ID. Reuse that scoped lookup for transcript and playback authorization.
2. Return `404` for cross-user detail, playback, displayed transcript, TXT download, retry, and
   delete requests, including when the recording UUID exists.
3. Require a user ID for manual retry. Check active recordings only for that user so another
   account's queued or transcribing file does not block the retry.
4. Require and verify the user ID when beginning user-requested deletion. Check deletion conflicts
   against active recordings belonging to that same user.
5. Keep background deletion reconciliation global after the authorized delete has marked the
   recording `deleting`; this allows safe retry after web or worker restarts.
6. Keep worker claiming, preparation, transcription, assembly, and cleanup global and sequential.
   Add a worker test with queued recordings for two different users to prove deterministic
   sequential claims preserve each recording's owner.
7. Add two-user isolation tests for histories, direct UUID access, storage authorization, retry,
   deletion, per-user active conflicts, and unchanged object ownership.
8. Verify deleting one user's completed recording while another user has active work is allowed,
   while the deleting user's own active work still causes the approved conflict.

### Verification

```powershell
uv run --project backend pytest backend/tests/test_recording_routes.py backend/tests/test_playback_routes.py backend/tests/test_deletion.py backend/tests/test_worker_repository.py
uv run --project backend ruff check backend/src/transcriber/api/routes_recordings.py backend/src/transcriber/deletion.py backend/src/transcriber/worker/repository.py
```

### Commit

`feat: enforce private recording access`

## Task 5: Update the landing page and combined account interface

### Files

- Modify `frontend/src/api/contracts.ts`
- Modify `frontend/src/auth/AuthProvider.tsx`
- Modify `frontend/src/auth/LoginPage.tsx`
- Modify `frontend/src/app.css`
- Modify `frontend/src/App.test.tsx`
- Modify `frontend/tests/LoginPage.test.tsx`
- Modify `frontend/tests/AppShell.test.tsx`

### Work

1. Add `accountCreated` to the typed session contract while preserving the existing CSRF/session
   handling and immediate transition into the private workspace.
2. Change the headline to `Transcribe any audio file into clear text.` Explain that files can come
   from phones, computers, messaging apps, or dedicated recorders.
3. Display M4A, MP3, WAV, AAC, FLAC, OGG, Opus, and MP4 as supported formats. Keep iPhone as at most
   one compatibility example rather than the product definition.
4. Change the account card title to `Sign in or create an account.`, its helper text to `Enter a new
   username to create a private account automatically.`, and the primary action to `Continue`.
5. Disable automatic capitalization and spelling correction on the username field. Submit its
   trimmed value and display the normalized lowercase username returned by the API.
6. Keep PIN input numeric, but use controlled form validation so a short PIN produces exactly `Use
   a 6–12 digit PIN.` instead of a browser-dependent native validation bubble.
7. Map `invalid_username`, `username_unavailable`, `invalid_pin`, `incorrect_pin`, and
   `rate_limited` to the approved exact messages. For network/service failures, explain that opening
   the account failed and retrying is safe.
8. Preserve focus, keyboard operation, visible focus indicators, `aria-live` error behavior,
   reduced motion, touch targets, and the established responsive visual identity.
9. Add component tests for general-audio copy and every format, automatic new-account login,
   existing login, lowercased display, exact errors, network failure, lockout timing, input
   attributes, keyboard submission, and an iPhone-width no-overflow layout.

### Verification

```powershell
pnpm --dir frontend test -- --run App LoginPage AppShell
pnpm --dir frontend build
```

### Commit

`feat: add combined account landing page`

## Task 6: Update shared fixtures and end-to-end isolation coverage

### Files

- Modify `backend/tests/conftest.py`
- Modify backend test helpers that construct `Recording` rows
- Modify `tests/e2e/support/mockApi.ts`
- Modify `tests/e2e/auth.spec.ts`
- Modify `tests/e2e/transcription.spec.ts`
- Modify `tests/e2e/deletion.spec.ts`
- Modify `tests/e2e/recovery.spec.ts`
- Modify `tests/e2e/accessibility.spec.ts`

### Work

1. Update every backend recording factory to attach a user while keeping worker-oriented tests
   independent of browser authentication.
2. Give the browser mock API a normalized account map with PINs, per-account recordings, combined
   registration/login behavior, exact safe errors, and session-specific histories.
3. Replace fixed `owner` sign-in helpers with helpers that register or sign in a named test account
   and expect the `Continue` action.
4. Add browser coverage proving a new mixed-case username is created/displayed in lowercase, the
   same username signs in case-insensitively, and a wrong existing PIN shows the approved message.
5. Prove all casings of `owner` show `That username is unavailable.`, remain signed out, and create
   no account in the mock state.
6. Exercise two separate browser contexts and prove their histories, direct recording URLs,
   playback, transcript downloads, retries, deletions, and resumable upload state remain isolated.
7. Prove two users can queue independently while the mocked single worker advances one durable job
   at a time.
8. Keep existing restart, chunk recovery, transcript equality, format/language, deletion, keyboard,
   mobile viewport, and accessibility assertions green under account ownership.

### Verification

```powershell
uv run --project backend pytest
pnpm --dir frontend test -- --run
pnpm --dir frontend build
pnpm exec playwright test
```

### Commit

`test: verify private account isolation`

## Task 7: Update operations, smoke testing, and deployment documentation

### Files

- Create `scripts/cleanup_smoke_user.py`
- Create `backend/tests/test_cleanup_smoke_user.py`
- Modify `scripts/smoke_railway.ps1`
- Modify `docs/runbooks/railway-deployment.md`
- Modify `docs/runbooks/operations.md`
- Modify `README.md`
- Modify `.env.example`

### Work

1. Remove instructions for generating or configuring a fixed Railway owner. Document open
   self-registration, the reserved `owner` username, lowercase usernames, the lack of PIN recovery,
   private per-user histories, and the continued requirement for a strong `APP_SESSION_SECRET`.
2. Remove `APP_USERNAME` and `APP_PIN_HASH` from all example variable tables and commands for both
   web and worker services.
3. Update the smoke script to accept a unique temporary username/PIN, require the registration
   response to report `accountCreated: true`, verify the returned lowercase username, and retain its
   no-secret/no-transcript output behavior.
4. Extend the full smoke path to delete its recording after transcript and playback checks. Poll
   safely through asynchronous deletion until the recording returns `404`, and abort unfinished
   multipart state on failure.
5. Add an operator cleanup script that accepts only the normalized `railway-smoke-...` username
   pattern, refuses deletion when any recording remains, removes that user's sessions with the user,
   and prints only a safe result plus user UUID.
6. Test cleanup refusal and success against PostgreSQL. Never let the cleanup script delete an
   arbitrary username or bypass remaining-recording checks.
7. Document the exact pre-migration empty checks, old-session revocation, variable-removal order,
   old-owner rejection check, temporary smoke-account lifecycle, and final zero-row/zero-object
   checks.

### Verification

```powershell
uv run --project backend pytest backend/tests/test_cleanup_smoke_user.py
uv run --project backend ruff check scripts/cleanup_smoke_user.py backend/tests/test_cleanup_smoke_user.py
Get-Help ./scripts/smoke_railway.ps1
rg -n -g '!docs/superpowers/**' "APP_USERNAME|APP_PIN_HASH|Owner access|stored in Railway" .env.example README.md docs scripts frontend backend/src
```

The final `rg` command must return no live configuration or interface references. Historical design
and implementation documents may retain the superseded terms for traceability.

### Commit

`docs: prepare private account Railway rollout`

## Task 8: Run the complete local acceptance pass and deploy to Railway

### Local verification

1. Apply all migrations to a fresh PostgreSQL database.
2. Test an upgrade from `0003` with empty tables and separately prove the guard rejects a seeded
   recording without changing its ownership.
3. Run every backend, frontend, and browser test plus static analysis and the production build.
4. Inspect the working tree for secrets, audio, transcripts, model files, signed URLs, and
   unintended fixture artifacts.

```powershell
uv run --project backend alembic -c backend/alembic.ini upgrade head
uv run --project backend ruff check backend scripts
uv run --project backend mypy backend/src
uv run --project backend pytest
pnpm --dir frontend test -- --run
pnpm --dir frontend build
pnpm exec playwright test
git diff --check
git status --short
```

### Railway rollout

1. Before deployment, query the production `0003` schema for sessions, login attempts, recordings,
   uploads, parts, chunks, and the current Alembic revision, and list Bucket objects without
   exposing their keys. Proceed only when recordings/uploads/Bucket objects are empty as approved;
   query the new `users` table only after migration `0004` creates it.
2. Confirm both Railway services still reference the same GitHub repository and production
   environment, and record the currently healthy deployment IDs for recovery.
3. Push the reviewed implementation commits and deploy the compatible web and worker images. Let
   the web pre-deploy step apply migration `0004`.
4. Confirm `/healthz`, `/readyz`, the web deployment, worker startup, PostgreSQL, Bucket connection,
   and model-cache volume are healthy before changing variables.
5. Remove `APP_USERNAME` and `APP_PIN_HASH` from both Railway services, keep
   `APP_SESSION_SECRET`, and confirm both services remain healthy after the resulting redeploys.
6. Submit `owner` with a fresh disposable valid PIN and verify the exact unavailable-username
   response, then repeat with mixed casing. Confirm no `owner` user row exists and the old
   pre-migration session is rejected; the former PIN never needs to be submitted or retained.
7. Through the public domain, register a unique lowercase `railway-smoke-...` account with a fresh
   temporary PIN. Sign out and sign back in with a mixed-case form of the same username, then verify
   a wrong PIN returns the exact incorrect-PIN message.
8. Upload a short spoken M4A, select one approved language, wait for real `large-v3` transcription,
   and verify clean displayed text, byte-identical TXT download, playback authorization, and one
   private history item without printing any private content.
9. Delete the recording through the API and wait for database and Bucket cleanup. Log out, run the
   guarded smoke-user cleanup command, and confirm the temporary user and sessions are gone.
10. Finish with zero users, zero sessions, zero recordings, zero uploads/chunks, and zero Bucket
    objects. Confirm web, worker, and PostgreSQL remain healthy and inspect logs for forbidden data.

### Final acceptance criteria

1. The landing page says any supported audio file and lists all approved formats.
2. A new username/PIN registers and signs in in one action; an existing username requires its PIN.
3. Usernames are case-insensitive and displayed in lowercase.
4. Every recording and upload boundary is private and cross-user UUID access returns `404`.
5. Each account has one active recording while different users may queue independently.
6. The worker remains sequential, durable, and restart-safe across all accounts.
7. The former configured owner and old sessions are absent, every casing of `owner` is permanently
   unavailable, and the obsolete Railway variables are removed.
8. All local checks and the clean live Railway smoke transcription pass.

No implementation is declared complete until all eight criteria pass and the live cleanup leaves
no temporary account, recording, multipart upload, transcript, or Bucket object behind.
