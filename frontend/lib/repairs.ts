/** Repair records: safe read contracts and labels. A historical outcome never substitutes current verification. */
import { z } from "zod";
import { readJSON, RunApiError } from "./runs";
const uuid = z.string().regex(/^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/);
const sha = z.string().regex(/^[0-9a-f]{64}$/);
const n = (max: number) => z.number().int().min(0).max(max);
const utc = z
  .string()
  .datetime({ offset: true })
  .refine((v) => /(?:Z|\+00:00)$/.test(v));
const errorCode = z
  .enum([
    "INVALID_INPUT",
    "PERMISSION_DENIED",
    "STALE_VERSION",
    "BUDGET_EXCEEDED",
    "RESOURCE_LIMIT",
    "TOOL_FAILURE",
    "RESULT_UNKNOWN",
    "ARTIFACT_MISMATCH",
    "INPUT_BOUND_UNVERIFIED",
    "MODEL_UNAVAILABLE",
    "MODEL_TIMEOUT",
    "MODEL_CANCELLED",
    "INVALID_MODEL_OUTPUT",
    "USAGE_UNKNOWN",
    "USAGE_INVALID",
    "OUTPUT_LIMIT",
    "CANCELLED",
    "PERSISTENCE_FAILED",
    "INTERRUPTED",
    "MODEL_CONFIGURATION_INVALID",
    "MODEL_NOT_CONFIGURED",
    "MODEL_TRANSPORT_ERROR",
    "MODEL_RESPONSE_INVALID",
    "MODEL_OUTPUT_TRUNCATED",
    "MODEL_USAGE_UNKNOWN",
    "MODEL_USAGE_BOUND_VIOLATION",
    "MODEL_DECISION_INVALID",
    "DEADLINE_EXCEEDED",
    "TOOL_FAILED",
    "TOOL_OUTCOME_UNKNOWN",
    "INTERNAL_ERROR",
  ])
  .nullable();
export const caseLabels = { output_mismatch: "返回类型错误", counter_offset: "计数值偏移" } as const;
export const outcomes = {
  failed: "失败",
  cancelled: "已取消",
  budget_exceeded: "预算用尽",
  outcome_unknown: "结果未知",
  inconclusive: "无法判定",
  candidate_verified: "回归通过",
};
const outcome = z.enum([
  "failed",
  "cancelled",
  "budget_exceeded",
  "outcome_unknown",
  "inconclusive",
  "candidate_verified",
]);
const budget = z.strictObject({
  total_tokens: n(2000000).min(1),
  output_tokens: n(32768).min(1),
  model_calls: n(12).min(1),
  tool_calls: n(20).min(1),
  candidates: n(2).min(1),
  timeout_seconds: n(600).min(1),
});
const usage = z.strictObject({
  model_calls: n(12),
  tool_calls: n(20),
  reserved_tokens: n(2000000),
  known_input_tokens: n(2000000),
  known_output_tokens: n(2000000),
  usage_complete: z.boolean(),
});
const task = z
  .strictObject({
    task_id: uuid,
    case_id: z.enum(["output_mismatch", "counter_offset"]),
    source_analysis_id: uuid.nullable(),
    mode: z.enum(["single_role", "three_role"]),
    phase: z.enum([
      "ready",
      "model_pending",
      "tool_pending",
      "candidate_preparing",
      "comparing",
      "finished",
    ]),
    recorded_outcome: outcome.nullable(),
    error_code: errorCode,
    revision: n(255),
    sha256: sha,
    started_at: utc,
    finished_at: utc.nullable(),
    budget,
    usage,
    candidate_count: n(2),
    cost_amount: z.null(),
  })
  .refine(
    (t) =>
      (t.phase === "finished") === (t.recorded_outcome !== null) &&
      (t.phase === "finished") === (t.finished_at !== null) &&
      (!t.finished_at ||
        Date.parse(t.finished_at) >= Date.parse(t.started_at)) &&
      t.usage.model_calls <= t.budget.model_calls &&
      t.usage.tool_calls <= t.budget.tool_calls &&
      t.candidate_count <= t.budget.candidates &&
      t.budget.output_tokens <= t.budget.total_tokens &&
      t.usage.known_input_tokens +
        t.usage.known_output_tokens +
        t.usage.reserved_tokens <=
        t.budget.total_tokens &&
      t.usage.usage_complete === (t.usage.reserved_tokens === 0),
  );
