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

    expect(await screen.findByRole("heading", { name: "Your recordings stay yours." })).toBeVisible();
    expect(screen.getByText("EN")).toBeVisible();
    expect(screen.getByText("DE")).toBeVisible();
    expect(screen.getByText("TR")).toBeVisible();
  });
});
