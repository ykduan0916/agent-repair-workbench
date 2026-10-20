/** B3: strict receipts, persisted intent and bounded requests. No automatic writes. */
import { z } from "zod";
import { memoryBindingSchema } from "./memory";
import { repairTaskSchema, repairSnapshotSchema } from "./repairs";
import { readJSON, RunApiError } from "./runs";
const uuid = z.string().uuid();
const cases = z.enum(["output_mismatch", "counter_offset"]);
const modes = z.enum(["single_role", "three_role"]);
export const pendingRepairSchema = z.strictObject({ request_id: uuid, case_id: cases, mode: modes, memory: memoryBindingSchema.optional() });
export type PendingRepair = z.infer<typeof pendingRepairSchema>;
export const REPAIR_PENDING_KEY = "awb-pending-repair-v1";
const { case_id: _case, sha256: _hash, recorded_outcome, ...fields } = repairTaskSchema.shape;
const rawTask = z.strictObject({ ...fields, schema_version: z.literal(1), outcome: recorded_outcome,
  baseline: repairSnapshotSchema.refine(v => v.kind === "baseline"), model_config_sha256: z.string().regex(/^[a-f0-9]{64}$/).nullable(),
}).refine(t => repairTaskSchema.safeParse({ ...Object.fromEntries(Object.keys(fields).map(k => [k, t[k as keyof typeof t]])),
  case_id: "output_mismatch", sha256: "0".repeat(64), recorded_outcome: t.outcome }).success);
export const receiptSchema = z.strictObject({ schema_version: z.literal(1), request_id: uuid, task_id: uuid,
  case_id: cases, mode: modes, task: rawTask, sha256: z.string().regex(/^[a-f0-9]{64}$/),
  cancel_requested: z.boolean(), active: z.boolean(), reused: z.boolean(),
}).refine(r => r.request_id === r.task_id && r.task_id === r.task.task_id && r.mode === r.task.mode && (r.active || r.task.phase === "finished"));
export type RepairReceipt = z.infer<typeof receiptSchema>;
export const catalogSchema = z.strictObject({ schema_version: z.literal(1),
  cases: z.array(z.strictObject({ id: cases, label: z.string().min(1).max(80) })).length(2),
  modes: z.array(z.strictObject({ id: modes, label: z.string().min(1).max(80) })).length(2),
  budget: repairTaskSchema.shape.budget, model: z.strictObject({ configured: z.boolean(), reason: z.enum(["model_not_configured", "model_configuration_invalid", "input_bound_unverified"]).nullable() }),
  image: z.strictObject({ prepared: z.boolean() }), slot: z.strictObject({ status: z.enum(["available", "busy", "recovery_required"]), task_id: uuid.nullable() }), accepting: z.boolean(),
}).refine(c => new Set(c.cases.map(v => v.id)).size === 2 && new Set(c.modes.map(v => v.id)).size === 2 && c.model.configured === (c.model.reason === null) && (c.slot.status !== "available" || c.slot.task_id === null));
export type RepairCatalog = z.infer<typeof catalogSchema>;
const event = z.strictObject({ revision: z.number().int().min(0).max(255), at: z.string().datetime({ offset: true }),
  kind: z.enum(["created", "reserved", "settled", "finished", "reconciled", "comparison_slot"]),
  role: z.enum(["single", "diagnosis", "repair", "verification"]).nullable(),
  operation: z.enum(["model", "tool", "candidate", "comparison", "reproduction"]).nullable(),
  tool: z.enum(["T01", "T02", "T03", "T04", "T05", "T06", "T07"]).nullable(),
  status: z.enum(["pending", "completed", "failed", "outcome_unknown"]).nullable(), error_code: repairTaskSchema.shape.error_code,
});
export const eventsSchema = z.strictObject({ schema_version: z.literal(1), task_id: uuid, revision: z.number().int().min(0).max(255), events: z.array(event).min(1).max(256) })
  .refine(v => v.events.length === v.revision + 1 && v.events.every((e, i) => e.revision === i && (!i || Date.parse(e.at) >= Date.parse(v.events[i - 1].at))));
