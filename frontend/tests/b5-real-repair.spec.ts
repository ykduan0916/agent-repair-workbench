/** B5 W12: actual model + Docker + production Next proxy. Never mocks or retries writes. */
import { expect, test, type APIRequestContext } from "@playwright/test";
import { createHash } from "node:crypto";
import { readFile, writeFile } from "node:fs/promises";
import path from "node:path";

test.skip(process.env.AWB_B5_LIVE !== "1", "Explicit B5 private isolated runner required");
const folder = process.env.AWB_B5_ARTIFACT_DIR;
const origin = "http://127.0.0.1:3018";
const sha = (body: Buffer) => createHash("sha256").update(body).digest("hex");

// Bounded retries are only for reads. Every observed 429 remains in the report.
async function read(request: APIRequestContext, url: string, reads: number[]) {
  for (let attempt = 0; attempt < 8; attempt++) {
    const result = await request.get(url, { headers: { Origin: origin } });
    reads.push(result.status());
    if (result.status() !== 429) {
      expect(result.ok(), `read failed with HTTP ${result.status()}`).toBe(true);
      return result.json();
    }
    await new Promise(resolve => setTimeout(resolve, Math.min(1000 * 2 ** attempt, 8000)));
  }
  throw new Error("bounded_read_retries_exhausted");
}

test("real model pending: explicit stop preserves unknown usage and never replays", async ({ page }) => {
  if (!folder) throw new Error("private_artifact_directory_required");
  const evidence: Record<string, unknown> = { case_id: "output_mismatch", mode: "three_role", status: "failed", scenario: "model_pending_cancel" };
  const writes: string[] = [];
  const reads: number[] = [];
  page.on("request", req => {
    const url = new URL(req.url());
    if (url.pathname.startsWith("/api/") && req.method() !== "GET") writes.push(url.pathname);
  });
  try {
    await page.goto("/workspace/repairs");
    await page.getByLabel("案例", { exact: true }).selectOption("output_mismatch");
    await page.getByLabel("协作方式", { exact: true }).selectOption("three_role");
    await expect(page.getByRole("button", { name: "开始修复", exact: true })).toBeEnabled();
    const started = page.waitForResponse(r => new URL(r.url()).pathname === "/api/repair-runs" && r.request().method() === "POST");
    await page.getByRole("button", { name: "开始修复", exact: true }).click();
    const response = await started;
    evidence.start_http_status = response.status();
    expect(response.status()).toBe(202);
    const initial = await response.json();
    const taskId = initial.task_id;
    evidence.task_id = taskId;
    let receipt = initial;
    const pendingDeadline = Date.now() + 20000;
    while (Date.now() < pendingDeadline) {
      receipt = await read(page.request, `/api/repair-runs/${taskId}`, reads);
      evidence.before_cancel = receipt;
      if (receipt.task.phase === "model_pending" && receipt.task.usage.model_calls >= 1) break;
      if (receipt.task.phase === "finished") break;
      await page.waitForTimeout(100);
    }
    expect(receipt.task.phase, "missed live pending window; retain failure without retry").toBe("model_pending");
    const callsBefore = receipt.task.usage.model_calls;
    await page.getByRole("button", { name: "停止修复", exact: true }).click();
    await page.getByRole("dialog").getByRole("button", { name: "停止修复", exact: true }).click();
    const deadline = Date.now() + 150000;
    while (Date.now() < deadline) {
      receipt = await read(page.request, `/api/repair-runs/${taskId}`, reads);
      evidence.last_receipt = receipt;
      if (receipt.task.phase === "finished" && !receipt.active) break;
      await page.waitForTimeout(500);
    }
    expect(receipt.task.phase).toBe("finished");
    expect(receipt.active).toBe(false);
    expect(receipt.cancel_requested).toBe(true);
    expect(receipt.task.outcome).toBe("outcome_unknown");
    expect(receipt.task.error_code).toBe("MODEL_USAGE_UNKNOWN");
    expect(receipt.task.usage.model_calls).toBe(callsBefore);
    expect(receipt.task.usage.usage_complete).toBe(false);
    expect(receipt.task.usage.reserved_tokens).toBe((evidence.before_cancel as typeof receipt).task.usage.reserved_tokens);
    expect(receipt.task.usage.reserved_tokens).toBeGreaterThan(0);
    const events = await read(page.request, `/api/repair-runs/${taskId}/events`, reads);
    evidence.events = events;
    expect(events.events.some((event: { operation: string; error_code?: string }) => event.operation === "model" && event.error_code === "MODEL_USAGE_UNKNOWN")).toBe(true);
    await page.reload();
    const after = await read(page.request, `/api/repair-runs/${taskId}`, reads);
    expect(after.task.usage).toEqual(receipt.task.usage);
    expect(after.active).toBe(false);
    expect(writes.filter(url => url === "/api/repair-runs")).toHaveLength(1);
    expect(writes.filter(url => url.endsWith("/cancel"))).toHaveLength(1);
    evidence.status = "passed";
    await page.screenshot({ path: path.join(folder, "model-cancel.png"), fullPage: true });
  } finally {
    evidence.writes = writes;
    evidence.read_http_statuses = reads;
    await writeFile(path.join(folder, "model-cancel.json"), JSON.stringify(evidence, null, 2), { mode: 0o600 });
  }
});

