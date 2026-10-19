import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

test("工程文档在本站完整阅读，目录和交叉引用可用", async ({ page }) => {
  await page.goto("/docs");
  await expect(page).toHaveTitle("文档 · Agent 修复台");
  const groups = page.locator(".doc-index-section[data-group] h2");
  await expect(groups).toHaveText(["使用指南", "参考", "工程记录"]);
  await expect(page.locator("#guide .doc-index-list a").first()).toHaveText("项目介绍与快速上手");
  const links = page.locator(".doc-index-list a");
  for (const href of await links.evaluateAll(elements => elements.map(el => el.getAttribute("href")))) expect(href).toMatch(/^\/docs\//);
  await page.locator("#reference").getByRole("link", { name: "架构与技术决策", exact: true }).click();
  await expect(page).toHaveURL(/\/docs\/02-architecture$/);
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await expect(page.locator(".doc-prose")).toContainText("LangGraph");
  const toc = page.getByRole("navigation", { name: "本页章节", exact: true });
  const section = toc.getByRole("link").first();
  const anchor = await section.getAttribute("href");
  await section.click();
  expect(new URL(page.url()).hash).toBe(encodeURI(anchor!));
  expect(await page.locator(".doc-prose h2").first().evaluate(el => el.id)).toBe(anchor!.slice(1));
  await page.goto("/docs/index");
  await page.locator(".doc-prose").getByRole("link", { name: "17 agents, tools and testing", exact: true }).click();
  await expect(page).toHaveURL(/\/docs\/17-agent-tools-testing-plan$/);
  await expect(page.locator(".doc-prose")).toContainText("A01");
  await page.goBack();
  await expect(page).toHaveURL(/\/docs\/index$/);
});

for (const theme of ["dark", "light"] as const) {
  for (const width of [1440, 390]) {
    test(`${theme} ${width}px 文档阅读排版与可访问性`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 });
      await page.emulateMedia({ colorScheme: theme, reducedMotion: "reduce" });
      for (const url of ["/docs/47-m2-r-engine-and-retrieval", "/docs/46-m1-r-authority-event-store", "/docs/45-mr-01-cognee-contract-validation", "/docs/44-p1-02-memory-product-and-integration", "/docs/42-p1-02-memory-research-design", "/docs/02-architecture", "/docs/17-agent-tools-testing-plan", "/docs/getting-started"]) {
        await page.goto(url);
        await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
        const diagrams = page.locator(".doc-diagram");
        for (let index = 0; index < await diagrams.count(); index++) {
          const image = diagrams.nth(index).locator("img");
          await expect(image).toBeVisible();
          await expect.poll(() => image.evaluate(el => (el as HTMLImageElement).naturalWidth)).toBeGreaterThan(0);
        }
        expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), url).toBe(false);
        const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
        expect(results.violations.map(v => ({ id: v.id, nodes: v.nodes.map(n => n.target) })), url).toEqual([]);
      }
      if (width === 390) {
        const code = page.locator(".doc-prose pre").first();
        await code.focus();
        await expect(code).toBeFocused();
        await page.locator(".doc-mobile-toc summary").click();
        await expect(page.getByRole("navigation", { name: "本页章节（移动版）" })).toBeVisible();
      }
    });
  }
}

test("未知文档返回404，源码入口指向独立GitHub仓库，工程记录有标注", async ({ page }) => {
  const response = await page.goto("/docs/not-a-document");
  expect(response?.status()).toBe(404);
  await page.goto("/docs/02-architecture");
  await expect(page).toHaveTitle("架构与技术决策 · 文档 · Agent 修复台");
  await expect(page.getByRole("navigation", { name: "文档目录", exact: true }).getByRole("link", { name: "架构与技术决策" })).toHaveAttribute("aria-current", "page");
  const source = page.getByRole("link", { name: /在 GitHub 上查看原文/ });
  await expect(source).toHaveAttribute("href", "https://github.com/ykduan0916/agent-repair-workbench/blob/main/docs/02-architecture.md");
  await expect(source).toHaveAttribute("target", "_blank");
  await expect(page.getByRole("banner").getByRole("link", { name: /GitHub/ })).toHaveAttribute("href", "https://github.com/ykduan0916/agent-repair-workbench");
  await expect(page.locator(".doc-record-note")).toHaveCount(0);
  await page.goto("/docs/03-roadmap");
  await expect(page.locator(".doc-breadcrumb")).toContainText("工程记录");
  await expect(page.locator(".doc-record-note")).toContainText("当前功能以更新记录为准");
});

test("架构图可阅读、随主题切换，手机键盘可滚动并查看源码", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 900 });
  await page.goto("/docs/02-architecture");
  const diagram = page.locator(".doc-diagram img");
  await expect(diagram).toBeVisible();
  await expect.poll(() => diagram.evaluate(el => (el as HTMLImageElement).naturalWidth)).toBeGreaterThan(0);
  expect(decodeURIComponent((await diagram.getAttribute("src"))!)).not.toContain("<foreignObject");
  const theme = page.getByRole("group", { name: "外观主题" });
  await theme.getByRole("button", { name: "浅色" }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  const lightImage = await diagram.getAttribute("src");
  await theme.getByRole("button", { name: "深色" }).click();
  await expect.poll(() => diagram.getAttribute("src")).not.toBe(lightImage);
  await expect.poll(() => diagram.evaluate(el => (el as HTMLImageElement).naturalWidth)).toBeGreaterThan(0);
  const scroll = page.locator(".doc-diagram-scroll");
  await scroll.focus();
  await page.keyboard.press("ArrowRight");
  await expect.poll(() => scroll.evaluate(el => el.scrollLeft)).toBeGreaterThan(0);
  await page.getByText("查看图表源码", { exact: true }).click();
  await expect(page.getByRole("region", { name: "图表源码，可横向滚动" })).toContainText("flowchart LR");
  expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
});
