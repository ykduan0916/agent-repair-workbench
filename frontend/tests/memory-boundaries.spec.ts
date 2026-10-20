import { test, expect, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

/** Mock browser contracts; not evidence of a live client Hook or model run. */
async function fixture(page: Page) {
  const calls: { method: string; path: string; body: any }[] = [];
  const connection = { connection_id: "local-tests", project_id: "demo", origin: "external_report", enabled: true, version: 1, credential_version: 0, credential_active: false };
  const event = { connection_id: "local-tests", event_id: "run-1", event_type: "test_completed", outcome: "failed", received_at: 1792370000, processing: "pending", memory_id: null as string | null };
  const content = { kind: "repair_experience", connection_id: "local-tests", event_id: "run-1", tool_id: "calculator", source_version: "v1", title: "保留数值类型", body: "返回结果应保持数字类型，修改后需要重新运行契约测试。" };
  const overview = { connections: [connection], events: [event], memories: [{ receipt: { resource_id: "memory-1", version: 1, revision: 1, state: "draft" }, kind: "repair_experience", digest: "a".repeat(64), content: content as typeof content | null }], grants: [] as any[], policies: [] as any[], jobs: [] };
  await page.route("**/api/memory/**", async route => {
    const request = route.request(), path = new URL(request.url()).pathname, method = request.method();
    const body = method === "GET" ? null : request.postDataJSON(); calls.push({ method, path, body });
    const reply = (value: unknown) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(value) });
    if (method !== "GET") expect(request.headers()["x-awb-local"]).toBe("1");
    if (path === "/api/memory/projects") return reply({ projects: [{ project_id: "demo", created_at: 1792370000 }] });
    if (path.endsWith("/overview")) return reply(overview);
    if (path.endsWith("/repair-source")) return reply({ source: null });
    if (path.endsWith("/context")) return reply({ policy: overview.policies[0] ?? null, grants: overview.grants });
    if (path.endsWith("/grants") && method === "GET") return reply({ grants: overview.grants });
    if (path.endsWith("/credential")) { connection.version++; connection.credential_version++; connection.credential_active = body.action === "rotate"; return reply({ credential_version: connection.credential_version, credential_active: connection.credential_active, ...(connection.credential_active ? { token: "only-once-test-token" } : {}) }); }
    if (path.endsWith("/connections/local-tests") && method === "PATCH") { connection.enabled = body.enabled; connection.version++; return reply({}); }
    if (path.endsWith("/events")) { overview.events.push({ ...event, event_id: body.event_id, outcome: body.outcome }); return reply({ accepted: true }); }
    if (path.endsWith("/process")) { overview.events.forEach(e => { e.processing = "done"; e.memory_id = "memory-1"; }); return reply({ processed: 1 }); }
    if (path.endsWith("/memories/memory-1") && method === "PATCH") { const m = overview.memories[0]; m.receipt.version++; if (body.state) { m.receipt.state = body.state; if (body.state === "deleted") m.content = null; } else { m.content = body.content; m.receipt.state = "draft"; m.receipt.revision++; } return reply(m.receipt); }
    if (path.endsWith("/policy")) { overview.policies = [{ memory_id: "memory-1", version: 1, generation: body.expected_generation, ...body.policy }]; return reply({ version: 1 }); }
    if (path.endsWith("/grants")) { overview.grants = [{ version: 1, ...body.grant }]; return reply({ version: 1 }); }
    if (path.endsWith("/engine")) return reply({ state: "running" });
    if (path.includes("/engine/")) return reply({ state: "done", result: { indexed: true } });
    return reply({});
  });
  return { calls, overview };
}
for (const theme of ["light", "dark"] as const) {
  test(`${theme} 经验在手机与桌面可读、无自动写入`, async ({ page }) => {
    const { calls } = await fixture(page);
    await page.setViewportSize({ width: 390, height: 844 }); await page.emulateMedia({ colorScheme: theme, reducedMotion: "reduce" });
    await page.goto("/workspace/memory?project=demo&memory=memory-1");
    await expect(page.getByRole("heading", { name: "项目经验", exact: true })).toBeVisible();
    await expect(page.getByRole("heading", { name: "保留数值类型" })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
    expect((await new AxeBuilder({ page }).include("main").withTags(["wcag2a", "wcag2aa"]).analyze()).violations).toEqual([]);
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.screenshot({ path: `../artifacts/m4r/memory-${theme}.png`, fullPage: true });
    expect(calls.filter(c => c.method !== "GET")).toEqual([]);
  });
}
test("确认、修订、撤回、删除使用当前版本并不自动索引", async ({ page }) => {
  const { calls } = await fixture(page);
  await page.goto("/workspace/memory?project=demo&memory=memory-1");
  await page.getByRole("button", { name: "确认经验", exact: true }).click(); await expect(page.getByRole("button", { name: "撤回经验", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "修订内容" }).click(); await page.getByLabel("标题", { exact: true }).fill("结果保持数字");
  await page.getByRole("button", { name: "保存草稿" }).click(); await expect(page.getByRole("heading", { name: "结果保持数字" })).toBeVisible();
  await page.getByRole("button", { name: "确认经验", exact: true }).click(); await page.getByRole("button", { name: "撤回经验", exact: true }).click();
  await page.getByRole("button", { name: "删除经验", exact: true }).click(); await page.getByRole("dialog").getByRole("button", { name: "删除经验" }).click();
  await expect(page.getByText("正文已删除。来源与状态记录仍保留，用于核对删除和清理过程。")).toBeVisible();
  const writes = calls.filter(c => c.method === "PATCH"); expect(writes.map(c => c.body.expected_version)).toEqual([1, 2, 3, 4, 5]);
  expect(new Set(writes.map(c => c.body.request_id)).size).toBe(5); expect(calls.some(c => c.path.endsWith("/engine"))).toBe(false);
});
test("凭据一次性展示，网页连通测试与处理状态分开", async ({ page }) => {
  const { calls } = await fixture(page);
  await page.goto("/workspace/connections?project=demo#memory");
  const region = page.getByRole("region", { name: "经验与事件", exact: true });
  await region.getByRole("button", { name: "管理来源" }).click(); await region.getByRole("button", { name: "生成投递凭据" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "生成凭据" }).click(); await expect(page.getByLabel("一次性投递凭据")).toHaveValue("only-once-test-token");
  expect(await page.evaluate(() => JSON.stringify({ ...localStorage, ...sessionStorage }))).not.toContain("only-once-test-token");
  await region.getByRole("button", { name: "隐藏凭据" }).click(); await expect(page.getByLabel("一次性投递凭据")).toHaveCount(0);
  await region.getByRole("button", { name: "发送连通测试事件" }).click(); await expect(region).toContainText("不代表 Codex Hook 已安装或已投递");
  await region.locator("summary").filter({ hasText: "最近事件与处理结果" }).click(); await expect(region.getByText("待处理", { exact: true })).toHaveCount(2);
  await region.getByRole("button", { name: "处理待办事件" }).click(); await expect(region.getByText("已处理", { exact: true })).toHaveCount(2);
  await region.getByRole("button", { name: "暂停接收" }).click(); await expect(region.getByRole("button", { name: "发送连通测试事件" })).toBeDisabled();
  expect(calls.filter(c => c.path.endsWith("/events") && c.method === "POST")[0].body.outcome).toBe("unknown"); expect(calls.filter(c => c.method === "PATCH")[0].body.expected_version).toBe(2);
});
test("外发显式勾选，策略、确认和索引分开执行", async ({ page }) => {
  const { calls } = await fixture(page);
  await page.goto("/workspace/connections?project=demo#memory"); await page.getByRole("button", { name: "管理来源" }).click();
  await page.getByText("内容外发授权", { exact: true }).click(); await expect(page.getByRole("button", { name: "保存授权" })).toBeDisabled();
  await page.getByLabel("我允许所选内容用于上述外部服务调用").check(); await page.getByRole("button", { name: "保存授权" }).click(); await expect(page.getByRole("button", { name: "保存授权" })).toBeDisabled();
  expect(calls.find(c => c.path.endsWith("/grants") && c.method === "POST")?.body.grant.purpose).toBe("index");
  await page.goto("/workspace/memory?project=demo&memory=memory-1"); await page.getByText("适用范围与索引", { exact: true }).click(); await expect(page.getByRole("button", { name: "建立索引" })).toBeDisabled();
  await page.getByRole("button", { name: "保存适用策略" }).click(); await page.getByRole("button", { name: "确认经验", exact: true }).click();
  await page.getByText("适用范围与索引", { exact: true }).click(); expect(calls.filter(c => c.path.endsWith("/engine"))).toHaveLength(0);
  await page.getByRole("button", { name: "建立索引" }).click(); await expect(page.getByText("索引已建立。后续检索仍会核对确认状态、授权与适用版本。")).toBeVisible(); expect(calls.filter(c => c.path.endsWith("/engine"))).toHaveLength(1);
});
test("写入响应丢失不会自动重试或显示成功", async ({ page }) => {
  await fixture(page); let writes = 0;
  await page.route("**/api/memory/projects/demo/memories/memory-1", route => { writes++; return route.abort(); });
  await page.goto("/workspace/memory?project=demo&memory=memory-1"); await page.getByRole("button", { name: "确认经验", exact: true }).click();
  await expect(page.getByRole("alert")).toBeVisible(); await expect(page.getByRole("button", { name: "撤回经验", exact: true })).toHaveCount(0); expect(writes).toBe(1);
});

test("未知索引请求跨刷新保留标识且不会再次提交", async ({ page }) => {
  const { calls } = await fixture(page);
  await page.route("**/api/memory/projects/demo/engine/*", route => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ state: "unknown", result: { reason: "interrupted" } }) }));
  await page.goto("/workspace/memory?project=demo&memory=memory-1"); await page.getByRole("button", { name: "确认经验", exact: true }).click(); await page.getByText("适用范围与索引", { exact: true }).click();
  await page.getByRole("button", { name: "建立索引" }).click(); await expect(page.getByText("处理结果未知，已停止自动重试。请核对任务记录。")).toBeVisible();
  await expect(page.getByRole("button", { name: "建立索引" })).toBeDisabled(); await page.reload(); await page.getByText("适用范围与索引", { exact: true }).click();
  await expect(page.getByText("处理结果未知，已停止自动重试。请核对任务记录。")).toBeVisible(); await expect(page.getByRole("button", { name: "建立索引" })).toBeDisabled(); expect(calls.filter(c => c.path.endsWith("/engine"))).toHaveLength(1);
});

