import { test, expect, Page } from "@playwright/test";
const ID = "10000000-0000-4000-8000-000000000001";
const REF = "20000000-0000-4000-8000-000000000001";
const SHA = "a".repeat(64);
const ref = { evidence_id: REF, sha256: SHA };
function detail(id = ID, finished = false) {
  const event = { kind: "created", role: null, assessment: null, reason_code: null, tool: null, evidence_refs: [], call_id: null, request_sha256: null, input_reserved: null, output_reserved: null, reservation_sha256: null, known_input_tokens: null, known_output_tokens: null, error_code: null };
  return { summary: { schema_version: 1, task_id: id, summary_revision: finished ? 2 : 1, source_kind: "synthetic", state: { task_id: id, intent: "check_only", phase: finished ? "finished" : "checking", revision: finished ? 2 : 1, outcome: finished ? "check_complete" : null }, started_at: "2026-10-14T00:00:00Z", finished_at: finished ? "2026-10-14T00:00:05Z" : null, budget: { total_tokens: 2000000, output_tokens: 8192, model_calls: 12, tool_calls: 20, candidates: 2, timeout_seconds: 600 }, usage: { model_calls: 0, tool_calls: 0, reserved_tokens: 0, known_input_tokens: 0, known_output_tokens: 0, usage_complete: true }, evidence_refs: [ref], execution_record_ids: [], proposal_count: 0, model_config_sha256: null, cost_amount: null }, sha256: SHA, events: [event, { ...event, kind: "phase_changed" }, ...(finished ? [{ ...event, kind: "finished" }] : [])].map((e, i) => ({ revision: i, created_at: 1791936000 + i, sha256: SHA, event: e })) };
}
const catalog = { schema_version: 1, cases: [{ id: "output_mismatch", label: "返回类型不一致" }, { id: "passing", label: "正常调用" }], intents: [{ id: "check_only", label: "契约检查", requires_model: false }, { id: "diagnose", label: "问题诊断", requires_model: true }, { id: "propose_repair", label: "修复建议", requires_model: true }], model: { configured: false, reason: "MODEL_NOT_CONFIGURED" }, slot: { status: "available", task_id: null } };
function evidence(id = ID) {
  return { schema_version: 1, task_id: id, summary_sha256: SHA, evidence: [{ ref, kind: "report", title: "调用契约检查", description: "已核对公开案例的检查摘要，返回结果存在契约不一致。", findings: [{ code: "output_schema_violation", path: "/result/structured_content/count" }], report: { status: "failed", input: "passed", output: "failed" }, source: null, schemas: null }] };
}
async function routes(page: Page, populated = false) {
  const state = { value: populated ? detail(ID, true) : null as ReturnType<typeof detail> | null, starts: [] as Record<string, string>[], stops: 0, lost: false };
  await page.route("**/api/analysis-runs**", async route => {
    const req = route.request(), path = new URL(req.url()).pathname;
    // Memory reads must never enter the task-creation branch or increment starts.
    if (req.method() === "GET" && /^\/api\/analysis-runs\/[^/]+\/memory$/.test(path)) return route.fulfill({ json: { state: "disabled", references: [], uses: [] } });
    if (path.endsWith("/catalog")) return route.fulfill({ json: { ...catalog, slot: state.value && !state.value.summary.finished_at ? { status: "busy", task_id: state.value.summary.task_id } : catalog.slot } });
    if (path.endsWith("/cancel")) { state.stops++; const id = state.value!.summary.task_id; state.value = detail(id, true); return route.fulfill({ json: { task_id: id, status: "cancel_requested" } }); }
    expect(req.method(), `Unexpected analysis request: ${path}`).toBe("POST");
    expect(path).toBe("/api/analysis-runs");
    const request = req.postDataJSON(); state.starts.push(request);
    const reused = !!state.value;
    state.value ??= detail(request.request_id);
    if (state.lost) return route.abort();
    return route.fulfill({ status: reused ? 200 : 202, json: { task_id: request.request_id, summary: state.value.summary, sha256: SHA, reused, cancel_requested: false } });
  });
  await page.route("**/api/analyses**", route => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/analyses") return route.fulfill({ json: { analyses: state.value ? [{ summary: state.value.summary, sha256: SHA }] : [] } });
    if (path.endsWith("/evidence")) return route.fulfill({ json: evidence(state.value?.summary.task_id ?? ID) });
    return route.fulfill(state.value ? { json: state.value } : { status: 404, json: { error: "analysis_not_found" } });
  });
  return state;
}

