# Self-Registering Private Accounts Design

**Date:** 2026-08-05
**Status:** Approved for implementation planning

## Goal

Replace the single Railway-configured owner login with a combined sign-in-or-registration flow.
Any new username and valid PIN creates a private account and signs the user in immediately. An
existing username signs in only with its correct PIN. Every account sees and controls only its own
recordings, transcripts, uploads, playback links, retries, and deletions.

This design also changes the landing-page promise from iPhone-specific transcription to
transcription of any supported audio file. iPhone recordings remain supported, but they are one
source among phones, computers, messaging applications, and dedicated recorders.

This specification supersedes the single-user authentication, global one-active-recording rule,
and iPhone-led landing-page wording in
`docs/superpowers/specs/2026-08-03-railway-transcriber-design.md`. The transcription pipeline,
supported formats, languages, retention, recovery, and Railway architecture remain unchanged.

## Approved product decisions

- Authentication uses one username/PIN form with one `Continue` action.
- A previously unseen username creates an account and authenticated session atomically.
- An existing username with the correct PIN creates a session.
- An existing username with the wrong PIN shows `That PIN is incorrect for this username.`
- Usernames are case-insensitive. They are trimmed, normalized, stored, and displayed in lowercase;
  `Assad` and `assad` identify the same account.
- PINs remain numeric and contain 6–12 digits.
- The old Railway-configured `owner` account and its PIN do not remain valid.
- Each user has a private recording history and one active upload or recording at a time.
- Different users may upload or queue work independently. The single worker continues processing
  jobs sequentially.
- Version 1 has no PIN recovery, PIN change, invitations, administrator UI, account deletion, teams,
  or sharing.

## Account and ownership model

### `users`

A new `users` table contains:

- random UUID primary key;
- normalized lowercase username with a unique database constraint;
- Argon2id PIN hash;
- creation timestamp.

Plain PINs are never stored or logged. The normalized username is the display username in this
version, avoiding separate display and identity forms.

Usernames must be 3–32 characters after trimming and normalization. They may contain Unicode
letters and numbers plus `.`, `_`, and `-`; whitespace and control characters are rejected.
Normalization uses Unicode NFKC followed by case folding and a lowercase display representation.

### Sessions

`auth_sessions` gains a non-null `user_id` foreign key. Resolving a session therefore produces an
authenticated user identity, not a configured global owner. Session responses return that user's
normalized username. Existing pre-migration sessions are revoked and removed.

The application-wide session HMAC secret remains a Railway secret. A per-user credential version is
derived from the stored PIN hash so a future PIN reset can revoke that user's sessions without
changing the application secret.

### Recordings

`recordings` gains a non-null, indexed `user_id` foreign key. Upload sessions and transcription
chunks inherit ownership through their recording and do not duplicate the user ID.

All authenticated recording queries include `recordings.user_id = authenticated_user.id`. Direct
access to another user's recording, transcript, playback URL, retry action, or deletion action
returns `404`, including when the UUID exists.

The current global partial unique index for active recordings is replaced by a partial unique index
on `user_id`. Each account can have at most one recording in an active status, while multiple users
can have active or queued recordings simultaneously.

Object keys remain UUID-based. Authorization is enforced through PostgreSQL ownership checks before
the API issues any presigned URL.

## Combined registration and login transaction

The existing `/api/auth/login` boundary becomes a combined sign-in-or-registration operation so the
browser does not need to decide which mode applies.

1. Validate and normalize the username and validate the PIN shape before database work.
2. Apply the existing bounded attempt control using the normalized username and client key.
3. Lock or look up the normalized username within a transaction.
4. If the user exists, verify the submitted PIN against its Argon2id hash.
5. If verification succeeds, clear failed-attempt state and issue a session.
6. If verification fails, record the failure and return a distinct safe error code for an incorrect
   PIN.
7. If the user does not exist, hash the PIN, insert the user, and issue a session in the same
   transaction.

Concurrent first requests for the same normalized username are resolved by the unique constraint.
Only one insert succeeds. A losing transaction reloads the created user and verifies its submitted
PIN; it cannot create a duplicate or overwrite the first PIN.

The response includes whether the account was newly created so the interface may provide an
accessible confirmation without adding another step.

## Security and rate limiting

- PIN hashing uses Argon2id with the existing hardened production parameters.
- Registration and sign-in share the current username/client attempt window and lockout policy.
- Failed existing-user PIN checks increment the bounded failure counter.
- Invalid username or PIN shapes are rejected before account creation.
- State-changing requests retain same-origin and CSRF requirements.
- Cookies remain `Secure`, `HttpOnly`, `SameSite=Lax`, and limited to the application path.
- Account existence is intentionally observable because a new username registers while an existing
  username with the wrong PIN returns an incorrect-PIN message. This matches the approved product
  behavior.
- Application logs may include user UUIDs and safe result codes but never usernames, PINs, PIN
  hashes, session values, transcript text, or presigned URLs.

Open registration means anyone who can reach the public domain may create a username. Preventing
public registration, adding invitations, and account recovery are explicitly outside this version.

## API authorization changes

The authenticated request dependency exposes the `User` and `AuthSession`. The following boundaries
become user-scoped:

