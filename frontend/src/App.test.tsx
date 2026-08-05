import { render, screen } from "@testing-library/react";

import { App } from "./App.tsx";

describe("App", () => {
  it("opens on the private sign-in screen without a session", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          error: { code: "unauthenticated", message: "Authentication required.", requestId: "r1" },
        }),
        { status: 401, headers: { "Content-Type": "application/json" } },
      ),
    );

    render(<App />);

    expect(
      await screen.findByRole("heading", {
        name: "Transcribe any audio file into clear text.",
      }),
    ).toBeVisible();
    for (const format of ["M4A", "MP3", "WAV", "AAC", "FLAC", "OGG", "Opus", "MP4"]) {
      expect(screen.getByText(format, { exact: true })).toBeVisible();
    }
    expect(screen.getByText("EN")).toBeVisible();
    expect(screen.getByText("DE")).toBeVisible();
    expect(screen.getByText("TR")).toBeVisible();
  });
});
