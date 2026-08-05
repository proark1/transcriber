import { expect, test } from "@playwright/test";

import { installMockApi } from "./support/mockApi.ts";

test("rejects invalid credentials, signs in, and protects private routes", async ({ page }) => {
  const state = await installMockApi(page);
  await page.goto("/");

  const privateStatuses = await page.evaluate(async () => {
    const requests: Array<[string, RequestInit?]> = [
      ["/api/recordings"],
      ["/api/uploads", { method: "POST", body: "{}" }],
      ["/api/recordings/missing/playback"],
      ["/api/recordings/missing/transcript"],
      ["/api/recordings/missing/retry", { method: "POST" }],
      ["/api/recordings/missing", { method: "DELETE" }],
    ];
    return Promise.all(requests.map(async ([path, init]) => (await fetch(path, init)).status));
  });
  expect(privateStatuses).toEqual([401, 401, 401, 401, 401, 401]);

  await page.getByLabel("Username").fill("Assad");
  await page.getByLabel("PIN").fill("000000");
  await page.getByRole("button", { name: "Continue" }).click();
  await expect(page.getByRole("alert")).toContainText(
    "That PIN is incorrect for this username.",
  );
  expect(state.authenticated).toBe(false);

  await page.getByLabel("PIN").fill("123456");
  await page.getByRole("button", { name: "Continue" }).click();
  await expect(page.getByRole("heading", { name: "Turn a recording into clean text." })).toBeVisible();
  await expect(page.getByText("assad", { exact: true })).toBeVisible();
  expect(state.loginAttempts).toBe(2);
});

test("registers a mixed-case username and keeps owner unavailable", async ({ page }) => {
  const state = await installMockApi(page);
  await page.goto("/");

  await page.getByLabel("Username").fill("OWNER");
  await page.getByLabel("PIN").fill("123456");
  await page.getByRole("button", { name: "Continue" }).click();
  await expect(page.getByRole("alert")).toHaveText("That username is unavailable.");
  expect(state.accounts.owner).toBeUndefined();

  await page.getByLabel("Username").fill("New-User");
  await page.getByRole("button", { name: "Continue" }).click();
  await expect(page.getByText("new-user", { exact: true })).toBeVisible();
  expect(state.accounts["new-user"]).toBeDefined();
});
