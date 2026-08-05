import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { AuthProvider } from "../src/auth/AuthProvider.tsx";
import { TranscriptView } from "../src/recordings/TranscriptView.tsx";

function json(body: object) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

describe("TranscriptView", () => {
  it("displays exact text, copies it, and links to the server TXT", async () => {
    const transcript = "First paragraph.\n\nSecond paragraph.\n";
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
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
      if (path.endsWith("/transcript")) {
        return new Response(transcript, {
          status: 200,
          headers: { "Content-Type": "text/plain; charset=utf-8" },
        });
      }
      throw new Error(`Unexpected request: ${path}`);
    });
    const user = userEvent.setup();
    const writeText = vi.spyOn(navigator.clipboard, "writeText").mockResolvedValue(undefined);
    render(
      <AuthProvider>
        <TranscriptView recordingId="recording-1" />
      </AuthProvider>,
    );

    expect(await screen.findByText(/First paragraph/)).toHaveTextContent(
      "First paragraph. Second paragraph.",
    );
    await user.click(screen.getByRole("button", { name: "Copy text" }));
    expect(writeText).toHaveBeenCalledWith(transcript);
    expect(await screen.findByText("Transcript copied.")).toBeVisible();
    expect(screen.getByRole("link", { name: "Download TXT" })).toHaveAttribute(
      "href",
      "/api/recordings/recording-1/transcript.txt",
    );
  });
});