export type RepairEvents = z.infer<typeof eventsSchema>;
const messages: Record<string, string> = {
  repair_busy: "已有修复占用执行槽，请查看当前任务。", repair_reads_busy: "读取正忙，请稍后查询。",
  recovery_required: "上次修复的资源尚未确认回收，需要先核对状态。", image_unavailable: "修复镜像不可用，请到环境页检查 Docker 和镜像。",
  model_not_configured: "本机服务尚未配置模型，请到环境页检查。", model_configuration_invalid: "模型配置无效，请到环境页检查。",
  input_bound_unverified: "模型预算上限尚未配置，请到环境页检查。", schema_incompatible: "记录库需要升级，请按本地运行指南迁移后重启。",
  service_stopping: "服务正在停止，请稍后查询原任务。", store_unavailable: "记录库暂时无法读取，请稍后查询。",
  web_repair_not_found: "服务端暂时没有这次提交的记录；不能据此确认请求从未送达。", request_gone: "接收记录仍在，但任务已不可读取；不会重新创建。",
  request_conflict: "同一请求的案例或模式不一致，已拒绝执行。", version_mismatch: "运行材料版本不一致，请检查本机环境。",
  invalid_request: "提交内容不符合接口要求，请检查页面与服务版本。", forbidden_origin: "请求来源未通过本机校验。", forbidden_host: "请求地址未通过本机校验。",
};
export class RepairApiError extends RunApiError { constructor(readonly code: string, status?: number) { super(messages[code] ?? "无法确认服务回应，请查询原任务。", status); } }
// Read errors as bounded safe codes rather than exposing upstream exception text.
async function write(path: string, body: object, signal: AbortSignal) {
  const response = await fetch(path, { method: "POST", body: JSON.stringify(body), headers: { "Content-Type": "application/json" }, cache: "no-store", signal: AbortSignal.any([signal, AbortSignal.timeout(15000)]) });
  if (!response.body || response.headers.get("content-type")?.split(";")[0] !== "application/json") { await response.body?.cancel(); throw new RepairApiError("invalid_response"); }
  const reader = response.body.getReader(); const chunks: Uint8Array[] = []; let size = 0;
  try { while (true) { const v = await reader.read(); if (v.done) break; size += v.value.byteLength; if (size > 32768) { await reader.cancel(); throw new RepairApiError("invalid_response"); } chunks.push(v.value); } }
  finally { reader.releaseLock(); }
  const bytes = new Uint8Array(size); let offset = 0; for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length; }
  let value: unknown;
  try { value = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes)); } catch { throw new RepairApiError("invalid_response"); }
  if (!response.ok) { const parsed = z.strictObject({ error: z.string().max(64) }).safeParse(value); throw new RepairApiError(parsed.success && parsed.data.error in messages ? parsed.data.error : "invalid_response", response.status); }
  return value;
}
function receipt(value: unknown, id: string, intent?: PendingRepair) {
  const parsed = receiptSchema.safeParse(value);
  if (!parsed.success || parsed.data.task_id !== id || (intent && (parsed.data.case_id !== intent.case_id || parsed.data.mode !== intent.mode))) throw new RepairApiError("invalid_response");
  return parsed.data;
}
export async function loadRepairCatalog(signal: AbortSignal) {
  const { value } = await readJSON("/api/repair-runs/catalog", signal);
  const result = catalogSchema.safeParse(value); if (!result.success) throw new RepairApiError("invalid_response"); return result.data;
}
export async function loadReceipt(id: string, signal: AbortSignal, intent?: PendingRepair) { const { value } = await readJSON(`/api/repair-runs/${uuid.parse(id)}`, signal); return receipt(value, id, intent); }
export async function startRepair(intent: PendingRepair, signal: AbortSignal) { return receipt(await write("/api/repair-runs", pendingRepairSchema.parse(intent), signal), intent.request_id, intent); }
export async function stopRepair(id: string, signal: AbortSignal) { return receipt(await write(`/api/repair-runs/${uuid.parse(id)}/cancel`, {}, signal), id); }
export async function loadRepairEvents(id: string, signal: AbortSignal) {
  const { value } = await readJSON(`/api/repair-runs/${uuid.parse(id)}/events`, signal);
  const parsed = eventsSchema.safeParse(value); if (!parsed.success || parsed.data.task_id !== id) throw new RepairApiError("invalid_response"); return parsed.data;
}

/** Late reads and write acknowledgements cannot rewind revision or durable flags. */
export function mergeReceipt(old: RepairReceipt | null, incoming: RepairReceipt): RepairReceipt {
  if (!old || old.task_id !== incoming.task_id) return incoming;
  const newer = incoming.task.revision >= old.task.revision ? incoming : old;
  return { ...newer, cancel_requested: old.cancel_requested || incoming.cancel_requested, active: old.active && incoming.active };
}
