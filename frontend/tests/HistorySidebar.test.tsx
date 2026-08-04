import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { RecordingResponse } from "../src/api/contracts.ts";
import { HistorySidebar } from "../src/recordings/HistorySidebar.tsx";

const recordings: RecordingResponse[] = [
  {
    id: "ready-1",
    filename: "Istanbul interview.m4a",
    contentType: "audio/mp4",
    language: "tr",
    status: "completed",
    createdAt: "2026-08-03T12:00:00Z",
    completedAt: "2026-08-03T13:00:00Z",
    durationSeconds: 3_661,
    verifiedBytes: 100,
    completedChunks: 4,
    totalChunks: 4,
    safeErrorCode: null,
    hasPlayback: true,
    hasTranscript: true,
  },
];

describe("HistorySidebar", () => {
  it("shows permanent history with language and status and opens an item", async () => {
    const onSelect = vi.fn();
    const user = userEvent.setup();
    render(
      <HistorySidebar
        recordings={recordings}
        selectedId={null}
        activeExists={false}
        onSelect={onSelect}
        onNew={vi.fn()}
      />,
    );

    expect(screen.getByText("Istanbul interview.m4a")).toBeVisible();
    expect(screen.getByText(/Turkish/)).toBeVisible();
    expect(screen.getByText("Ready")).toBeVisible();
    await user.click(screen.getByRole("button", { name: /Istanbul interview.m4a/i }));
    expect(onSelect).toHaveBeenCalledWith("ready-1");
  });

  it("prevents a second transcription while one is active", () => {
    render(
      <HistorySidebar
        recordings={recordings}
        selectedId="ready-1"
        activeExists
        onSelect={vi.fn()}
        onNew={vi.fn()}
      />,
    );

    expect(screen.getByRole("button", { name: "New transcription" })).toBeDisabled();
  });
});