- recording history and recording detail;
- multipart creation, inspection, authorization, completion, and abort;
- playback authorization;
- transcript display and TXT download;
- retry and deletion;
- detection of an already-active recording;
- browser upload-resume reconciliation.

Worker claims remain global and do not need a logged-in user. They operate on durable recording IDs
and preserve the recording's ownership without exposing it. Queue ordering remains deterministic by
the existing repository policy.

## Landing page and account interface

The page keeps its established visual identity and responsive layout. The content changes from an
iPhone-led private-owner screen to a general audio-transcription entry point.

### Introductory content

- Headline: `Transcribe any audio file into clear text.`
- Supporting copy explains that recordings may come from phones, computers, messaging apps, or
  dedicated recorders.
- Format guidance lists M4A, MP3, WAV, AAC, FLAC, OGG, Opus, and MP4.
- iPhone compatibility may appear as one reassuring example, never as the product's definition.

### Account card

- Title: `Sign in or create an account.`
- Helper text: `Enter a new username to create a private account automatically.`
- Fields: `Username` and `PIN`.
- Primary action: `Continue`.
- Username input disables automatic capitalization and spelling correction.
- The browser submits the trimmed username; the API is authoritative for normalization.
- A newly created account opens the private workspace immediately without a confirmation page.

### User-facing failures

- Existing username with wrong PIN: `That PIN is incorrect for this username.`
- Invalid PIN: `Use a 6–12 digit PIN.`
- Invalid username: `Use 3–32 letters or numbers. You may also use ., _ or -.`
- Locked attempts: retain the current actionable retry timing.
- Network or service failure: explain that the account could not be opened and that retrying is
  safe.

Errors use an `aria-live` status region, focus remains predictable, and the flow is fully operable by
keyboard and at iPhone viewport sizes.

## Migration and Railway rollout

The migration adds `users`, links sessions and recordings to users, replaces the active-recording
index, and removes obsolete single-owner session state.

Production history was empty when this design was approved. Immediately before applying the
migration, deployment must verify again that no recordings or unfinished uploads exist. If any are
present, deployment stops for an explicit ownership decision rather than deleting, hiding, or
misassigning data.

After the empty-state guard passes:

1. Remove old auth sessions and login-attempt rows.
2. Add the new ownership schema and constraints.
3. Deploy compatible web and worker images.
4. Remove `APP_USERNAME` and `APP_PIN_HASH` from both Railway services after the new version is
   healthy; retain the application session secret.
5. Confirm the previous `owner` credentials no longer authenticate.
6. Register a temporary normalized smoke-test account through the public domain.
7. Upload and transcribe a short spoken M4A, verify transcript download and playback, delete the
   recording and Bucket objects, then remove the temporary user directly through an operator-safe
   cleanup command.
8. Leave production with zero users, zero recordings, and zero Bucket objects so the first real user
   registers normally.

The migration is forward-only. Rollback uses a forward repair migration rather than restoring the
single-owner credential model.

## Verification strategy

### Authentication tests

- A new normalized username creates exactly one user and logs in immediately.
- `Assad`, `assad`, and compatible Unicode case variants resolve to one account.
- A correct existing-user PIN logs in.
- A wrong existing-user PIN returns the incorrect-PIN code and message.
- Invalid username and PIN shapes create no database rows.
- Concurrent registration for one normalized username creates one account; the losing request must
  authenticate against the winning PIN.
- Attempt windows, lockouts, session rotation, expiry, logout, cookies, CSRF, and session revocation
  remain correct.
- Old Railway owner credentials do not authenticate after migration.

### Ownership tests

- Two users receive separate histories.
- Recording detail, playback, transcript, download, retry, deletion, and upload-session routes return
  `404` across user boundaries.
- Presigned URLs are issued only after ownership checks.
- Each user may have one active recording.
- Two different users may independently create active or queued recordings.
- The single worker processes both users' jobs sequentially without changing ownership.
- Deleting one user's recording cannot alter another user's database rows or Bucket objects.

### Interface and end-to-end tests

- Landing copy promises any supported audio file and lists all formats.
- The combined form registers a new account and signs in an existing account.
- The incorrect-PIN, invalid-input, lockout, and offline messages are exact and accessible.
- Usernames display in lowercase after authentication.
- Accessibility scans have no serious or critical violations.
- The login and workspace layouts do not overflow an iPhone viewport.
- A live Railway smoke test covers registration, spoken M4A upload, transcription, transcript
  equality, playback, user-scoped deletion, and complete cleanup.

## Acceptance criteria

1. The landing page clearly accepts any listed audio file and does not present the app as an
   iPhone-only voice-note tool.
2. A valid new username and PIN creates a private account and authenticated session in one action.
3. Usernames are case-insensitive and displayed in lowercase.
4. An existing username accepts only its correct PIN and shows the approved incorrect-PIN message
   otherwise.
5. The old configured owner account no longer works and its Railway username/PIN variables are
   removed.
6. Every account sees and controls only its own recordings and storage authorizations.
7. One active recording is allowed per account, while different users may queue work independently.
8. The existing long-file, format, language, restart, transcript, playback, retention, and deletion
   guarantees remain intact.
9. Automated tests and a clean live Railway smoke test verify registration, isolation, transcription,
   and cleanup.