const candidate = z
  .strictObject({
    candidate_id: uuid,
    sequence: n(2).min(1),
    status: z.enum(["preparing", "committed", "failed", "outcome_unknown"]),
    snapshot_status: z.enum(["verified", "unavailable", "not_created"]),
    snapshot_sha256: sha.nullable(),
    error_code: errorCode,
    created_at: utc,
    finished_at: utc.nullable(),
  })
  .refine(
    (c) =>
      (c.status === "committed") === (c.snapshot_sha256 !== null) &&
      (c.status === "committed") === (c.snapshot_status !== "not_created") &&
      (c.status === "preparing") === (c.finished_at === null),
  );
export const steps = [
  "initialize",
  "schema_before",
  "valid_one",
  "invalid_type",
  "valid_zero",
  "valid_negative",
  "schema_after",
  "alive",
] as const;
export const stepLabels: Record<(typeof steps)[number], string> = {
  initialize: "协议初始化",
  schema_before: "调用前契约",
  valid_one: "首次合法调用",
  invalid_type: "拒绝非法类型",
  valid_zero: "后续合法调用",
  valid_negative: "边界输入调用",
  schema_after: "调用后契约",
  alive: "进程存活",
};
export const codeLabels = {
  not_run: "尚未执行",
  ok: "符合规则",
  protocol_mismatch: "协议不一致",
  schema_mismatch: "契约变化",
  tool_error: "工具返回错误",
  structured_missing: "缺少结构化结果",
  result_shape: "结果结构不一致",
  count_type: "计数类型不一致",
  count_value: "计数值不一致",
  invalid_input_accepted: "非法输入未被拒绝",
  wrong_error_code: "错误码不一致",
  process_exited: "进程已退出",
};
const step = z
  .strictObject({
    name: z.enum(steps),
    status: z.enum(["passed", "failed", "not_run"]),
    code: z.enum(
      Object.keys(codeLabels) as [
        keyof typeof codeLabels,
        ...Array<keyof typeof codeLabels>,
      ],
    ),
    result_sha256: sha.nullable(),
  })
  .refine(
    (s) =>
      s.status ===
      (s.code === "ok"
        ? "passed"
        : s.code === "not_run"
          ? "not_run"
          : "failed"),
  );
const slotNames = [
  "baseline_wrong_type",
  "baseline_pass",
  "candidate_wrong_type",
  "candidate_pass",
] as const;
const slot = z
  .strictObject({
    slot: z.enum(slotNames),
    status: z.enum([
      "passed",
      "failed",
      "inconclusive",
      "cancelled",
      "outcome_unknown",
    ]),
    cleanup: z.enum(["not_created", "removed", "unconfirmed"]),
    steps: z.array(step).length(8),
  })
  .refine(
    (s) =>
      s.steps.every((item, i) => item.name === steps[i]) &&
      (s.status !== "passed" ||
        (s.steps.every((item) => item.status === "passed") &&
          s.cleanup === "removed")),
  );
