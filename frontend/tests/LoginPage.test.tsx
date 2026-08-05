import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { App } from "../src/App.tsx";

function jsonResponse(body: object, status = 200, extraHeaders: HeadersInit = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...extraHeaders },
  });
}

function unauthenticated() {
  return jsonResponse(
    { error: { code: "unauthenticated", message: "Authentication required.", requestId: "r1" } },
    401,
  );
}

describe("LoginPage", () => {
  it("registers a trimmed username and opens the normalized private workspace", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(unauthenticated())
      .mockResolvedValueOnce(
        jsonResponse({
          authenticated: true,
          username: "assad",
          csrfToken: "csrf",
          expiresAt: "2026-08-11T12:00:00Z",
          accountCreated: true,
        }),
      )
      .mockResolvedValueOnce(jsonResponse([]));
    const user = userEvent.setup();
    render(<App />);

    const username = await screen.findByLabelText("Username");
    expect(username).toHaveAttribute("autocapitalize", "none");
    expect(username).toHaveAttribute("spellcheck", "false");
    await user.type(username, "  AsSaD  ");
    await user.type(screen.getByLabelText("PIN"), "12ab3456");
    expect(screen.getByLabelText("PIN")).toHaveValue("123456");
    await user.click(screen.getByRole("button", { name: "Continue" }));

    expect(await screen.findByText("assad", { exact: true })).toBeVisible();
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      "/api/auth/login",
      expect.objectContaining({
        method: "POST",
        credentials: "include",
        body: JSON.stringify({ username: "AsSaD", pin: "123456" }),
      }),
    );
  });

  it.each([
    ["incorrect_pin", "That PIN is incorrect for this username."],
    ["username_unavailable", "That username is unavailable."],
    ["invalid_username", "Use 3–32 letters or numbers. You may also use ., _ or -."],
  ])("shows the exact %s account error", async (code, message) => {
    vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(unauthenticated())
      .mockResolvedValueOnce(
        jsonResponse({ error: { code, message, requestId: "r2" } }, code === "incorrect_pin" ? 401 : 422),
      );
    const user = userEvent.setup();
    render(<App />);

    await user.type(await screen.findByLabelText("Username"), "existing-user");
    await user.type(screen.getByLabelText("PIN"), "123456");
    await user.click(screen.getByRole("button", { name: "Continue" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(message);
  });

  it("validates PIN length with the approved message before sending", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(unauthenticated());
    const user = userEvent.setup();
    render(<App />);

    await user.type(await screen.findByLabelText("Username"), "new-user");
    await user.type(screen.getByLabelText("PIN"), "12345");
    await user.click(screen.getByRole("button", { name: "Continue" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Use a 6–12 digit PIN.");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("turns a rate limit into plain-language lockout feedback", async () => {
    vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(unauthenticated())
      .mockResolvedValueOnce(
        jsonResponse(
          { error: { code: "rate_limited", message: "Too many attempts.", requestId: "r2" } },
          429,
          { "Retry-After": "900" },
        ),
      );
    const user = userEvent.setup();
    render(<App />);

    await user.type(await screen.findByLabelText("Username"), "assad");
    await user.type(screen.getByLabelText("PIN"), "123456");
    await user.click(screen.getByRole("button", { name: "Continue" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("about 15 minutes");
  });

  it("explains that retrying is safe after a service failure", async () => {
    vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(unauthenticated())
      .mockRejectedValueOnce(new TypeError("offline"));
    const user = userEvent.setup();
    render(<App />);

    await user.type(await screen.findByLabelText("Username"), "assad");
    await user.type(screen.getByLabelText("PIN"), "123456");
    await user.click(screen.getByRole("button", { name: "Continue" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The account could not be opened. Retrying is safe.",
    );
  });
});
