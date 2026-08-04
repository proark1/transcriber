import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { App } from "../src/App.tsx";

describe("AppShell", () => {
  it("shows the signed-in owner and signs out with the CSRF token", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            authenticated: true,
            username: "owner",
            csrfToken: "csrf-token",
            expiresAt: "2026-08-11T12:00:00Z",
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      )
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    const user = userEvent.setup();
    render(<App />);

    expect(await screen.findByText("owner")).toBeVisible();
    expect(
      screen.getByRole("heading", { name: "Choose a recording or start a new transcription." }),
    ).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Sign out" }));

    expect(await screen.findByRole("heading", { name: "Your recordings stay yours." })).toBeVisible();
    const logoutInit = fetchMock.mock.calls[1]?.[1];
    expect(new Headers(logoutInit?.headers).get("X-CSRF-Token")).toBe("csrf-token");
  });
});
