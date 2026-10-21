import { test, expect } from "@playwright/test";

/** UI-08: the shared workspace shell, information architecture and capability statuses. */
const sections = [
  { path: "/workspace", nav: "总览", title: "总览", badge: null },
  { path: "/workbench", nav: "调用检查", title: "调用检查", badge: "网页可用" },
  { path: "/workspace/tasks", nav: "复现", title: "复现", badge: "网页可用" },
  { path: "/workspace/diagnosis", nav: "分析", title: "分析", badge: "网页可用" },
  { path: "/workspace/repairs", nav: "修复", title: "修复", badge: "网页可用" },
  { path: "/workspace/memory", nav: "记忆", title: "记忆", badge: "网页可用" },
  { path: "/workspace/connections", nav: "环境与接入", title: "环境与接入", badge: "网页可用" },
] as const;

test("工作区共用外壳：导航、页头状态、页面标题，不展示里程碑编号", async ({ page }) => {
  const writes: string[] = [];
  page.on("request", request => { if (request.method() !== "GET" && request.method() !== "HEAD") writes.push(request.url()); });
  const titles = new Set<string>();
  for (const section of sections) {
    await page.goto(section.path);
    const nav = page.getByRole("navigation", { name: "产品模块" });
    for (const item of ["总览", "调用检查", "复现", "分析", "修复", "记忆"]) await expect(nav.getByRole("link", { name: item, exact: true })).toBeVisible();
    await expect(page.getByRole("navigation", { name: "设置" }).getByRole("link", { name: "环境与接入" })).toBeVisible();
    await expect(page.getByRole("complementary", { name: "工作区" })).not.toContainText(/项目记忆|回归验证|预览/);
    const current = page.getByRole("complementary", { name: "工作区" }).locator('[aria-current="page"]');
    await expect(current).toHaveText(section.nav);
    await expect(page.getByRole("heading", { level: 1 })).toContainText(section.title);
    if (section.badge) await expect(page.locator(".page-header [data-capability]")).toContainText(section.badge);
    const main = page.locator("main");
    await expect(main).not.toContainText(/\b(?:P[0-2]-\d|UI-\d|DOC-\d|T0[5-7]\b)/);
    await expect(main).not.toContainText(/当前能力与后续计划|开发能力快照|独立评审|验收记录/);
    titles.add(await page.title());
  }
  expect(titles.size).toBe(sections.length);
  expect(writes).toEqual([]);
});

test("旧地址重定向到合并后的页面", async ({ page }) => {
  await page.goto("/workspace/regression");
  await expect(page).toHaveURL(/\/workspace\/repairs\?tab=regression$/);
});

test("侧栏环境状态可展开，列出本机服务、复现环境与模型", async ({ page }) => {
  await page.goto("/workspace");
  const trigger = page.getByRole("complementary", { name: "工作区" }).locator(".env-trigger");
  await expect(trigger).not.toHaveText(/正在检查环境/);
  await trigger.click();
  const pop = page.getByRole("dialog", { name: "本机环境状态" });
  await expect(pop).toContainText("本机服务");
  await expect(pop).toContainText("复现环境");
  await expect(pop).toContainText("模型");
  await page.keyboard.press("Escape");
  await expect(pop).toHaveCount(0);
  await expect(trigger).toHaveAttribute("aria-expanded", "false");
});

test("本机服务不可用时，环境状态明确提示而不是保持绿色", async ({ page }) => {
  await page.route("**/api/**", route => route.fulfill({ status: 503, body: "" }));
  await page.goto("/workspace");
  const trigger = page.getByRole("complementary", { name: "工作区" }).locator(".env-trigger");
  await expect(trigger).toContainText("本机服务未连接");
  await expect(trigger.locator(".status-dot")).toHaveAttribute("data-tone", "fail");
});

test("手机宽度通过抽屉导航，Esc 关闭", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/workspace");
  const toggle = page.getByRole("button", { name: "打开导航" });
  await toggle.click();
  const nav = page.getByRole("navigation", { name: "产品模块" });
  await expect(nav.getByRole("link", { name: "复现", exact: true })).toBeInViewport();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("button", { name: "打开导航" })).toHaveAttribute("aria-expanded", "false");
  await page.getByRole("button", { name: "打开导航" }).click();
  await nav.getByRole("link", { name: "复现", exact: true }).click();
  await expect(page).toHaveURL(/\/workspace\/tasks/);
  await expect(page.getByRole("button", { name: "打开导航" })).toHaveAttribute("aria-expanded", "false");
});

test("不存在的页面显示品牌化 404", async ({ page }) => {
  const response = await page.goto("/workspace/toString");
  expect(response?.status()).toBe(404);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("这个页面不存在");
  await page.getByRole("link", { name: "回到总览" }).click();
  await expect(page).toHaveURL(/\/workspace$/);
});
