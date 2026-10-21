import { test, expect, Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
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
test("未配置模型仍可直接检查，持久提交、停止和真实事件", async ({ page }) => {
  const state = await routes(page);
  await page.goto("/workspace/diagnosis");
  const launch = page.locator(".an-launch");
  // Model not configured: model intents are disabled with one reason; check-only is the default.
  await expect(launch.locator('option[value="diagnose"]')).toBeDisabled();
  await expect(launch.getByLabel("目标", { exact: true })).toHaveValue("check_only");
  await launch.getByLabel("目标", { exact: true }).selectOption("check_only");
  await launch.getByRole("button", { name: "开始分析", exact: true }).click();
  await expect(page.getByRole("region", { name: "实时执行事件" })).toContainText("分析已创建");
  expect(state.starts).toHaveLength(1);
  expect(await page.evaluate(() => JSON.parse(sessionStorage.getItem("awb-pending-analysis-v1")!))).toEqual(state.starts[0]);
  await page.reload();
  const stop = page.getByRole("region", { name: "执行控制台" }).getByRole("button", { name: "停止", exact: true });
  await expect(stop).toBeVisible();
  expect(state.starts).toHaveLength(1);
  await stop.click();
  // Stopping asks first; cancelling the dialog sends nothing.
  await page.getByRole("dialog").getByRole("button", { name: "取消" }).click();
  expect(state.stops).toBe(0);
  await stop.click();
  await page.getByRole("dialog").getByRole("button", { name: "停止", exact: true }).click();
  await expect(page.getByRole("region", { name: "实时执行事件" })).toContainText("分析已结束");
  expect(state.stops).toBe(1);
  await expect.poll(() => page.evaluate(() => sessionStorage.getItem("awb-pending-analysis-v1"))).toBeNull();
});
test("提交响应丢失刷新只找回原任务，不自动二次执行", async ({ page }) => {
  const state = await routes(page); state.lost = true;
  await page.goto("/workspace/diagnosis");
  await page.getByLabel("目标", { exact: true }).selectOption("check_only");
  await page.getByRole("button", { name: "开始分析", exact: true }).click();
  await expect(page.getByRole("region", { name: "实时执行事件" })).toContainText("分析已创建");
  await page.reload();
  await expect(page.getByRole("region", { name: "实时执行事件" })).toContainText("分析已创建");
  expect(state.starts).toHaveLength(1);
});
test("存储被拒绝时不发起执行", async ({ page }) => {
  const state = await routes(page);
  await page.addInitScript(() => { Storage.prototype.setItem = () => { throw new Error("denied"); }; });
  await page.goto("/workspace/diagnosis");
  await page.getByLabel("目标", { exact: true }).selectOption("check_only");
  await expect(page.getByRole("button", { name: "开始分析", exact: true })).toBeDisabled();
  expect(state.starts).toHaveLength(0);
});
for (const mode of ["light", "dark"]) for (const width of [1440, 390]) test(`可读证据与控制台 ${mode} ${width}`, async ({ page }) => {
  await page.setViewportSize({ width, height: 900 });
  await page.addInitScript(theme => localStorage.setItem("awb-theme", theme), mode);
  await routes(page, true);
  await page.goto(`/workspace/diagnosis?analysis=${ID}`);
  const library = page.getByRole("region", { name: "证据", exact: true });
  await library.getByRole("button", { name: "调用契约检查" }).click();
  await expect(library.locator(".ev-findings")).toContainText("不符合");
  await expect(library).toContainText("/result/structured_content/count");
  await expect(library.getByText(REF, { exact: true })).not.toBeVisible();
  await library.getByText("技术详情", { exact: true }).click();
  await expect(library.getByText(REF, { exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  // Page content only; the shared shell's mobile bar landmark is tracked separately.
  expect((await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze()).violations).toEqual([]);
  await page.screenshot({ path: `../artifacts/console-evidence-${mode}-${width}.png`, fullPage: true });
});
test("证据摘要不一致时不显示可能错配的内容", async ({ page }) => {
  await routes(page, true);
  await page.route("**/api/analyses/*/evidence", route => route.fulfill({ json: { ...evidence(), summary_sha256: "b".repeat(64) } }));
  await page.goto(`/workspace/diagnosis?analysis=${ID}`);
  const library = page.getByRole("region", { name: "证据", exact: true });
  await library.getByRole("button", { name: "证据 1" }).click();
  await expect(library).toContainText("证据与当前分析版本不一致");
  await expect(library.locator(".ev-findings")).toHaveCount(0);
});

test("未知提交重查遇到忙响应仍保留原请求关联", async ({ page }) => {
  const state = await routes(page);
  let pending: Record<string, string> | null = null;
  let writes = 0;
  await page.route("**/api/analysis-runs", route => {
    writes++;
    const request = route.request().postDataJSON();
    if (!pending) { pending = request; return route.abort(); }
    expect(request).toEqual(pending);
    return route.fulfill({ status: 429, json: { error: "runner_busy" } });
  });
  await page.goto("/workspace/diagnosis");
  await page.getByLabel("目标", { exact: true }).selectOption("check_only");
  await page.getByRole("button", { name: "开始分析", exact: true }).click();
  await page.getByRole("button", { name: "用同一 ID 重新提交" }).click();
  await expect(page.getByRole("button", { name: "用同一 ID 重新提交" })).toBeEnabled();
  expect(await page.evaluate(() => JSON.parse(sessionStorage.getItem("awb-pending-analysis-v1")!))).toEqual(pending);
  await expect(page.getByRole("button", { name: "开始分析", exact: true })).toBeDisabled();
  state.value = detail(pending!.request_id, true);
  await page.reload();
  await expect(page.getByRole("region", { name: "实时执行事件" })).toContainText("分析已结束");
  expect(writes).toBe(2);
  await expect.poll(() => page.evaluate(() => sessionStorage.getItem("awb-pending-analysis-v1"))).toBeNull();
});
