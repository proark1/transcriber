import { expect, test } from "@playwright/test";

import { completedRecording, installMockApi } from "./support/mockApi.ts";

test("keeps completed chunks through failure and manual retry", async ({ page }) => {
  const recording = completedRecording({
    id: "recovering",
    filename: "Three hour workshop.m4a",
    status: "transcribing",
    completedAt: null,
    completedChunks: 1,
    totalChunks: 3,
    hasPlayback: true,
    hasTranscript: false,
  });
  const state = await installMockApi(page, { authenticated: true, recordings: [recording] });
  await page.goto("/");

  await expect(page.getByRole("heading", { name: "Transcribing 2 of 3" })).toBeVisible();
  await expect(page.getByRole("button", { name: "New transcription" })).toBeDisabled();

  recording.status = "failed";
  recording.safeErrorCode = "transcription_failed";
  await page.reload();
  await page.getByRole("button", { name: /Three hour workshop\.m4a.*Needs attention/ }).click();
  await expect(page.getByText(/completed parts are saved/i)).toBeVisible();
  await page.getByRole("button", { name: "Retry unfinished parts" }).click();

  expect(state.retryCalls).toBe(1);
  expect(recording.completedChunks).toBe(1);
  await expect(page.getByRole("heading", { name: "Waiting to start" })).toBeVisible();
});

test("offers to resume a locally checkpointed upload", async ({ page }) => {
  await installMockApi(page, { authenticated: true });
  await page.goto("/");
  await page.evaluate(() => {
    localStorage.setItem(
      "transcriber.pending-upload.v2.assad",
      JSON.stringify({
        clientRequestId: "request-resume",
        uploadSessionId: "upload-1",
        recordingId: "recording-uploaded",
        fileSignature: "long-note.m4a:100:42",
        filename: "long-note.m4a",
        sizeBytes: 100,
        language: "de",
      }),
    );
  });
  await page.reload();

  await expect(page.getByText("Saved upload found")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Continue long-note.m4a" })).toBeVisible();
  await expect(page.getByLabel("Spoken language")).toBeDisabled();
});
