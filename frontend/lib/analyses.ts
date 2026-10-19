/** Analysis records: metadata only. Unknown fields and impossible summaries are rejected. */
import { z } from "zod";
import { readJSON, RunApiError } from "./runs";
import { formatNumber } from "./format";
import { roleIds, toolIds } from "./agent-contracts";
const uuid = z.string().regex(/^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/);
const sha = z.string().regex(/^[0-9a-f]{64}$/);
const integer = (max: number) => z.number().int().min(0).max(max);
const utc = z.string().datetime({ offset: true }).refine(value => /(?:Z|\+00:00)$/.test(value));
const ref = z.strictObject({ evidence_id: uuid, sha256: sha });
const refs = z.array(ref).max(32).refine(items => new Set(items.map(item => item.evidence_id)).size === items.length);
// UI-08 copy: plain labels, one term per concept (docs/32 §4).
export const phaseLabels = { queued: "等待开始", checking: "检查契约", diagnosing: "诊断中", proposing: "生成建议", reviewing: "验证中", finished: "已结束" };
export const outcomeLabels = { check_complete: "检查完成", diagnosis_complete: "诊断完成", proposal_only: "已提出建议", inconclusive: "证据不足", cancelled: "已停止", failed: "失败", budget_exceeded: "超出预算", outcome_unknown: "结果未知" };
export const intentLabels = { check_only: "检查契约", diagnose: "分析原因", propose_repair: "生成修复建议" };
export const roleLabels = { diagnosis: "诊断", repair: "修复", verification: "验证" };
export const toolLabels = { read_evidence: "读取证据", read_contract: "读取契约", read_source: "读取源码", check_contract: "检查契约", request_reproduction: "运行复现" };
export const assessmentLabels = { hypothesis: "待验证假设", inconclusive: "证据不足", proposal_only: "已提出建议", cannot_propose: "无法提出建议", needs_evidence: "需要运行验证", proposal_reviewed: "证据已核对" };
export const reasonLabels = { evidence_missing: "缺少证据", contract_mismatch: "契约不一致", serialization_mismatch: "序列化不一致", unsupported: "当前范围不支持", proposal_requires_execution: "建议需要实际运行验证" };
export const eventLabels = { created: "分析已创建", phase_changed: "进入下一阶段", model_started: "调用模型", model_settled: "模型已返回", model_unknown: "模型未返回用量", tool_started: "调用工具", tool_completed: "工具已返回", role_concluded: "提交结论", finished: "分析已结束", reconciled: "中断后已核对状态" };
const errors = ["INVALID_INPUT", "PERMISSION_DENIED", "STALE_VERSION", "BUDGET_EXCEEDED", "RESOURCE_LIMIT", "TOOL_FAILURE", "RESULT_UNKNOWN", "ARTIFACT_MISMATCH", "INPUT_BOUND_UNVERIFIED", "MODEL_UNAVAILABLE", "MODEL_TIMEOUT", "MODEL_CANCELLED", "INVALID_MODEL_OUTPUT", "USAGE_UNKNOWN", "USAGE_INVALID", "OUTPUT_LIMIT", "CANCELLED", "PERSISTENCE_FAILED", "INTERRUPTED", "MODEL_CONFIGURATION_INVALID", "MODEL_NOT_CONFIGURED", "MODEL_TRANSPORT_ERROR", "MODEL_RESPONSE_INVALID", "MODEL_OUTPUT_TRUNCATED", "MODEL_USAGE_UNKNOWN", "MODEL_USAGE_BOUND_VIOLATION", "MODEL_DECISION_INVALID", "MODEL_DECISION_JSON_INVALID", "MODEL_ROLE_SCHEMA_INVALID", "MODEL_DECISION_LIMIT", "MODEL_EVIDENCE_INVALID", "MODEL_HANDOFF_INVALID", "MODEL_EVIDENCE_REQUIRED", "DEADLINE_EXCEEDED", "TOOL_FAILED", "TOOL_OUTCOME_UNKNOWN", "INTERNAL_ERROR"] as const;
export type AnalysisErrorCode = typeof errors[number];
export const errorLabels: Record<AnalysisErrorCode, string> = {
  MODEL_DECISION_JSON_INVALID: "模型决策 JSON 格式错误",
  MODEL_ROLE_SCHEMA_INVALID: "模型输出不符合当前角色格式",
  MODEL_DECISION_LIMIT: "模型决策超出大小或嵌套限制",
  MODEL_EVIDENCE_INVALID: "模型引用的证据无法核对",
  MODEL_HANDOFF_INVALID: "当前执行状态不允许角色交接",
  MODEL_EVIDENCE_REQUIRED: "模型缺少继续执行所需的证据",
  INVALID_INPUT: "输入无效", PERMISSION_DENIED: "没有权限", STALE_VERSION: "记录版本已过期", BUDGET_EXCEEDED: "超出预算", RESOURCE_LIMIT: "超出资源限制",
  TOOL_FAILURE: "工具执行失败", RESULT_UNKNOWN: "结果未知", ARTIFACT_MISMATCH: "产物校验不一致", INPUT_BOUND_UNVERIFIED: "模型输入上限未确认", MODEL_UNAVAILABLE: "模型不可用",
  MODEL_TIMEOUT: "模型响应超时", MODEL_CANCELLED: "模型调用已停止", INVALID_MODEL_OUTPUT: "模型输出无效", USAGE_UNKNOWN: "用量未知", USAGE_INVALID: "用量数据无效",
  OUTPUT_LIMIT: "输出超出上限", CANCELLED: "已停止", PERSISTENCE_FAILED: "记录保存失败", INTERRUPTED: "执行中断", MODEL_CONFIGURATION_INVALID: "模型配置无效",
  MODEL_NOT_CONFIGURED: "未配置模型", MODEL_TRANSPORT_ERROR: "无法连接模型", MODEL_RESPONSE_INVALID: "模型响应无效", MODEL_OUTPUT_TRUNCATED: "模型输出被截断", MODEL_USAGE_UNKNOWN: "模型未返回用量",
  MODEL_USAGE_BOUND_VIOLATION: "模型用量超出预留", MODEL_DECISION_INVALID: "模型决策无效", DEADLINE_EXCEEDED: "超过时间上限", TOOL_FAILED: "工具执行失败", TOOL_OUTCOME_UNKNOWN: "工具结果未知", INTERNAL_ERROR: "内部错误",
};
export const summarySchema = z.strictObject({
  schema_version: z.literal(1), task_id: uuid, summary_revision: integer(255), source_kind: z.enum(["synthetic", "public_historical"]),
  state: z.strictObject({ task_id: uuid, intent: z.enum(Object.keys(intentLabels) as [keyof typeof intentLabels, ...Array<keyof typeof intentLabels>]), phase: z.enum(Object.keys(phaseLabels) as [keyof typeof phaseLabels, ...Array<keyof typeof phaseLabels>]), revision: integer(1000000), outcome: z.enum(Object.keys(outcomeLabels) as [keyof typeof outcomeLabels, ...Array<keyof typeof outcomeLabels>]).nullable() }),
  started_at: utc, finished_at: utc.nullable(),
  budget: z.strictObject({ total_tokens: integer(2000000).min(1), output_tokens: integer(32768).min(1), model_calls: integer(12).min(1), tool_calls: integer(20).min(1), candidates: integer(2).min(1), timeout_seconds: integer(600).min(1) }),
  usage: z.strictObject({ model_calls: integer(12), tool_calls: integer(20), reserved_tokens: integer(2000000), known_input_tokens: integer(2000000), known_output_tokens: integer(2000000), usage_complete: z.boolean() }),
  evidence_refs: refs, execution_record_ids: z.array(uuid).max(20), proposal_count: integer(2), model_config_sha256: sha.nullable(), cost_amount: z.null(),
}).refine(s => s.task_id === s.state.task_id && s.summary_revision >= s.state.revision
  && (s.state.phase === "finished") === (s.state.outcome !== null) && (s.state.phase === "finished") === (s.finished_at !== null)
  && (!s.finished_at || Date.parse(s.finished_at) >= Date.parse(s.started_at))
  && s.budget.output_tokens <= s.budget.total_tokens && s.usage.model_calls <= s.budget.model_calls && s.usage.tool_calls <= s.budget.tool_calls
  && s.proposal_count <= s.budget.candidates && s.usage.known_input_tokens + s.usage.known_output_tokens + s.usage.reserved_tokens <= s.budget.total_tokens
  && s.usage.usage_complete === (s.usage.reserved_tokens === 0) && Boolean(s.usage.model_calls) === (s.model_config_sha256 !== null)
  && (s.usage.model_calls > 0 || s.usage.known_input_tokens + s.usage.known_output_tokens + s.usage.reserved_tokens === 0)
  && new Set(s.execution_record_ids).size === s.execution_record_ids.length
  && (s.state.phase === "queued" ? s.state.revision === 0 && s.summary_revision === 0 : s.state.revision > 0)
  && (s.state.intent !== "check_only" || ["queued", "checking", "finished"].includes(s.state.phase))
  && (s.state.intent !== "diagnose" || s.state.phase !== "proposing")
  && (s.state.outcome !== "check_complete" || s.state.intent === "check_only")
  && (s.state.outcome !== "diagnosis_complete" || s.state.intent === "diagnose")
  && (s.state.outcome !== "proposal_only" || s.state.intent === "propose_repair"));
