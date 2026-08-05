import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { AuthProvider } from "../src/auth/AuthProvider.tsx";
import type { RecordingResponse } from "../src/api/contracts.ts";
import { RecordingPage } from "../src/recordings/RecordingPage.tsx";

const failedRecording: RecordingResponse = {
  id: "failed-1",
  filename: "Long meeting.wav",
  contentType: "audio/wav",
  language: "de",
  status: "failed",
  createdAt: "2026-08-03T12:00:00Z",
  completedAt: null,
  durationSeconds: 7_205,
  verifiedBytes: 100,
  completedChunks: 3,
  totalChunks: 6,
  safeErrorCode: "transcription_failed",
  hasPlayback: true,
  hasTranscript: false,
};

function json(body: object) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

describe("RecordingPage", () => {
  it("shows useful metadata and retries only unfinished parts", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const path = String(input);
      if (path === "/api/auth/session") {
        return json({
          authenticated: true,
          username: "assad",
          csrfToken: "csrf",
          expiresAt: "2026-08-05T12:00:00Z",
          accountCreated: false,
        });
      }
      if (path.endsWith("/retry")) return json({ ...failedRecording, status: "queued" });
      throw new Error(`Unexpected request: ${path}`);
    });
    const onRefresh = vi.fn();
    const user = userEvent.setup();
    render(
      <AuthProvider>
        <RecordingPage
          recording={failedRecording}
          anotherRecordingActive={false}
          onRefresh={onRefresh}
          onRemoved={vi.fn()}
        />
      </AuthProvider>,
    );

    expect(screen.getByRole("heading", { name: "Long meeting.wav" })).toBeVisible();
    expect(screen.getByText("German")).toBeVisible();
    expect(screen.getByText("2:00:05")).toBeVisible();
    expect(screen.getByText(/completed parts are saved/i)).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Retry unfinished parts" }));
    expect(onRefresh).toHaveBeenCalledOnce();
    const retryCall = fetchMock.mock.calls.find(([path]) => String(path).endsWith("/retry"));
    expect(retryCall?.[1]).toEqual(expect.objectContaining({ method: "POST" }));
  });
});