test("明确拒绝接收的索引请求允许用户纠正后再提交", async ({ page }) => {
  await fixture(page);
  await page.route("**/api/memory/projects/demo/engine", route => route.fulfill({ status: 403, contentType: "application/json", body: '{"error":"forbidden"}' }));
  await page.goto("/workspace/memory?project=demo&memory=memory-1"); await page.getByRole("button", { name: "确认经验", exact: true }).click(); await page.getByText("适用范围与索引", { exact: true }).click();
  await page.getByRole("button", { name: "建立索引" }).click(); await expect(page.getByText("当前来源未获授权或连接已暂停。")).toBeVisible(); await expect(page.getByRole("button", { name: "建立索引" })).toBeEnabled();
});

test("经验分页可达更早记录，深链接按权威详情读取", async ({ page }) => {
  const { overview } = await fixture(page); const old = structuredClone(overview.memories[0]); old.receipt.resource_id = "memory-old"; old.content!.title = "更早的经验";
  await page.route("**/api/memory/projects/demo/overview**", route => {
    const pageIndex = Number(new URL(route.request().url()).searchParams.get("memories_page") ?? 0);
    const info = { page: 0, has_more: false, next_page: null };
    return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ ...overview, memories: pageIndex ? [old] : overview.memories, pagination: { connections: info, events: info, grants: info, policies: info, jobs: info, memories: { page: pageIndex, has_more: !pageIndex, next_page: pageIndex ? null : 1 } } }) });
  });
  await page.route("**/api/memory/projects/demo/memories/memory-old", route => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(old) }));
  await page.goto("/workspace/memory?project=demo");
  const pager = page.getByRole("navigation", { name: "经验分页" }); await pager.getByRole("button", { name: "下一页" }).click();
  await expect(page.getByRole("button", { name: /更早的经验/ })).toBeVisible(); await expect(pager.getByRole("button", { name: "下一页" })).toBeDisabled();
  await page.goto("/workspace/memory?project=demo&memory=memory-old"); await expect(page.getByRole("heading", { name: "更早的经验" })).toBeVisible();
});

test("深色手机接入表单可访问，凭据配置只下载到用户选择位置", async ({ page }) => {
  await fixture(page); await page.setViewportSize({ width: 390, height: 844 }); await page.emulateMedia({ colorScheme: "dark", reducedMotion: "reduce" });
  await page.goto("/workspace/connections?project=demo#memory"); await page.getByRole("button", { name: "管理来源" }).click(); await page.getByRole("button", { name: "生成投递凭据" }).click(); await page.getByRole("dialog").getByRole("button", { name: "生成凭据" }).click();
  await page.getByLabel("允许采集的项目绝对路径").fill("/tmp/approved-project"); await page.getByLabel("允许记录的测试命令（每行一条，精确匹配）").fill("uv run pytest");
  const download = page.waitForEvent("download"); await page.getByRole("button", { name: "下载本机接入配置" }).click(); expect((await download).suggestedFilename()).toBe("connection.json");
  expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
  expect((await new AxeBuilder({ page }).include("#memory").withTags(["wcag2a", "wcag2aa"]).analyze()).violations).toEqual([]);
});
