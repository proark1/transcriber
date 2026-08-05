import type { Page, Route } from "@playwright/test";

export type RecordingStatus =
  | "uploading"
  | "queued"
  | "validating"
  | "normalizing"
  | "chunking"
  | "transcribing"
  | "assembling"
  | "completed"
  | "failed"
  | "deleting";

export interface MockRecording {
  id: string;
  filename: string;
  contentType: string;
  language: "en" | "de" | "tr";
  status: RecordingStatus;
  createdAt: string;
  completedAt: string | null;
  durationSeconds: number | null;
  verifiedBytes: number | null;
  completedChunks: number;
  totalChunks: number;
  safeErrorCode: string | null;
  hasPlayback: boolean;
  hasTranscript: boolean;
}

export interface MockApiState {
  authenticated: boolean;
  currentUsername: string | null;
  recordings: MockRecording[];
  accounts: Record<string, { pin: string; recordings: MockRecording[] }>;
  transcript: string;
  uploadedPart: boolean;
  loginAttempts: number;
  retryCalls: number;
  deleteCalls: number;
}

export function completedRecording(overrides: Partial<MockRecording> = {}): MockRecording {
  return {
    id: "recording-ready",
    filename: "Studio Interview.m4a",
    contentType: "audio/mp4",
    language: "de",
    status: "completed",
    createdAt: "2026-08-03T12:00:00Z",
    completedAt: "2026-08-03T13:00:00Z",
    durationSeconds: 3_661,
    verifiedBytes: 48_117,
    completedChunks: 4,
    totalChunks: 4,
    safeErrorCode: null,
    hasPlayback: true,
    hasTranscript: true,
    ...overrides,
  };
}

export async function installMockApi(
  page: Page,
  options: { authenticated?: boolean; recordings?: MockRecording[]; transcript?: string } = {},
): Promise<MockApiState> {
  const initialRecordings = options.recordings ?? [];
  const accounts = { assad: { pin: "123456", recordings: initialRecordings } };
  const state: MockApiState = {
    authenticated: options.authenticated ?? false,
    currentUsername: options.authenticated ? "assad" : null,
    recordings: initialRecordings,
    accounts,
    transcript: options.transcript ?? "First clean paragraph.\n\nSecond clean paragraph.\n",
    uploadedPart: false,
    loginAttempts: 0,
    retryCalls: 0,
    deleteCalls: 0,
  };

  await page.route("**/*", async (route) => handleRoute(route, state));
  return state;
}

async function handleRoute(route: Route, state: MockApiState): Promise<void> {
  const request = route.request();
  const url = new URL(request.url());
  const path = url.pathname;
  const method = request.method();

  if (path.startsWith("/fake-bucket/")) {
    state.uploadedPart = true;
    await route.fulfill({ status: 200, body: "" });
    return;
  }
  if (path === "/fake-playback/audio.m4a") {
    await route.fulfill({ status: 200, contentType: "audio/mp4", body: "mock-audio" });
    return;
  }
  if (!path.startsWith("/api/")) {
    await route.continue();
    return;
  }

  if (path === "/api/auth/session") {
    if (!state.authenticated) {
      await apiError(route, 401, "unauthenticated", "Authentication required.");
      return;
    }
    await json(route, session(state.currentUsername ?? "assad", false));
    return;
  }
  if (path === "/api/auth/login" && method === "POST") {
    state.loginAttempts += 1;
    const submitted = request.postDataJSON() as { username?: string; pin?: string };
    const username = (submitted.username ?? "").trim().normalize("NFKC").toLowerCase();
    const pin = submitted.pin ?? "";
    if (username === "owner") {
      await apiError(route, 422, "username_unavailable", "That username is unavailable.");
      return;
    }
    if (!/^[a-z0-9._-]{3,32}$/.test(username)) {
      await apiError(
        route,
        422,
        "invalid_username",
        "Use 3–32 letters or numbers. You may also use ., _ or -.",
      );
      return;
    }
    if (!/^[0-9]{6,12}$/.test(pin)) {
      await apiError(route, 422, "invalid_pin", "Use a 6–12 digit PIN.");
      return;
    }
    const existing = state.accounts[username];
    if (existing && existing.pin !== pin) {
      await apiError(
        route,
        401,
        "incorrect_pin",
        "That PIN is incorrect for this username.",
      );
      return;
    }
    const accountCreated = existing === undefined;
    const account = existing ?? { pin, recordings: [] };
    state.accounts[username] = account;
    state.authenticated = true;
    state.currentUsername = username;
    state.recordings = account.recordings;
    await json(route, session(username, accountCreated));
    return;
  }
  if (!state.authenticated) {
    await apiError(route, 401, "unauthenticated", "Authentication required.");
    return;
  }
  if (path === "/api/auth/logout" && method === "POST") {
    state.authenticated = false;
    state.currentUsername = null;
    await route.fulfill({ status: 204, body: "" });
    return;
  }
  if (path === "/api/recordings" && method === "GET") {
    await json(route, state.recordings);
    return;
  }
  if (path === "/api/uploads" && method === "POST") {
    state.uploadedPart = false;
    await json(route, uploadState(state, false));
    return;
  }
  if (path === "/api/uploads/upload-1/parts/authorize" && method === "POST") {
    await json(route, {
      authorizedParts: [{ partNumber: 1, url: `${url.origin}/fake-bucket/one` }],
      confirmedParts: state.uploadedPart ? [{ partNumber: 1, sizeBytes: 48_117 }] : [],
      expiresAt: "2026-08-05T12:00:00Z",
    });
    return;
  }
  if (path === "/api/uploads/upload-1" && method === "GET") {
    await json(route, uploadState(state, state.uploadedPart));
    return;
  }
  if (path === "/api/uploads/upload-1/complete" && method === "POST") {
    state.recordings = [
      completedRecording({
        id: "recording-uploaded",
        filename: "field-recording.m4a",
        language: "tr",
        status: "queued",
        completedAt: null,
        durationSeconds: null,
        completedChunks: 0,
        totalChunks: 0,
        hasPlayback: false,
        hasTranscript: false,
      }),
      ...state.recordings,
    ];
    await json(route, { recordingId: "recording-uploaded", status: "queued" });
    return;
  }
  if (path === "/api/uploads/upload-1/abort" && method === "POST") {
    await route.fulfill({ status: 204, body: "" });
    return;
  }

  const recordingMatch = path.match(/^\/api\/recordings\/([^/]+)(?:\/(.+))?$/);
  if (recordingMatch) {
    await handleRecordingRoute(route, state, recordingMatch[1], recordingMatch[2]);
    return;
  }
  await apiError(route, 404, "not_found", "Route not found.");
}

