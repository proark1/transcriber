import { render, screen } from "@testing-library/react";

import { App } from "./App.tsx";

describe("App", () => {
  it("introduces the private transcription workspace", () => {
    render(<App />);

    expect(screen.getByRole("heading", { name: "Long recordings in. Clean text out." })).toBeVisible();
    expect(screen.getByText("Private audio workspace")).toBeVisible();
    expect(screen.getByText("English · German · Turkish")).toBeVisible();
  });
});
