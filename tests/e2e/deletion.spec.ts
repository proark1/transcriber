import { expect, test } from "@playwright/test";

import { completedRecording, installMockApi } from "./support/mockApi.ts";

test("requires confirmation and keeps a recording visible during safe deletion", async ({ page }) => {
  const recording = completedRecording();
  const state = await installMockApi(page, { authenticated: true, recordings: [recording] });
  await page.goto("/");
  await page.getByRole("button", { name: /Interview from iPhone\.m4a.*Ready/ }).click();

  await page.getByRole("button", { name: "Delete recording" }).click();
  await expect(page.getByRole("dialog", { name: "Delete this recording?" })).toContainText(
    "cannot be undone",
  );
  await page.getByRole("button", { name: "Keep recording" }).click();
  expect(state.deleteCalls).toBe(0);

  await page.getByRole("button", { name: "Delete recording" }).click();
  await page.getByRole("button", { name: "Delete permanently" }).click();
  expect(state.deleteCalls).toBe(1);
  await expect(page.getByRole("heading", { name: "Deleting safely" })).toBeVisible();
  await expect(page.getByRole("button", { name: /Interview from iPhone\.m4a.*Deleting/ })).toBeVisible();

  state.recordings = [];
  await page.reload();
  await expect(page.getByText("Finished recordings will stay here until you delete them.")).toBeVisible();
});