async function handleRecordingRoute(
  route: Route,
  state: MockApiState,
  recordingId: string,
  action?: string,
): Promise<void> {
  const method = route.request().method();
  const origin = new URL(route.request().url()).origin;
  const recording = state.recordings.find((item) => item.id === recordingId);
  if (!recording) {
    await apiError(route, 404, "not_found", "Recording not found.");
    return;
  }
  if (!action && method === "GET") {
    await json(route, recording);
    return;
  }
  if (!action && method === "DELETE") {
    state.deleteCalls += 1;
    recording.status = "deleting";
    await json(route, { status: "deleting" }, 202);
    return;
  }
  if (action === "playback" && method === "GET") {
    await json(route, {
      url: `${origin}/fake-playback/audio.m4a`,
      expiresAt: "2026-08-05T12:00:00Z",
    });
    return;
  }
  if (action === "transcript" && method === "GET") {
    await route.fulfill({
      status: 200,
      contentType: "text/plain; charset=utf-8",
      body: state.transcript,
    });
    return;
  }
  if (action === "transcript.txt" && method === "GET") {
    await route.fulfill({
      status: 200,
      contentType: "text/plain; charset=utf-8",
      headers: { "Content-Disposition": 'attachment; filename="transcript.txt"' },
      body: state.transcript,
    });
    return;
  }
  if (action === "retry" && method === "POST") {
    state.retryCalls += 1;
    recording.status = "queued";
    await json(route, recording);
    return;
  }
  await apiError(route, 404, "not_found", "Route not found.");
}

function uploadState(state: MockApiState, confirmed: boolean) {
  return {
    recordingId: "recording-uploaded",
    uploadSessionId: "upload-1",
    partSizeBytes: 33_554_432,
    partCount: 1,
    expiresAt: "2026-08-05T12:00:00Z",
    status: "uploading",
    confirmedParts: confirmed ? [{ partNumber: 1, sizeBytes: 48_117 }] : [],
  };
}

function session(username: string, accountCreated: boolean) {
  return {
    authenticated: true,
    username,
    csrfToken: "csrf-test-token",
    expiresAt: "2026-08-11T12:00:00Z",
    accountCreated,
  };
}

async function json(route: Route, body: unknown, status = 200): Promise<void> {
  await route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

async function apiError(
  route: Route,
  status: number,
  code: string,
  message: string,
): Promise<void> {
  await json(route, { error: { code, message, requestId: "request-test" } }, status);
}

export async function signIn(page: Page): Promise<void> {
  await page.goto("/");
  await page.getByLabel("Username").fill("assad");
  await page.getByLabel("PIN").fill("123456");
  await page.getByRole("button", { name: "Continue" }).click();
  await page.getByRole("heading", { name: "Turn a recording into clean text." }).waitFor();
}
