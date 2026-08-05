import { readFile } from "node:fs/promises";

import { expect, test } from "@playwright/test";

import { installMockApi, signIn } from "./support/mockApi.ts";

test("uploads a supported audio file and exposes a byte-identical finished transcript", async ({
  context,
  page,
}) => {
  const transcript = "First clean paragraph.\n\nSecond clean paragraph.\n";
  const state = await installMockApi(page, { transcript });
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await signIn(page);

  await page.getByLabel("Spoken language").selectOption("tr");
  await page.locator('input[type="file"]').setInputFiles({
    name: "field-recording.m4a",
    mimeType: "audio/mp4",
    buffer: Buffer.from("synthetic audio"),
  });
  await expect(page.getByRole("heading", { name: "field-recording.m4a" })).toBeVisible();
  await page.getByRole("button", { name: "Start transcription" }).click();

  await expect(page.getByRole("heading", { name: "Waiting to start" })).toBeVisible();
  expect(state.uploadedPart).toBe(true);
  expect(state.recordings[0]).toMatchObject({ filename: "field-recording.m4a", language: "tr" });

  Object.assign(state.recordings[0], {
    status: "completed",
    completedAt: "2026-08-04T12:00:00Z",
    durationSeconds: 3,
    completedChunks: 1,
    totalChunks: 1,
    hasPlayback: true,
    hasTranscript: true,
  });
  await page.reload();
  await page.getByRole("button", { name: /field-recording\.m4a.*Ready/ }).click();

  await expect(page.getByText("First clean paragraph.")).toBeVisible();
  await page.getByRole("button", { name: "Copy text" }).click();
  expect(
    (await page.evaluate(() => navigator.clipboard.readText())).replaceAll("\r\n", "\n"),
  ).toBe(transcript);

  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("link", { name: "Download TXT" }).click();
  const download = await downloadPromise;
  expect(await readFile(await download.path(), "utf8")).toBe(transcript);
  await expect(page.locator("audio")).toHaveAttribute("src", /fake-playback\/audio\.m4a/);
});
