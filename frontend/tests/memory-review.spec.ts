import { expect, test, type Page } from "@playwright/test";
import type { Overview } from "../lib/memory";

/** Independent review regressions: deliberately separate displayed pages from selected authority. */
async function reviewFixture(page: Page) {
  const draft = { kind: "repair_experience" as const, connection_id: "old-source", event_id: "old-event", tool_id: "calculator", source_version: "v1", title: "旧事件的经验", body: "保留原始来源；修改标题不能改变来源归属。" };
  const memory = { receipt: { resource_id: "memory-old", version: 3, revision: 2, state: "confirmed" as const }, kind: draft.kind, digest: "a".repeat(64), content: draft };
  const overview: Overview = {
    connections: [{ connection_id: "old-source", project_id: "demo", origin: "external_report", enabled: true, version: 1, credential_version: 0, credential_active: false }],
    memories: [{ ...memory, receipt: { ...memory.receipt, resource_id: "memory-first" }, content: { ...draft, title: "列表首条经验" } }, memory],
    events: [{ connection_id: "new-source", event_id: "new-event", event_type: "test_completed", outcome: "unknown", received_at: 1, processing: "done", memory_id: null }],
    grants: [], policies: [], jobs: [],
  };
  const writes: { path: string; payload: unknown }[] = [];
  await page.route("**/api/memory/**", async route => {
    const request = route.request(); const path = new URL(request.url()).pathname;
    let value: unknown;
    if (request.method() !== "GET") { writes.push({ path, payload: request.postDataJSON() }); value = {}; }
    else if (path.endsWith("/projects")) value = { projects: [{ project_id: "demo", created_at: 1 }] };
    else if (path.endsWith("/overview")) value = overview;
    else if (path.endsWith("/repair-source")) value = { source: null };
    else if (path.endsWith("/context")) value = { policy: null, grants: [] };
    else if (path.endsWith("/grants")) value = { grants: [] };
    else value = memory;
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(value) });
  });
  return { writes };
}

test("评审：旧事件不在当前页时显示与提交保持同一来源", async ({ page }) => {
  const { writes } = await reviewFixture(page);
  await page.goto("/workspace/memory?project=demo&memory=memory-old");
  await page.getByRole("button", { name: "修订内容" }).click();
  await expect(page.getByLabel("来源事件")).toHaveValue("old-source/old-event");
  await page.getByLabel("标题", { exact: true }).fill("只修改标题");
  await page.getByRole("button", { name: "保存草稿" }).click();
  await expect.poll(() => writes.length).toBe(1);
  expect(writes[0].payload).toMatchObject({ expected_version: 3, content: { connection_id: "old-source", event_id: "old-event" } });
});

test("评审：关闭详情后键盘焦点回到原选择而非首条", async ({ page }) => {
  await reviewFixture(page); await page.goto("/workspace/memory?project=demo");
  const row = page.getByRole("button", { name: /旧事件的经验/ });
  await row.focus(); await page.keyboard.press("Enter");
  await expect(page.getByRole("heading", { name: "旧事件的经验", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "关闭详情" }).click();
  await expect(row).toBeFocused();
});

test("评审：当前事件页没有旧来源不能声称从未收到事件", async ({ page }) => {
  await reviewFixture(page); await page.goto("/workspace/memory/hooks?project=demo");
  const source = page.getByRole("region", { name: "来源 old-source", exact: true });
  await expect(source).not.toContainText("等待首条事件");
});

for (const target of ["policy", "grants"] as const) {
  test(`评审：${target} 权威读取失败不能用默认版本覆盖`, async ({ page }) => {
    const { writes } = await reviewFixture(page);
    await page.route(target === "policy" ? "**/api/memory/**/context" : "**/api/memory/**/grants", route => route.fulfill({ status: 503, contentType: "application/json", body: "{}" }));
    if (target === "policy") {
      await page.goto("/workspace/memory?project=demo&memory=memory-old");
      await page.getByText("适用范围与索引", { exact: true }).click();
      await expect(page.getByRole("button", { name: "保存适用策略" })).toBeDisabled();
    } else {
      await page.goto("/workspace/memory/hooks?project=demo");
      await page.getByRole("button", { name: "管理来源" }).click();
      await page.getByText("内容外发授权", { exact: true }).click();
      await page.getByLabel("我允许所选内容用于上述外部服务调用").check();
      await expect(page.getByRole("button", { name: "保存授权" })).toBeDisabled();
    }
    await expect(page.locator(".memory-error[role=alert]")).toBeVisible(); expect(writes).toEqual([]);
  });
}
