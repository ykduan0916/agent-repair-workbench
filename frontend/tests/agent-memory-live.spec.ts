import { test, expect } from "@playwright/test";
import { mkdir, writeFile } from "node:fs/promises";
import { join } from "node:path";

/** No route mocks and no execution POST: inspect a completed isolated real-model gate. */
test.use({ trace: "off", screenshot: "off" });
const taskId = process.env.AWB_M3_TASK_ID ?? "";
test.skip(process.env.AWB_M3_UI_LIVE !== "1", "requires an explicitly selected isolated M3 gate");

test("真实M3网页：核对角色引用、费用与恢复已保存经验草稿", async ({ page, request, baseURL }) => {
  expect(taskId).toMatch(/^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/);
  // Fixed isolated port: never run against the resident user's 3010 state.
  expect(new URL(baseURL!).port).toBe("3018");
  const writes: string[] = [];
  page.on("request", r => { if (new URL(r.url()).pathname.startsWith("/api/") && r.method() !== "GET") writes.push(`${r.method()} ${new URL(r.url()).pathname}`); });
  const read = async (path: string) => { const response = await request.get(path); expect(response.status(), path).toBe(200); return response.json(); };
  const detail = await read(`/api/repairs/${taskId}`);
  const audit = await read(`/api/repair-runs/${taskId}/memory`);
  // A valid UUID with no analysis record must reach FastAPI's JSON not_found,
  // rather than Next's HTML 404. This exercises the separate analysis rewrite.
  const missingAnalysis = await request.get(`/api/analysis-runs/${taskId}/memory`);
  expect(missingAnalysis.status()).toBe(404);
  expect(await missingAnalysis.json()).toEqual({ error: "not_found" });
  expect(detail.task.phase).toBe("finished");
  expect(detail.task.recorded_outcome).toBe("candidate_verified");
  expect(audit.state).toBe("done");
  expect(audit.reason).toBe("matched");
  expect(audit.references.length).toBeGreaterThan(0);
  expect(audit.references.length).toBeLessThanOrEqual(8);
  expect(audit.uses.some((use: { role: string; refs: unknown[] }) => use.role === "diagnosis" && use.refs.length)).toBe(true);
  expect(audit.uses.some((use: { role: string; refs: unknown[] }) => use.role === "repair" && use.refs.length)).toBe(true);
  const verification = audit.uses.filter((use: { role: string }) => use.role === "verification");
  // A role may have no audit row when it carries no reference; no row may carry verification references.
  expect(verification.every((use: { refs: unknown[] }) => use.refs.length === 0)).toBe(true);
  expect(audit.references.every((ref: Record<string, unknown>) => !Object.hasOwn(ref, "body"))).toBe(true);
  expect(audit.uses.every((use: { refs: Record<string, unknown>[] }) => use.refs.every(ref => !Object.hasOwn(ref, "body")))).toBe(true);
  const comparison = detail.comparisons.find((c: { current_outcome: string; verification: string }) => c.current_outcome === "candidate_verified" && c.verification === "verified");
  expect(comparison).toBeTruthy();
  const decision = await read(`/api/repairs/${taskId}/candidates/${comparison.candidate_id}/decision`);
  expect(decision.can_download).toBe(true);
  const proof = await read(`/api/repairs/${taskId}/candidates/${comparison.candidate_id}/memory-draft?project_id=${encodeURIComponent(audit.project_id)}`);
  expect(proof.state).toBe("draft");
  expect(proof.source_current_valid).toBe(true);
  // Exercise the POST rewrite without creating any business data: the backend
  // must reject an empty, schema-invalid payload at its existing write boundary.
  const invalidDraft = await request.post(`/api/repairs/${taskId}/candidates/${comparison.candidate_id}/memory-draft`, {
    headers: { Origin: baseURL!, "X-AWB-Local": "1" },
    data: {},
  });
  expect(invalidDraft.status()).toBe(400);
  expect(await invalidDraft.json()).toEqual({ error: "invalid_request" });

  await page.goto(`/workspace/repairs?task=${taskId}`);
  const experience = page.locator(".memory-task-usage");
  await experience.locator("summary").first().click();
  await expect(experience).toContainText("金额未知");
  await expect(experience).toContainText("不能证明模型采纳了建议");
  await expect(experience.getByRole("link", { name: /查看来源/ })).toHaveCount(audit.references.length);
  const saved = page.locator(".memory-task-choice").filter({ has: page.locator("summary").filter({ hasText: "保存经验草稿" }) });
  await saved.locator("summary").first().click();
  await saved.getByRole("combobox", { name: "项目", exact: true }).selectOption(audit.project_id);
  await saved.getByLabel("经验来源").selectOption("fixture");
  await saved.getByRole("button", { name: "使用此来源" }).click();
  await saved.getByRole("button", { name: "查询保存结果" }).click();
  await expect(saved.getByRole("link", { name: "查看并核对经验" })).toHaveAttribute("href", `/workspace/memory?project=${proof.project_id}&memory=${proof.memory_id}`);
  await saved.getByRole("link", { name: "查看并核对经验" }).click();
  await expect(page.getByRole("heading", { name: "经验详情", exact: true })).toBeVisible();
  await expect(page.getByText(/当前仍为接受且证据有效/)).toBeVisible();
  await expect(page.getByRole("button", { name: "确认经验", exact: true })).toBeEnabled();
  expect(writes).toEqual([]);

  const artifact = join(process.cwd(), "..", "artifacts", "m3r");
  await mkdir(artifact, { recursive: true, mode: 0o700 });
  await page.screenshot({ path: join(artifact, `browser-${taskId}.png`), fullPage: true });
  await writeFile(join(artifact, `browser-${taskId}.json`), JSON.stringify({
    schema_version: 1, status: "passed", task_id: taskId, project_id: audit.project_id,
    candidate_id: comparison.candidate_id, draft_id: proof.memory_id,
    references: audit.references.length, model_uses: audit.uses.map((use: { role: string; refs: unknown[] }) => ({ role: use.role, references: use.refs.length })),
    draft_state: proof.state, source_current_valid: proof.source_current_valid,
    api_writes: writes.length, providers_called_by_browser_test: false,
    rejected_api_probe: { analysis_get: 404, invalid_draft_post: 400 },
    boundary: "read_existing_isolated_real_model_gate_and_recover_existing_draft",
  }, null, 2), { mode: 0o600 });
});
