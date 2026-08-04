import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { App } from "../src/App.tsx";

function jsonResponse(body: object, status = 200, extraHeaders: HeadersInit = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...extraHeaders },
  });
}

describe("LoginPage", () => {
  it("submits a numeric PIN and shows a generic invalid-credential message", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        jsonResponse(
          { error: { code: "unauthenticated", message: "Authentication required.", requestId: "r1" } },
          401,
        ),
      )
      .mockResolvedValueOnce(
        jsonResponse(
          { error: { code: "unauthenticated", message: "Authentication required.", requestId: "r2" } },
          401,
        ),
      );
    const user = userEvent.setup();
    render(<App />);

    await user.type(await screen.findByLabelText("Username"), "owner");
    await user.type(screen.getByLabelText("PIN"), "12ab3456");
    expect(screen.getByLabelText("PIN")).toHaveValue("123456");
    await user.click(screen.getByRole("button", { name: "Open workspace" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "That username or PIN wasn’t accepted.",
    );
    expect(fetchMock).toHaveBeenLastCalledWith(
      "/api/auth/login",
      expect.objectContaining({ method: "POST", credentials: "include" }),
    );
  });

  it("turns a rate limit into plain-language lockout feedback", async () => {
    vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        jsonResponse(
          { error: { code: "unauthenticated", message: "Authentication required.", requestId: "r1" } },
          401,
        ),
      )
      .mockResolvedValueOnce(
        jsonResponse(
          { error: { code: "rate_limited", message: "Too many attempts.", requestId: "r2" } },
          429,
          { "Retry-After": "900" },
        ),
      );
    const user = userEvent.setup();
    render(<App />);

    await user.type(await screen.findByLabelText("Username"), "owner");
    await user.type(screen.getByLabelText("PIN"), "123456");
    await user.click(screen.getByRole("button", { name: "Open workspace" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("about 15 minutes");
  });
});