const comparisonOutcome = z.enum([
  "candidate_verified",
  "rejected",
  "inconclusive",
  "cancelled",
  "outcome_unknown",
]);
const comparison = z
  .strictObject({
    schema_version: z.literal(1),
    task_id: uuid,
    comparison_id: uuid,
    candidate_id: uuid,
    status: z.enum(["reserved", "finished", "outcome_unknown"]),
    verification: z.enum(["verified", "unavailable", "not_finished"]),
    recorded_outcome: comparisonOutcome.nullable(),
    current_outcome: comparisonOutcome.nullable(),
    reason: z
      .enum([
        "frozen_suite_passed",
        "candidate_failed",
        "baseline_not_reproduced",
        "execution_incomplete",
        "version_mismatch",
        "execution_cancelled",
        "cleanup_unconfirmed",
        "execution_unknown",
      ])
      .nullable(),
    unavailable_reason: z.literal("artifact_or_version_mismatch").nullable(),
    suite_sha256: sha,
    oracle_sha256: sha,
    created_at: utc,
    finished_at: utc.nullable(),
    slots: z.array(slot).max(4),
    accepted: z.literal(false),
  })
  .refine((c) => {
    if (
      !c.slots.every((s, i) => s.slot === slotNames[i]) ||
      (c.status === "reserved") !== (c.finished_at === null)
    )
      return false;
    if (c.verification === "unavailable")
      return (
        c.current_outcome === null &&
        c.reason === null &&
        c.slots.length === 0 &&
        c.unavailable_reason !== null
      );
    if (c.unavailable_reason !== null) return false;
    if (c.verification === "not_finished")
      return (
        c.status !== "finished" &&
        c.current_outcome === null &&
        c.reason === null
      );
    if (
      c.status !== "finished" ||
      c.current_outcome === null ||
      c.current_outcome !== c.recorded_outcome ||
      c.reason === null
    )
      return false;
    if (c.current_outcome !== "candidate_verified") return true;
    return (
      c.reason === "frozen_suite_passed" &&
      c.slots.length === 4 &&
      c.slots.every((s) => s.cleanup === "removed") &&
      c.slots[0].status === "failed" &&
      c.slots.slice(1).every((s) => s.status === "passed")
    );
  });
export const detailSchema = z
  .strictObject({
    schema_version: z.literal(2),
    task,
    candidates: z.array(candidate).max(2),
    comparisons: z.array(comparison).max(2),
  })
  .refine(
    (d) =>
      d.candidates.length === d.task.candidate_count &&
      d.candidates.every((c, i) => c.sequence === i + 1) &&
      new Set(d.candidates.map((c) => c.candidate_id)).size ===
        d.candidates.length &&
      new Set(d.comparisons.map((c) => c.comparison_id)).size ===
        d.comparisons.length &&
      new Set(d.comparisons.map((c) => c.candidate_id)).size ===
        d.comparisons.length &&
      d.comparisons.every(
        (c) =>
          c.task_id === d.task.task_id &&
          (c.current_outcome !== "candidate_verified" ||
            (c.slots.length === 4 &&
              c.slots[0].steps.every((s) =>
                ["valid_one", "valid_zero", "valid_negative"].includes(s.name)
                  ? s.code ===
                    (d.task.case_id === "output_mismatch"
                      ? "count_type"
                      : "count_value")
                  : s.code === "ok",
              ))) &&
          d.candidates.some(
            (item) =>
              item.candidate_id === c.candidate_id &&
              item.status === "committed" &&
              (c.verification !== "verified" ||
                item.snapshot_status === "verified"),
          ),
      ),
  );
const listSchema = z
  .strictObject({
    schema_version: z.literal(2),
    repairs: z.array(task).max(50),
  })
  .refine(
    (d) => new Set(d.repairs.map((t) => t.task_id)).size === d.repairs.length,
  );
const snapshot = z
  .strictObject({
    schema_version: z.literal(1),
    snapshot_id: uuid,
    kind: z.enum(["baseline", "candidate"]),
    file: z.literal("server.py"),
    manifest_sha256: sha,
    file_sha256: sha,
    tree_sha256: sha,
    baseline_id: uuid.nullable(),
    baseline_tree_sha256: sha.nullable(),
    patch_sha256: sha.nullable(),
  })
  .refine((s) =>
    s.kind === "baseline"
      ? s.baseline_id === null &&
        s.baseline_tree_sha256 === null &&
        s.patch_sha256 === null
      : s.baseline_id !== null &&
        s.baseline_id !== s.snapshot_id &&
        s.baseline_tree_sha256 !== null &&
        s.patch_sha256 !== null,
  );