test("独立反例：取消旧任务后切换新占用任务仍能停止", async ({ page }) => {
  const second = "10000000-0000-4000-8000-000000000002";
  const state = await routes(page);
  state.value = detail(ID);
  await page.goto(`/workspace/diagnosis?analysis=${ID}`);
  await page.getByRole("button", { name: "停止", exact: true }).click();
  await page.getByRole("dialog").getByRole("button", { name: "停止", exact: true }).click();
  await expect(page.getByRole("region", { name: "实时执行事件" })).toContainText("分析已结束");
  state.value = detail(second);
  await page.getByRole("button", { name: "刷新", exact: true }).click();
  await page.getByRole("button", { name: "查看当前分析", exact: true }).click();
  await expect(page.getByRole("region", { name: "实时执行事件" })).toContainText("分析已创建");
  await expect(page.getByRole("button", { name: "停止", exact: true })).toBeEnabled();
});

test("独立反例：同一帧连续点击仅产生一次POST", async ({ page }) => {
  const state = await routes(page);
  await page.goto("/workspace/diagnosis");
  await page.getByLabel("目标", { exact: true }).selectOption("check_only");
  const button = page.getByRole("button", { name: "开始分析", exact: true });
  await expect(button).toBeEnabled();
  await button.evaluate((element: HTMLButtonElement) => { element.click(); element.click(); });
  await expect(page.getByRole("region", { name: "实时执行事件" })).toContainText("分析已创建");
  expect(state.starts).toHaveLength(1);
});

test("独立反例：未知提交离页回来保持ID且不自动POST", async ({ page }) => {
  const state = await routes(page); state.lost = true;
  await page.goto("/workspace/diagnosis");
  await page.getByLabel("目标", { exact: true }).selectOption("check_only");
  await page.getByRole("button", { name: "开始分析", exact: true }).click();
  await expect(page.getByRole("region", { name: "实时执行事件" })).toContainText("分析已创建");
  const pending = await page.evaluate(() => sessionStorage.getItem("awb-pending-analysis-v1"));
  await page.goto("/docs");
  await page.goto("/workspace/diagnosis");
  await expect(page.getByRole("region", { name: "实时执行事件" })).toContainText("分析已创建");
  expect(state.starts).toHaveLength(1);
  expect(await page.evaluate(() => sessionStorage.getItem("awb-pending-analysis-v1"))).toEqual(pending);
});

test("独立反例：切换任务后旧取消响应不污染当前控制台", async ({ page }) => {
  const state = await routes(page);
  const second = "10000000-0000-4000-8000-000000000002";
  state.value = detail(ID);
  let complete: (() => void) | undefined;
  const blocked = new Promise<void>(resolve => { complete = resolve; });
  let requested = false;
  await page.route(`**/api/analysis-runs/${ID}/cancel`, async route => {
    requested = true; await blocked;
    await route.fulfill({ json: { task_id: ID, status: "cancel_requested" } });
  });
  await page.goto(`/workspace/diagnosis?analysis=${ID}`);
  await page.getByRole("button", { name: "停止", exact: true }).click();
  await page.getByRole("dialog").getByRole("button", { name: "停止", exact: true }).click();
  await expect.poll(() => requested).toBe(true);
  state.value = detail(second);
  await page.getByRole("button", { name: "刷新", exact: true }).click();
  await page.getByRole("button", { name: "查看当前分析", exact: true }).click();
  await expect(page.getByRole("button", { name: "停止", exact: true })).toBeEnabled();
  complete!();
  await page.getByRole("button", { name: "刷新", exact: true }).click();
  await expect(page.locator(".toast-region")).not.toContainText("正在停止");
  await expect(page.getByRole("button", { name: "停止", exact: true })).toBeEnabled();
});

