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
    await expect(region.locator('[data-capability="cli"]')).toContainText("CLI 可用");
    await expect(region.getByText("待开发", { exact: true })).toHaveCount(1);
    await expect(region).toContainText("不会自动采集外部会话");
    await expect(region.getByRole("button")).toHaveCount(0);
    expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
    expect((await new AxeBuilder({ page }).include("#memory").withTags(["wcag2a", "wcag2aa"]).analyze()).violations).toEqual([]);
    await region.screenshot({ path: `../artifacts/m2r/memory-${theme}.png` });
    await region.getByRole("link", { name: "查看配置与使用方法" }).click();
    await expect(page).toHaveURL(/\/docs\/47-m2-r-engine-and-retrieval#/);
    expect(decodeURIComponent(new URL(page.url()).hash)).toBe("#6-本机使用");
    await expect(page.locator('[id="6-本机使用"]')).toBeInViewport();
    await expect(page.locator(".doc-prose")).toContainText("不安装外部Hook");
    expect(writes).toEqual([]);
  });
}
