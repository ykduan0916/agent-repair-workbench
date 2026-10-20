import { z } from "zod";
import { memoryBindingSchema } from "./memory";
import { readJSON, RunApiError } from "./runs";
import { summarySchema } from "./analyses";
const uuid = z.string().regex(/^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/);
const cases = z.enum(["output_mismatch", "passing"]);
const intents = z.enum(["check_only", "diagnose", "propose_repair"]);
export const pendingAnalysisSchema = z.strictObject({ request_id: uuid, case_name: cases, intent: intents, memory: memoryBindingSchema.optional() });
export type PendingAnalysis = z.infer<typeof pendingAnalysisSchema>;
export const ANALYSIS_PENDING_KEY = "awb-pending-analysis-v1";
const catalogSchema = z.strictObject({
  schema_version: z.literal(1),
  cases: z.array(z.strictObject({ id: cases, label: z.string().min(1).max(80) })).length(2),
  intents: z.array(z.strictObject({ id: intents, label: z.string().min(1).max(80), requires_model: z.boolean() })).length(3),
  model: z.strictObject({ configured: z.boolean(), reason: z.enum(["MODEL_NOT_CONFIGURED", "MODEL_CONFIGURATION_INVALID", "INPUT_BOUND_UNVERIFIED"]).nullable() }),
  slot: z.strictObject({ status: z.enum(["available", "busy", "recovery_required"]), task_id: uuid.nullable() }),
}).refine(c => new Set(c.cases.map(v => v.id)).size === 2 && new Set(c.intents.map(v => v.id)).size === 3
  && c.intents.every(v => v.requires_model === (v.id !== "check_only"))
  && c.model.configured === (c.model.reason === null)
  && (c.slot.status === "available") === (c.slot.task_id === null));
export type AnalysisRunCatalog = z.infer<typeof catalogSchema>;
const acceptedSchema = z.strictObject({ task_id: uuid, summary: summarySchema, sha256: z.string().regex(/^[0-9a-f]{64}$/), reused: z.boolean(), cancel_requested: z.boolean() });
const cancelSchema = z.strictObject({ task_id: uuid, status: z.enum(["cancel_requested", "already_finished"]) });
export async function loadAnalysisCatalog(signal: AbortSignal) {
  const { value } = await readJSON("/api/analysis-runs/catalog", signal);
  const parsed = catalogSchema.safeParse(value);
  if (!parsed.success) throw new RunApiError("无法读取可选案例。请重启本机服务后刷新页面。");
  return parsed.data;
}
export async function startAnalysis(request: PendingAnalysis, signal: AbortSignal) {
  const { value } = await readJSON("/api/analysis-runs", signal, { method: "POST", body: JSON.stringify(pendingAnalysisSchema.parse(request)) });
  const parsed = acceptedSchema.safeParse(value);
  if (!parsed.success || parsed.data.task_id !== request.request_id || parsed.data.summary.task_id !== request.request_id || parsed.data.summary.state.intent !== request.intent) throw new RunApiError("服务的回应与本次提交对不上，无法确认分析是否已开始。请点「查询提交结果」。");
  return parsed.data;
}
export async function stopAnalysis(id: string, signal: AbortSignal) {
  const { value } = await readJSON(`/api/analysis-runs/${uuid.parse(id)}/cancel`, signal, { method: "POST", body: "{}" });
  const parsed = cancelSchema.safeParse(value);
  if (!parsed.success || parsed.data.task_id !== id) throw new RunApiError("无法确认是否已停止。请刷新查看状态。");
  return parsed.data;
}
export function analysisCommand(request: Pick<PendingAnalysis, "case_name" | "intent">) {
  return `uv run --frozen agent-workbench analyze --case ${cases.parse(request.case_name)} --intent ${intents.parse(request.intent)}`;
}

/** Fallback labels until the API catalog arrives; the catalog's own labels win once loaded. */
export const caseFallback: AnalysisRunCatalog["cases"] = [{ id: "output_mismatch", label: "返回值类型错误" }, { id: "passing", label: "正常调用" }];
export const intentFallback: AnalysisRunCatalog["intents"] = [{ id: "check_only", label: "仅检查契约", requires_model: false }, { id: "diagnose", label: "分析原因", requires_model: true }, { id: "propose_repair", label: "生成修复建议", requires_model: true }];
export const intentHints: Record<PendingAnalysis["intent"], string> = {
  check_only: "只检查契约，不调用模型。",
  diagnose: "诊断角色读取证据，必要时运行复现；验证角色核对结论。",
  propose_repair: "三个角色依次工作，输出修复建议，不修改代码。",
};
export const ANALYSIS_INIT_COMMAND = "uv run --frozen agent-workbench runs init";
export function reconcileCommand(id = "<分析 ID>") {
  return `uv run --frozen agent-workbench analyses reconcile ${id}`;
}
/** Runs `run` now, or as soon as the tab becomes visible again. Returns a canceller. */
export function whenVisible(run: () => void): () => void {
  if (typeof document === "undefined" || !document.hidden) { run(); return () => {}; }
  const listener = () => { if (document.hidden) return; document.removeEventListener("visibilitychange", listener); run(); };
  document.addEventListener("visibilitychange", listener);
  return () => document.removeEventListener("visibilitychange", listener);
}