test("独立反例：键盘可以启动检查、关闭跟随并聚焦事件区域", async ({ page }) => {
  const state = await routes(page);
  await page.emulateMedia({ reducedMotion: "reduce", colorScheme: "dark" });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/workspace/diagnosis");
  await page.getByLabel("目标", { exact: true }).selectOption("check_only");
  await page.getByLabel("目标", { exact: true }).focus();
  await expect(page.getByRole("button", { name: "开始分析", exact: true })).toBeEnabled();
  await page.keyboard.press("Tab");
  await expect(page.getByRole("button", { name: "开始分析", exact: true })).toBeFocused();
  await page.keyboard.press("Enter");
  const viewport = page.getByRole("region", { name: "实时执行事件" });
  await expect(viewport).toContainText("分析已创建");
  const follow = page.getByRole("checkbox", { name: "自动滚动" });
  await follow.focus(); await page.keyboard.press("Space");
  await expect(follow).not.toBeChecked();
  await page.keyboard.press("Shift+Tab");
  await expect(viewport).toBeFocused();
  expect(await viewport.evaluate(el => getComputedStyle(el).outlineStyle)).not.toBe("none");
  expect(state.starts).toHaveLength(1);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test("独立反例：离开期间取消失败，返回原任务仍可重试停止", async ({ page }) => {
  const state = await routes(page);
  const second = "10000000-0000-4000-8000-000000000002";
  state.value = detail(ID);
  let complete: (() => void) | undefined;
  const blocked = new Promise<void>(resolve => { complete = resolve; });
  let requested = false;
  await page.route(`**/api/analysis-runs/${ID}/cancel`, async route => {
    requested = true; await blocked;
    await route.fulfill({ status: 503, json: { error: "unavailable" } });
  });
  await page.goto(`/workspace/diagnosis?analysis=${ID}`);
  await page.getByRole("button", { name: "停止", exact: true }).click();
  await page.getByRole("dialog").getByRole("button", { name: "停止", exact: true }).click();
  await expect.poll(() => requested).toBe(true);
  state.value = detail(second);
  await page.getByRole("button", { name: "刷新", exact: true }).click();
  await page.getByRole("button", { name: "查看当前分析", exact: true }).click();
  await expect(page.getByRole("button", { name: "停止", exact: true })).toBeEnabled();
  const response = page.waitForResponse(res => res.url().endsWith(`/${ID}/cancel`));
  complete!(); await response;
  state.value = detail(ID);
  await page.getByRole("button", { name: "刷新", exact: true }).click();
  await page.getByRole("button", { name: "查看当前分析", exact: true }).click();
  await expect(page.getByRole("button", { name: "停止", exact: true })).toBeEnabled();
});


test("独立反例：catalog短暂429自动恢复但不自动POST", async ({ page }) => {
  const state = await routes(page);
  let reads = 0;
  await page.route("**/api/analysis-runs/catalog", route => {
    reads++;
    return route.fulfill(reads < 3 ? { status: 429, json: { error: "runner_busy" } } : { json: catalog });
  });
  await page.goto("/workspace/diagnosis");
  await page.getByLabel("目标", { exact: true }).selectOption("check_only");
  await expect(page.getByRole("button", { name: "开始分析", exact: true })).toBeEnabled({ timeout: 10000 });
  // The shell's environment poller and the launcher share these reads; the launcher retries 429 only.
  expect(reads).toBeGreaterThanOrEqual(3);
  expect(reads).toBeLessThanOrEqual(5);
  expect(state.starts).toHaveLength(0);
});

test("分析页不再内嵌角色与工具接口，改为链接到环境与接入", async ({ page }) => {
  await routes(page);
  await page.goto("/workspace/diagnosis");
  await expect(page.getByRole("link", { name: "角色与工具接口" })).toHaveAttribute("href", "/workspace/connections#contracts");
  await expect(page.locator(".contract-tool-list")).toHaveCount(0);
  await expect(page.getByRole("region", { name: "协作接口", exact: true })).toHaveCount(0);
});