const diffSchema = z
  .strictObject({
    schema_version: z.literal(1),
    task_id: uuid,
    candidate_id: uuid,
    verification: z.literal("verified"),
    diff: z.strictObject({
      schema_version: z.literal(1),
      baseline: snapshot,
      candidate: snapshot,
      path: z.literal("source/server.py"),
      unified_diff: z.string().max(65536),
    }),
  })
  .refine(
    (d) =>
      d.candidate_id === d.diff.candidate.snapshot_id &&
      d.diff.baseline.kind === "baseline" &&
      d.diff.candidate.kind === "candidate" &&
      d.diff.candidate.baseline_id === d.diff.baseline.snapshot_id &&
      d.diff.candidate.baseline_tree_sha256 === d.diff.baseline.tree_sha256 &&
      d.diff.candidate.manifest_sha256 === d.diff.baseline.manifest_sha256,
  );
export type RepairTask = z.infer<typeof task>;
export type RepairDetail = z.infer<typeof detailSchema>;
export type Comparison = z.infer<typeof comparison>;
export type Candidate = z.infer<typeof candidate>;
export type RepairDiff = z.infer<typeof diffSchema>;
const fail = () =>
  new RunApiError(
    "修复记录的格式或关联与页面不一致，未展示。请重启本机服务后刷新页面。",
  );
const diffMismatch = () =>
  new RunApiError(
    "代码差异与记录的候选快照不一致，未展示。请用 CLI 重新运行修复命令后刷新。",
  );
// Only idempotent record reads retry a busy reader. Writes never enter this path.
async function readRepairJSON(url: string, signal: AbortSignal) {
  for (let attempt = 0; ; attempt++) {
    signal.throwIfAborted();
    try { return await readJSON(url, signal, undefined, 524288); }
    catch (error) {
      if (!(error instanceof RunApiError) || error.status !== 429 || attempt >= 3) throw error;
      await new Promise<void>((resolve, reject) => {
        const abort = () => { clearTimeout(timer); reject(signal.reason); };
        const timer = setTimeout(() => { signal.removeEventListener("abort", abort); resolve(); }, 250 * 2 ** attempt);
        signal.addEventListener("abort", abort, { once: true });
        if (signal.aborted) { signal.removeEventListener("abort", abort); abort(); }
      });
    }
  }
}
export async function loadRepairs(signal: AbortSignal) {
  const { value } = await readRepairJSON("/api/repairs?limit=50", signal);
  const p = listSchema.safeParse(value);
  if (!p.success) throw fail();
  return p.data.repairs;
}
export async function loadRepair(id: string, signal: AbortSignal) {
  const { value } = await readRepairJSON(`/api/repairs/${uuid.parse(id)}`, signal);
  const p = detailSchema.safeParse(value);
  if (!p.success || p.data.task.task_id !== id) throw fail();
  return p.data;
}
export async function loadDiff(
  taskId: string,
  item: Candidate,
  signal: AbortSignal,
) {
  const { value } = await readRepairJSON(
    `/api/repairs/${uuid.parse(taskId)}/candidates/${uuid.parse(item.candidate_id)}/diff`, signal,
  );
  const p = diffSchema.safeParse(value);
  if (!p.success) throw fail();
  if (
    p.data.task_id !== taskId ||
    p.data.candidate_id !== item.candidate_id ||
    p.data.diff.candidate.tree_sha256 !== item.snapshot_sha256
  )
    throw diffMismatch();
  return p.data;
}

/* ---------- labels used by the repairs page ---------- */
export const REPAIR_COMMAND =
  "uv run --frozen agent-workbench repair --mode three_role";
