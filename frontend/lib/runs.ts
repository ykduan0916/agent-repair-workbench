import { z } from "zod";

const uuid = z.string().regex(/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i);
const hash = z.string().regex(/^[a-f0-9]{64}$/);
const count = z.number().int().nonnegative().max(Number.MAX_SAFE_INTEGER);
const timestamp = z.number().finite().nonnegative().max(253402300799);
const scenario = z.enum(["wrong_type", "pass", "tool_error", "timeout", "response_lost", "duplicate_result", "crash_before_call", "crash_during_call", "crash_before_result", "stdout_flood", "stderr_flood", "stdout_total_flood", "pid_limit", "spawn_child", "oom", "cpu_spin", "policy_probe"]);
const state = z.enum(["queued", "running", "succeeded", "failed", "cancelled", "outcome_unknown"]);
export const runSchema = z.strictObject({
  run_id: uuid, attempt_id: uuid.nullable(), status: state, version: count.min(1),
  created_at: timestamp, updated_at: timestamp, lease_until: timestamp.nullable(),
  profile: z.literal("isolated-fixture"), scenario,
  config_sha256: hash, case_sha256: hash, report_sha256: hash.nullable(),
  executor_state: z.enum(["absent", "running", "stopped", "unavailable", "mismatch"]).nullable(),
});
const eventSchema = z.strictObject({
  event_id: uuid, version: count.min(1), kind: z.enum(["created", "claimed", "finished", "reconciled"]), created_at: timestamp,
});
export const runListSchema = z.strictObject({ runs: z.array(runSchema).max(50) }).refine(
  ({ runs }) => new Set(runs.map(run => run.run_id)).size === runs.length,
);
export const runDetailSchema = z.strictObject({ run: runSchema, events: z.array(eventSchema).max(100) }).refine(
  ({ run, events }) => new Set(events.map(event => event.event_id)).size === events.length
    && events.every((event, i) => event.version <= run.version && (i === 0 || event.version > events[i - 1].version)),
);
export type RunView = z.infer<typeof runSchema>;
export type RunDetail = z.infer<typeof runDetailSchema>;
export const runStatusLabels: Record<RunView["status"], string> = {
  queued: "排队中", running: "运行中", succeeded: "调用成功", failed: "调用失败", cancelled: "已停止", outcome_unknown: "结果未知",
};
export function activeStatus(status: RunView["status"]) { return status === "running" || status === "queued"; }
/** Wall-clock duration of a finished run; null while it is still active. */
export function runDurationMs(run: RunView) {
  return activeStatus(run.status) ? null : Math.max(0, (run.updated_at - run.created_at) * 1000);
}
export const scenarioLabels: Record<z.infer<typeof scenario>, string> = {
  wrong_type: "返回类型不符", pass: "正常调用", tool_error: "工具错误", timeout: "调用超时", response_lost: "响应丢失",
  duplicate_result: "重复结果", crash_before_call: "调用前崩溃", crash_during_call: "调用中崩溃",
  crash_before_result: "返回前崩溃", stdout_flood: "标准输出超限", stderr_flood: "错误输出超限",
  stdout_total_flood: "累计输出超限", pid_limit: "进程数限制", spawn_child: "子进程回收",
  oom: "内存超限", cpu_spin: "CPU 限制", policy_probe: "隔离策略探测",
};
export const eventLabels = { created: "已创建", claimed: "开始运行", finished: "运行结束", reconciled: "中断后已核对状态" };
export const executorLabels = { absent: "容器不存在", running: "容器运行中", stopped: "容器已停止", unavailable: "Docker 不可用", mismatch: "容器不属于此运行" };
const finding = z.strictObject({ code: z.string().max(128), phase: z.string().max(64), instance_path: z.string().max(8192), schema_path: z.string().max(8192) });
const checkStatus = z.enum(["passed", "failed", "not_checked", "skipped"]);
// Preserve metadata-only artifacts. Unknown fields are rejected, never silently downloaded.
export const sandboxReportSchema = z.strictObject({
  sandbox_report_version: z.literal(1), run_id: uuid, record_id: uuid,
  mcp: z.strictObject({
    mcp_report_version: z.literal(1), invocation_id: uuid,
    adapter_outcome: z.enum(["completed", "precheck_rejected", "unsupported", "tool_error", "transport_error", "protocol_error", "outcome_unknown"]),
    source: z.strictObject({
      kind: z.literal("mcp_sdk_normalized"), association: z.literal("sdk_dispatch"), wire_json_integrity: z.literal("not_checked"),
      sdk_version: z.string().max(64), adapter_version: z.literal("1"), protocol_version: z.literal("2025-11-25").nullable(),
      profile: z.enum(["contract-fixture", "public-sdk-3644", "isolated-fixture"]).nullable(),
      config_sha256: hash.nullable(), case_sha256: hash.nullable(), server_code_sha256: hash.nullable(),
      server_dependencies_sha256: hash.nullable(), tool_schema_sha256: hash.nullable(),
      server_revision: z.string().max(128).nullable(), source_tree_sha256: hash.nullable(),
    }),
    contract_report: z.strictObject({
      schema_version: z.literal(1), checker_version: z.string().max(64), evidence_sha256: hash.nullable(),
      status: z.enum(["passed", "failed", "inconclusive"]), checks: z.strictObject({ input: checkStatus, output: checkStatus }),
      findings: z.array(finding).max(50), findings_truncated: z.boolean(),
    }).nullable(),
    preflight_findings: z.array(finding).max(50),
    content_summary: z.array(z.strictObject({ kind: z.enum(["text", "image", "audio", "resource", "resource_link", "other"]), items: count, text_characters: count })).max(6),
    elapsed_ms: count,
  }),
  execution: z.strictObject({
    image_id: z.string().regex(/^sha256:[a-f0-9]{64}$/).nullable(), source_sha256: hash.nullable(), scenario: scenario.nullable(),
    seed: z.number().int().min(0).max(2147483647).nullable(), policy: z.literal("offline-readonly-v1"),
    stdout_bytes: count, stderr_bytes: count, frames: count, error: z.string().max(128).nullable(),
    cleanup: z.enum(["not_created", "removed", "unconfirmed"]), container_exit_code: z.number().int().min(-2147483648).max(2147483647).nullable(), oom_killed: z.boolean(),
  }),
});

