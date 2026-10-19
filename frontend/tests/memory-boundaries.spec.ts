import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

for (const theme of ["light", "dark"] as const) {
  test(`${theme} 经验与事件展示真实边界并提供本站入口`, async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.emulateMedia({ colorScheme: theme, reducedMotion: "reduce" });
    const writes: string[] = [];
    page.on("request", request => { if (!["GET", "HEAD"].includes(request.method())) writes.push(request.url()); });
    await page.goto("/workspace/connections#memory");
    const region = page.getByRole("region", { name: "经验与事件", exact: true });
    await expect(region).toBeVisible();
    await expect(region.locator('[data-capability="planned"]')).toContainText("待开发");
    await expect(region.getByText("待开发", { exact: true })).toHaveCount(1);
    await expect(region).toContainText("不会自动采集外部会话");
    await expect(region.getByRole("button")).toHaveCount(0);
    expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
    expect((await new AxeBuilder({ page }).include("#memory").withTags(["wcag2a", "wcag2aa"]).analyze()).violations).toEqual([]);
    await region.screenshot({ path: `../artifacts/m1r/memory-${theme}.png` });
    await region.getByRole("link", { name: "查看存储与使用边界" }).click();
    await expect(page).toHaveURL(/\/docs\/46-m1-r-authority-event-store$/);
    await expect(page.locator(".doc-prose")).toContainText("不开放外部HTTP/Hook入口");
    expect(writes).toEqual([]);
  });
}