export const REPAIR_DOCS = "/docs/27-local-development-guide";
export const modeLabels = { single_role: "单角色", three_role: "三角色" };
export const comparisonOutcomes = {
  candidate_verified: "回归通过",
  rejected: "候选未通过",
  inconclusive: "无法判定",
  cancelled: "已取消",
  outcome_unknown: "结果未知",
};
/** One plain sentence per recorded comparison reason. */
export const reasonText = {
  frozen_suite_passed: "候选通过冻结规则：原错误已修复，正常路径无回归。",
  candidate_failed: "候选在至少一项检查中失败，详见「回归结果」。",
  baseline_not_reproduced: "基线没有复现原错误，无法确认候选修复了它。",
  execution_incomplete: "有场景没有执行完，结果不完整。",
  version_mismatch: "代码或检查项在记录后有改动，结果不再有效。",
  execution_cancelled: "回归在完成前被取消。",
  cleanup_unconfirmed: "容器清理未确认，结果不计为通过。",
  execution_unknown: "执行结果未知，按未通过处理。",
};
export const slotStatusLabels = {
  passed: "通过",
  failed: "不通过",
  inconclusive: "无法判定",
  cancelled: "已取消",
  outcome_unknown: "结果未知",
};
export const cleanupLabels = {
  removed: "已清理",
  unconfirmed: "未确认",
  not_created: "未创建",
};
export const candidateStatusLabels = {
  preparing: "生成中",
  committed: "已生成",
  failed: "生成失败",
  outcome_unknown: "结果未知",
};
export const phaseLabels = {
  ready: "等待开始",
  model_pending: "等待模型响应",
  tool_pending: "等待工具结果",
  candidate_preparing: "正在生成候选",
  comparing: "正在运行回归",
  finished: "已结束",
};
type ErrorCode = NonNullable<z.infer<typeof errorCode>>;
export const errorLabels: Record<ErrorCode, string> = {
  INVALID_INPUT: "输入不合法",
  PERMISSION_DENIED: "权限不足",
  STALE_VERSION: "记录版本已过期",
  BUDGET_EXCEEDED: "超出预算",
  RESOURCE_LIMIT: "超出资源限制",
  TOOL_FAILURE: "工具执行失败",
  RESULT_UNKNOWN: "结果未知",
  ARTIFACT_MISMATCH: "文件与记录不一致",
  INPUT_BOUND_UNVERIFIED: "输入范围无法核对",
  MODEL_UNAVAILABLE: "模型不可用",
  MODEL_TIMEOUT: "模型响应超时",
  MODEL_CANCELLED: "模型调用已取消",
  INVALID_MODEL_OUTPUT: "模型输出不合法",
  USAGE_UNKNOWN: "用量待确认",
  USAGE_INVALID: "用量数据不合法",
  OUTPUT_LIMIT: "输出超出上限",
  CANCELLED: "已取消",
  PERSISTENCE_FAILED: "记录保存失败",
  INTERRUPTED: "执行被中断",
  MODEL_CONFIGURATION_INVALID: "模型配置无效",
  MODEL_NOT_CONFIGURED: "模型未配置",
  MODEL_TRANSPORT_ERROR: "无法连接模型服务",
  MODEL_RESPONSE_INVALID: "模型响应不合法",
  MODEL_OUTPUT_TRUNCATED: "模型输出被截断",
  MODEL_USAGE_UNKNOWN: "模型用量待确认",
  MODEL_USAGE_BOUND_VIOLATION: "模型用量超出上限",
  MODEL_DECISION_INVALID: "模型决策不合法",
  DEADLINE_EXCEEDED: "超出时限",
  TOOL_FAILED: "工具执行失败",
  TOOL_OUTCOME_UNKNOWN: "工具结果未知",
  INTERNAL_ERROR: "内部错误",
};

// Shared validators for the B3 execution receipt; read-only verdicts remain separate.
export { task as repairTaskSchema, snapshot as repairSnapshotSchema };