export class RunApiError extends Error {
  constructor(message: string, readonly status?: number) { super(message); }
}
const LIMIT = 1024 * 1024;
const UNREADABLE = "本机服务返回的数据无法识别，已停止显示。通常是页面与服务版本不一致：请重启本机服务后刷新页面。";

export async function readJSON(url: string, signal: AbortSignal, init: { method: "POST"; body: string } | undefined = undefined, maxBytes = LIMIT): Promise<{ value: unknown; bytes: Uint8Array<ArrayBuffer> }> {
  const response = await fetch(url, { method: init?.method ?? "GET", ...(init ? { body: init.body, headers: { "Content-Type": "application/json" } } : {}), cache: "no-store", signal: AbortSignal.any([signal, AbortSignal.timeout(15000)]) });
  if (!response.ok) {
    let code = "";
    try {
      const raw = await response.text();
      const parsed = JSON.parse(raw) as { error?: unknown };
      if (typeof parsed.error === "string") code = parsed.error;
    } catch { /* keep the generic status message */ }
    const messages: Record<string, string> = {
      decision_conflict: "审批状态已经变化，请刷新当前记录后再决定。",
      request_conflict: "这次请求 ID 已绑定另一种决定，请刷新后重新操作。",
      version_mismatch: "候选或回归证据已变化，请刷新后重新核对。",
      decision_stale: "历史决定仍保留，但当前证据已失效，请刷新后重新核对。",
      approval_busy: "审批服务正在处理其他请求，请稍后重试。",
      forbidden_origin: "当前页面来源未获本机服务允许。",
    };
    const message = response.status === 404
      ? "记录或报告已不存在，请刷新列表。"
      : messages[code] ?? "本地记录服务暂时不可用，请稍后重试。";
    throw new RunApiError(message, response.status);
  }
  if (response.headers.get("content-type")?.split(";")[0].trim() !== "application/json" || !response.body) {
    await response.body?.cancel().catch(() => {});
    throw new RunApiError(UNREADABLE);
  }
  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let length = 0;
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      length += value.byteLength;
      if (length > maxBytes) { await reader.cancel(); throw new RunApiError(`返回数据超过 ${maxBytes / LIMIT} MiB，已停止读取。`); }
      chunks.push(value);
    }
  } finally { reader.releaseLock(); }
  const bytes = new Uint8Array(length);
  let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length; }
  try { return { value: JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes)), bytes }; }
  catch { throw new RunApiError(UNREADABLE); }
}

export async function loadRuns(signal: AbortSignal) {
  const { value } = await readJSON("/api/runs?limit=50", signal);
  const parsed = runListSchema.safeParse(value);
  if (!parsed.success) throw new RunApiError("运行列表不符合数据约定，已停止显示。通常是页面与服务版本不一致：请重启本机服务后刷新页面。");
  return parsed.data.runs;
}
export async function loadRun(id: string, signal: AbortSignal) {
  const { value } = await readJSON(`/api/runs/${uuid.parse(id)}`, signal);
  const parsed = runDetailSchema.safeParse(value);
  if (!parsed.success || parsed.data.run.run_id !== id) throw new RunApiError("运行详情不符合数据约定，已停止显示。通常是页面与服务版本不一致：请重启本机服务后刷新页面。");
  return parsed.data;
}
export type SandboxReport = z.infer<typeof sandboxReportSchema>;
export async function loadVerifiedReport(run: RunView, signal: AbortSignal) {
  const { value, bytes } = await readJSON(`/api/runs/${uuid.parse(run.run_id)}/report`, signal);
  const parsed = sandboxReportSchema.safeParse(value);
  const digest = Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)), byte => byte.toString(16).padStart(2, "0")).join("");
  if (!parsed.success || parsed.data.record_id !== run.run_id || parsed.data.run_id !== run.attempt_id || digest !== run.report_sha256) {
    throw new RunApiError("报告的结构、关联或摘要校验未通过，未显示也未下载。请刷新后重试；仍然失败，说明报告文件已被改动。");
  }
  return { report: parsed.data, bytes };
}
export async function loadReport(run: RunView, signal: AbortSignal) {
  return (await loadVerifiedReport(run, signal)).bytes;
}
export function runError(error: unknown) {
  return error instanceof RunApiError ? error.message : "连接不上本机服务。请确认服务已启动（端口 8030）后重试。";
}

/** Resolves after `ms`; while the tab is hidden it waits until the tab is visible again. */
export function pollDelay(ms: number, signal: AbortSignal) {
  return new Promise<void>((resolve) => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    const done = () => { cleanup(); resolve(); };
    const visible = () => { if (!document.hidden) done(); };
    const cleanup = () => { clearTimeout(timer); document.removeEventListener("visibilitychange", visible); signal.removeEventListener("abort", cleanup); };
    signal.addEventListener("abort", cleanup, { once: true });
    timer = setTimeout(() => { if (document.hidden) document.addEventListener("visibilitychange", visible); else done(); }, ms);
  });
}