const eventSchema = z.strictObject({
  kind: z.enum(Object.keys(eventLabels) as [keyof typeof eventLabels, ...Array<keyof typeof eventLabels>]), role: z.enum(roleIds).nullable(),
  assessment: z.enum(Object.keys(assessmentLabels) as [keyof typeof assessmentLabels, ...Array<keyof typeof assessmentLabels>]).nullable(),
  reason_code: z.enum(Object.keys(reasonLabels) as [keyof typeof reasonLabels, ...Array<keyof typeof reasonLabels>]).nullable(), tool: z.enum(toolIds).nullable(), evidence_refs: refs,
  call_id: uuid.nullable(), request_sha256: sha.nullable(), input_reserved: integer(2000000).nullable(), output_reserved: integer(32768).min(1).nullable(), reservation_sha256: sha.nullable(), known_input_tokens: integer(2000000).nullable(), known_output_tokens: integer(2000000).nullable(), error_code: z.enum(errors).nullable(),
}).refine(e => {
  const model = e.kind.startsWith("model_"); const tool = e.kind.startsWith("tool_");
  if (model && [e.role, e.call_id, e.request_sha256, e.input_reserved, e.output_reserved, e.reservation_sha256].some(v => v === null)) return false;
  if (!model && [e.input_reserved, e.output_reserved, e.reservation_sha256, e.known_input_tokens, e.known_output_tokens].some(v => v !== null)) return false;
  if (e.kind === "model_settled" ? e.known_input_tokens === null || e.known_output_tokens === null : e.known_input_tokens !== null || e.known_output_tokens !== null) return false;
  if (tool ? [e.role, e.tool, e.call_id, e.request_sha256].some(v => v === null) : e.tool !== null) return false;
  if (!model && !tool && (e.call_id !== null || e.request_sha256 !== null)) return false;
  if (e.kind !== "role_concluded") return e.assessment === null && e.reason_code === null;
  const allowed = { diagnosis: ["hypothesis", "inconclusive"], repair: ["proposal_only", "cannot_propose"], verification: ["needs_evidence", "proposal_reviewed"] };
  return e.role !== null && e.assessment !== null && allowed[e.role].includes(e.assessment) && (!["hypothesis", "proposal_only", "proposal_reviewed"].includes(e.assessment) || e.evidence_refs.length > 0);
});
const savedSchema = z.strictObject({ summary: summarySchema, sha256: sha });
const eventView = z.strictObject({ revision: integer(255), created_at: z.number().finite().min(0).max(253402300799), sha256: sha, event: eventSchema });
const detailSchema = z.strictObject({ summary: summarySchema, sha256: sha, events: z.array(eventView).min(1).max(256) }).refine(d => d.events.length === d.summary.summary_revision + 1 && d.events.every((e, i) => e.revision === i && e.event.evidence_refs.every(ref => d.summary.evidence_refs.some(known => known.evidence_id === ref.evidence_id && known.sha256 === ref.sha256))));
const listSchema = z.strictObject({ analyses: z.array(savedSchema).max(50) }).refine(d => new Set(d.analyses.map(v => v.summary.task_id)).size === d.analyses.length);
export type AnalysisSummary = z.infer<typeof summarySchema>;
export type AnalysisDetail = z.infer<typeof detailSchema>;
export type SavedAnalysis = z.infer<typeof savedSchema>;
export type AnalysisEvent = AnalysisDetail["events"][number]["event"];
export function analysisStatus(summary: AnalysisSummary) { return summary.state.outcome ? outcomeLabels[summary.state.outcome] : phaseLabels[summary.state.phase]; }
export const sourceLabels = { synthetic: "内置示例", public_historical: "公开案例" };
/** One plain-language line per ledger event; error codes keep their code in parentheses. */
export function describeEvent(e: AnalysisEvent): { text: string; error: boolean } {
  let text: string;
  if (e.kind === "model_started") text = `调用模型，预留输入 ${formatNumber(e.input_reserved ?? 0)} / 输出 ${formatNumber(e.output_reserved ?? 0)} token`;
  else if (e.kind === "model_settled") text = `模型已返回，输入 ${formatNumber(e.known_input_tokens ?? 0)} / 输出 ${formatNumber(e.known_output_tokens ?? 0)} token`;
  else if (e.kind === "model_unknown") text = "模型未返回用量，按预留额度计入预算";
  else if (e.kind === "tool_started" && e.tool) text = `调用工具：${toolLabels[e.tool]}`;
  else if (e.kind === "tool_completed" && e.tool) text = `工具已返回：${toolLabels[e.tool]}`;
  else if (e.kind === "role_concluded" && e.assessment) text = `提交结论：${assessmentLabels[e.assessment]}${e.reason_code ? `，${reasonLabels[e.reason_code]}` : ""}`;
  else text = eventLabels[e.kind];
  if (e.error_code) text += ["MODEL_USAGE_UNKNOWN", "USAGE_UNKNOWN"].includes(e.error_code) && e.kind === "model_unknown" ? `（${e.error_code}）` : ` · ${errorLabels[e.error_code]}（${e.error_code}）`;
  return { text, error: e.error_code !== null };
}
// The server checks canonical stored bytes, event ledger and links before returning metadata.
export async function loadAnalyses(signal: AbortSignal) {
  const { value } = await readJSON("/api/analyses?limit=50", signal, undefined, 16 * 1024 * 1024);
  const parsed = listSchema.safeParse(value);
  if (!parsed.success) throw new RunApiError("分析列表不符合数据约定，未展示。请确认本机服务与网页是同一版本后刷新。");
  return parsed.data.analyses;
}
export async function loadAnalysis(id: string, signal: AbortSignal) {
  const { value } = await readJSON(`/api/analyses/${uuid.parse(id)}`, signal, undefined, 8 * 1024 * 1024);
  const parsed = detailSchema.safeParse(value);
  if (!parsed.success || parsed.data.summary.task_id !== id) throw new RunApiError("分析详情不符合数据约定，未展示。请确认本机服务与网页是同一版本后刷新。");
  return parsed.data;
}
