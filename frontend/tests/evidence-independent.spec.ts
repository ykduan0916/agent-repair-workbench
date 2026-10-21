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

test("独立：键盘打开和收起证据后回到原入口", async ({ page }) => {
  await routes(page, true);
  await page.goto(`/workspace/diagnosis?analysis=${ID}`);
  const library = page.getByRole("region", { name: "证据", exact: true });
  const opener = library.getByRole("button", { name: "调用契约检查" });
  await opener.focus(); await page.keyboard.press("Enter");
  await expect(page.locator("#evidence-reader-title")).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(library.getByRole("button", { name: "收起证据内容" })).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(opener).toBeFocused();
});
for (const mode of ["light", "dark"]) test(`独立：源码契约与过期引用 ${mode}`, async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.addInitScript(theme => localStorage.setItem("awb-theme", theme), mode);
  await routes(page, true);
  let variant = "source";
  await page.route("**/api/analyses/*/evidence", route => {
    const payload = evidence();
    const item: any = payload.evidence[0];
    item.kind = variant; item.findings = []; item.report = null;
    item.title = variant === "source" ? "源码片段" : variant === "contract" ? "输入输出契约" : "证据内容不可用";
    item.description = variant === "unavailable" ? "仅保留引用，具体内容未保存或当前版本不匹配" : "已核对公开测试数据";
    item.source = variant === "source" ? { path: "source/server.py", start_line: 107, end_line: 110, text: "def lookup_result():\n    return {'count': '1'}\n" } : null;
    item.schemas = variant === "contract" ? {tool_name:"lookup",input_schema:{type:"object",properties:{id:{type:"integer"}}},output_schema:{type:"object",properties:{count:{type:"integer"}}}} : null;
    return route.fulfill({json:payload});
  });
  for (const next of ["source", "contract", "unavailable"]) {
    variant = next;
    await page.goto(`/workspace/diagnosis?analysis=${ID}`);
    const library = page.getByRole("region", { name: "证据", exact: true });
    await library.getByRole("button", { name: next === "source" ? "源码片段" : next === "contract" ? "输入输出契约" : "证据内容不可用" }).click();
    if (next === "source") await expect(library.getByLabel("已核对的源码片段")).toContainText("lookup_result");
    if (next === "contract") await expect(library.getByLabel("返回契约")).toContainText("integer");
    if (next === "unavailable") await expect(library).toContainText("具体内容未保存或当前版本不匹配");
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    expect((await new AxeBuilder({ page }).include(".ev-library").analyze()).violations).toEqual([]);
    await library.screenshot({path:`../artifacts/evidence-review-${mode}-${next}.png`});
  }
});

for (const mismatch of ["task", "ref-id", "ref-hash", "missing", "source-range"]) test(`独立：拒绝错配证据 ${mismatch}`, async ({ page }) => {
  await routes(page, true);
  await page.route("**/api/analyses/*/evidence", route => {
    const payload = evidence();
    if (mismatch === "task") payload.task_id = "99999999-9999-4999-8999-999999999999";
    if (mismatch === "ref-id") payload.evidence[0].ref = { ...ref, evidence_id: "99999999-9999-4999-8999-999999999999" };
    if (mismatch === "ref-hash") payload.evidence[0].ref = { ...ref, sha256: "f".repeat(64) };
    if (mismatch === "missing") payload.evidence = [];
    if (mismatch === "source-range") Object.assign(payload.evidence[0], { kind: "source", report: null, findings: [], source: { path: "source/server.py", start_line: 1, end_line: 500, text: "UNVERIFIED_SOURCE" } });
    return route.fulfill({json: payload});
  });
  await page.goto(`/workspace/diagnosis?analysis=${ID}`);
  const library = page.getByRole("region", { name: "证据", exact: true });
  await library.getByRole("button", { name: "证据 1" }).click();
  await expect(library).toContainText("证据与当前分析版本不一致");
  await expect(library.locator(".ev-findings")).toHaveCount(0);
  await expect(library).not.toContainText("UNVERIFIED_SOURCE");
});

for (const mode of ["recover", "bounded", "server-error", "unmount"] as const) test(`独立：证据限流重查 ${mode}`, async ({ page }) => {
  await routes(page, true);
  let calls = 0;
  await page.route("**/api/analyses/*/evidence", route => {
    calls++;
    if (mode === "recover" && calls >= 3) return route.fulfill({ json: evidence() });
    return route.fulfill({ status: mode === "server-error" ? 503 : 429, json: {error:"evidence_busy"} });
  });
  await page.goto(`/workspace/diagnosis?analysis=${ID}`);
  const library = page.getByRole("region", { name: "证据", exact: true });
  if (mode === "recover") {
    await expect(library.getByRole("button", { name: "调用契约检查" })).toBeVisible({timeout: 8000});
    expect(calls).toBeGreaterThanOrEqual(3);
    expect(calls).toBeLessThanOrEqual(4);
    return;
  }
  await library.getByRole("button", {name:"证据 1"}).click();
  if (mode === "unmount") {
    await page.getByRole("link", { name: "调用检查", exact: true }).click();
    await expect(page).toHaveURL(/\/workbench$/);
    const before = calls;
    await page.waitForTimeout(1200);
    expect(calls).toBe(before);
    return;
  }
  await expect(library.getByRole("alert")).toContainText("暂时不可用", {timeout: 9000});
  const stopped = calls;
  // Development StrictMode can make one immediately aborted first request.
  expect(calls).toBeGreaterThanOrEqual(mode === "bounded" ? 6 : 1);
  expect(calls).toBeLessThanOrEqual(mode === "bounded" ? 7 : 2);
  await page.waitForTimeout(1200);
  expect(calls).toBe(stopped);
});
