import { render, screen } from "@testing-library/react";

import type { RecordingResponse } from "../src/api/contracts.ts";
import { ProcessingStatus } from "../src/recordings/ProcessingStatus.tsx";

const recording: RecordingResponse = {
  id: "recording-1",
  filename: "interview.m4a",
  contentType: "audio/mp4",
  language: "de",
  status: "transcribing",
  createdAt: "2026-08-04T12:00:00Z",
  completedAt: null,
  durationSeconds: 7_200,
  verifiedBytes: 10,
  completedChunks: 1,
  totalChunks: 5,
  safeErrorCode: null,
  hasPlayback: false,
  hasTranscript: false,
};

describe("ProcessingStatus", () => {
  it("shows restart-safe chunk progress in plain language", () => {
    render(<ProcessingStatus recording={recording} />);

    expect(screen.getByRole("heading", { name: "Transcribing 2 of 5" })).toBeVisible();
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "1");
    expect(screen.getByText(/You can close this page/i)).toBeVisible();
  });
});
