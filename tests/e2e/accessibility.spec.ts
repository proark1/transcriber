import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

import { installMockApi } from "./support/mockApi.ts";

test("critical login and upload screens have no serious accessibility violations", async ({ page }) => {
  await installMockApi(page);
  await page.goto("/");

  const loginResults = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
    .analyze();
  expect(loginResults.violations.filter((item) => ["serious", "critical"].includes(item.impact ?? ""))).toEqual([]);

  await expect(page.getByLabel("Username")).toBeFocused();
  await page.getByLabel("Username").fill("assad");
  await page.keyboard.press("Tab");
  await expect(page.getByLabel("PIN")).toBeFocused();
  await page.getByLabel("PIN").fill("123456");
  await page.keyboard.press("Enter");
  await expect(page.getByRole("heading", { name: "Turn a recording into clean text." })).toBeVisible();

  const workspaceResults = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
    .analyze();
  expect(
    workspaceResults.violations.filter((item) => ["serious", "critical"].includes(item.impact ?? "")),
  ).toEqual([]);
});

test("mobile workspace stays within a narrow phone viewport", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await installMockApi(page, { authenticated: true });
  await page.goto("/");

  expect(
    await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
  ).toBe(true);
  await expect(page.getByLabel("Open a recording")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Turn a recording into clean text." })).toBeVisible();
});