for (const caseId of ["output_mismatch", "counter_offset"]) {
  test(`real three-role ${caseId}: start, refresh, verify, decide, exact download, withdraw`, async ({ page }) => {
    if (!folder) throw new Error("private_artifact_directory_required");
    const evidence: Record<string, unknown> = { case_id: caseId, mode: "three_role", status: "failed" };
    const writes: Array<{ path: string; task_id?: string }> = [];
    const reads: number[] = [];
    let taskId = "";
    page.on("request", req => {
      const url = new URL(req.url());
      if (url.pathname.startsWith("/api/") && req.method() !== "GET") {
        writes.push({ path: url.pathname, task_id: req.postDataJSON()?.request_id });
      }
    });
    try {
      await page.goto("/workspace/repairs");
      await page.getByLabel("案例", { exact: true }).selectOption(caseId);
      await page.getByLabel("协作方式", { exact: true }).selectOption("three_role");
      await expect(page.getByRole("button", { name: "开始修复", exact: true })).toBeEnabled();
      const started = page.waitForResponse(r => new URL(r.url()).pathname === "/api/repair-runs" && r.request().method() === "POST");
      await page.getByRole("button", { name: "开始修复", exact: true }).click();
      const response = await started;
      evidence.start_http_status = response.status();
      expect(response.status()).toBe(202);
      const initial = await response.json();
      taskId = initial.task_id;
      evidence.task_id = taskId;
      expect(initial.case_id).toBe(caseId);
      expect(initial.mode).toBe("three_role");
      await expect.poll(() => new URL(page.url()).searchParams.get("task")).toBe(taskId);
      await page.reload();
      expect(new URL(page.url()).searchParams.get("task")).toBe(taskId);
      await expect(page.getByRole("region", { name: "实时执行事件", exact: true })).toBeVisible();
      let receipt = initial;
      const deadline = Date.now() + 660000;
      while (Date.now() < deadline) {
        receipt = await read(page.request, `/api/repair-runs/${taskId}`, reads);
        evidence.last_receipt = receipt;
        if (receipt.task.phase === "finished" && !receipt.active) break;
        await page.waitForTimeout(2000);
      }
      expect(receipt.task.phase, "task must reach actual terminal state").toBe("finished");
      expect(receipt.active).toBe(false);
      expect(receipt.task.outcome, "failed/inconclusive attempts must fail acceptance").toBe("candidate_verified");
      const usage = receipt.task.usage;
      const budget = receipt.task.budget;
      expect(usage.model_calls).toBeGreaterThanOrEqual(3);
      expect(usage.model_calls).toBeLessThanOrEqual(budget.model_calls);
      expect(usage.tool_calls).toBeLessThanOrEqual(budget.tool_calls);
      expect(usage.known_input_tokens + usage.known_output_tokens + usage.reserved_tokens).toBeLessThanOrEqual(budget.total_tokens);
      expect(usage.usage_complete).toBe(true);
      const events = await read(page.request, `/api/repair-runs/${taskId}/events`, reads);
      evidence.events = events;
      const roles = new Set(events.events.filter((e: { operation: string; kind: string }) => e.operation === "model" && e.kind === "reserved").map((e: { role: string }) => e.role));
      expect([...roles].sort()).toEqual(["diagnosis", "repair", "verification"]);
      const detail = await read(page.request, `/api/repairs/${taskId}`, reads);
      evidence.detail = detail;
      const comparisons = detail.comparisons.filter((c: { current_outcome: string; verification: string }) => c.current_outcome === "candidate_verified" && c.verification === "verified");
      expect(comparisons).toHaveLength(1);
      const candidateId = comparisons[0].candidate_id;
      evidence.verified_candidate_id = candidateId;
      expect(comparisons[0].slots).toHaveLength(4);
      expect(comparisons[0].slots.every((s: { cleanup: string }) => s.cleanup === "removed")).toBe(true);
      const raw = await read(page.request, `/api/repairs/${taskId}/candidates/${candidateId}/diff`, reads);
      const expected = Buffer.from(raw.diff.unified_diff, "utf8");
      await page.goto(`/workspace/repairs?task=${taskId}&candidate=${candidateId}`);
      const panel = page.getByRole("region", { name: "人工审批", exact: true });
      await expect(panel.getByRole("button", { name: "接受候选", exact: true })).toBeEnabled();
      await expect(panel.getByRole("button", { name: "下载补丁", exact: true })).toHaveCount(0);
      await panel.getByRole("button", { name: "接受候选", exact: true }).click();
      await expect(panel.getByText("已接受", { exact: true })).toBeVisible();
      const decisionPath = `/api/repairs/${taskId}/candidates/${candidateId}/decision`;
      const accepted = await read(page.request, decisionPath, reads);
      expect(accepted.decision.decision).toBe("accepted");
      expect(accepted.current_valid).toBe(true);
      expect(accepted.can_download).toBe(true);
      const downloaded = page.waitForEvent("download");
      await panel.getByRole("button", { name: "下载补丁", exact: true }).click();
      const download = await downloaded;
      const file = await download.path();
      expect(file).toBeTruthy();
      if (!file) throw new Error("download_file_missing");
      const bytes = await readFile(file);
      expect(bytes.equals(expected)).toBe(true);
      expect(sha(bytes)).toBe(sha(expected));
      const candidate = detail.candidates.find((c: { candidate_id: string }) => c.candidate_id === candidateId);
      expect(download.suggestedFilename()).toBe(`agent-repair-${candidate.sequence}-${candidateId.slice(0, 8)}.patch`);
      await download.saveAs(path.join(folder, `${caseId}.patch`));
      evidence.download = { sha256: sha(bytes), size: bytes.byteLength, exact_utf8_bytes: true, filename: download.suggestedFilename() };
      await panel.getByRole("button", { name: "拒绝候选", exact: true }).click();
      await expect(panel.getByText("已拒绝", { exact: true })).toBeVisible();
      await expect(panel.getByRole("button", { name: "下载补丁", exact: true })).toHaveCount(0);
      const rejected = await read(page.request, decisionPath, reads);
      expect(rejected.can_download).toBe(false);
      expect(rejected.decision.history.map((e: { decision: string }) => e.decision)).toEqual(["accepted", "rejected"]);
      const withdrawn = await page.request.get(`/api/repairs/${taskId}/candidates/${candidateId}/download`, { headers: { Origin: origin } });
      expect(withdrawn.status()).toBe(403);
      await page.reload();
      await expect(panel.getByText("已拒绝", { exact: true })).toBeVisible();
      expect(writes.filter(w => w.path === "/api/repair-runs")).toHaveLength(1);
      evidence.decision_history = rejected.decision.history;
      evidence.status = "passed";
      await page.screenshot({ path: path.join(folder, `${caseId}.png`), fullPage: true });
    } finally {
      evidence.writes = writes;
      evidence.read_http_statuses = reads;
      // Contains public structured evidence only, never provider responses or configuration.
      await writeFile(path.join(folder, `${caseId}.json`), JSON.stringify(evidence, null, 2), { mode: 0o600 });
    }
  });
}
