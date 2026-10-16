import { z } from "zod";
import { readJSON, RunApiError } from "./runs";

const uuid = z.string().regex(/^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/);
const sha = z.string().regex(/^[0-9a-f]{64}$/);
const utc = z.string().datetime({ offset: true });
const event = z.strictObject({ candidate_id: uuid, decision: z.enum(["accepted", "rejected"]), revision: z.number().int().min(1).max(255), request_id: uuid, created_at: utc });
const decision = z.strictObject({
  schema_version: z.literal(1), decision_id: uuid, task_id: uuid, candidate_id: uuid, request_id: uuid,
  decision: z.enum(["accepted", "rejected"]), revision: z.number().int().min(1).max(255),
  candidate_sha256: sha, comparison_id: uuid, comparison_sha256: sha, created_at: utc, history: z.array(event).min(1).max(8),
});
export const decisionReceiptSchema = z.strictObject({
  schema_version: z.literal(1), task_id: uuid, candidate_id: uuid, revision: z.number().int().min(0).max(255),
  current_valid: z.boolean(), can_accept: z.boolean(), can_reject: z.boolean(), can_download: z.boolean(),
  decision: decision.nullable(), candidate_sha256: sha.nullable(), comparison_sha256: sha.nullable(),
}).superRefine((value, context) => {
  if (value.decision && (value.decision.task_id !== value.task_id || value.decision.candidate_id !== value.candidate_id)) {
    context.addIssue({ code: "custom", path: ["decision"], message: "decision_binding_mismatch" });
  }
  if (value.decision && (value.decision.candidate_sha256 !== value.candidate_sha256 || value.decision.comparison_sha256 !== value.comparison_sha256)) {
    context.addIssue({ code: "custom", path: ["decision"], message: "decision_version_mismatch" });
  }
  if (value.can_download && (!value.current_valid || value.decision?.decision !== "accepted")) {
    context.addIssue({ code: "custom", path: ["can_download"], message: "invalid_download_permission" });
  }
  if (value.decision && value.decision.revision > value.revision) {
    context.addIssue({ code: "custom", path: ["revision"], message: "decision_revision_ahead" });
  }
});
export type DecisionReceipt = z.infer<typeof decisionReceiptSchema>;
export type DecisionChoice = "accepted" | "rejected";

export type PendingDecision = {
  request_id: string;
  decision: DecisionChoice;
  expected_revision: number;
  expected_candidate_sha256: string;
  expected_comparison_sha256: string;
};

const pendingKey = (taskId: string, candidateId: string) => `awb-decision-pending-v1:${taskId}:${candidateId}`;

export function loadPendingDecision(taskId: string, candidateId: string): PendingDecision | null {
  try {
    const raw = sessionStorage.getItem(pendingKey(taskId, candidateId));
    if (!raw) return null;
    const value = JSON.parse(raw) as unknown;
    const parsed = z.strictObject({ request_id: uuid, decision: z.enum(["accepted", "rejected"]), expected_revision: z.number().int().min(0).max(255), expected_candidate_sha256: sha, expected_comparison_sha256: sha }).safeParse(value);
    return parsed.success ? parsed.data : null;
  } catch { return null; }
}

export function savePendingDecision(taskId: string, candidateId: string, value: PendingDecision): boolean {
  try { sessionStorage.setItem(pendingKey(taskId, candidateId), JSON.stringify(value)); return true; } catch { return false; }
}

export function clearPendingDecision(taskId: string, candidateId: string) {
  try { sessionStorage.removeItem(pendingKey(taskId, candidateId)); } catch { /* storage is optional */ }
}

export async function loadDecision(taskId: string, candidateId: string, signal: AbortSignal): Promise<DecisionReceipt> {
  const { value } = await readJSON(`/api/repairs/${taskId}/candidates/${candidateId}/decision`, signal);
  const parsed = decisionReceiptSchema.safeParse(value);
  if (!parsed.success) throw new RunApiError("审批记录不符合数据约定，已停止显示。请重启本机服务后刷新页面。");
  return parsed.data;
}

export async function saveDecision(taskId: string, candidateId: string, payload: {
  request_id: string; decision: DecisionChoice; expected_revision: number;
  expected_candidate_sha256: string; expected_comparison_sha256: string;
}, signal: AbortSignal): Promise<DecisionReceipt> {
  const { value } = await readJSON(`/api/repairs/${taskId}/candidates/${candidateId}/decision`, signal, { method: "POST", body: JSON.stringify(payload) });
  const parsed = decisionReceiptSchema.safeParse(value);
  if (!parsed.success) throw new RunApiError("审批服务返回的数据无法识别，已停止显示。");
  return parsed.data;
}

export async function downloadDecision(taskId: string, candidateId: string, signal: AbortSignal): Promise<void> {
  const response = await fetch(`/api/repairs/${taskId}/candidates/${candidateId}/download`, { cache: "no-store", signal: AbortSignal.any([signal, AbortSignal.timeout(15000)]) });
  if (!response.ok) { await response.body?.cancel().catch(() => {}); throw new RunApiError(response.status === 409 ? "候选版本已变化，下载前需要重新核对。" : "补丁下载暂不可用，请先确认已接受当前候选。", response.status); }
  if (!response.headers.get("content-type")?.startsWith("text/x-diff") || !response.body) throw new RunApiError("下载内容无法识别，已停止保存。");
  if (response.headers.get("x-task-id") !== taskId || response.headers.get("x-candidate-id") !== candidateId) {
    await response.body.cancel().catch(() => {});
    throw new RunApiError("下载内容与当前候选不匹配，已停止保存。");
  }
  const length = Number(response.headers.get("content-length") ?? "0");
  const maxBytes = 1024 * 1024;
  if (Number.isFinite(length) && length > maxBytes) {
    await response.body.cancel().catch(() => {});
    throw new RunApiError("补丁超过 1 MiB 限制，已停止保存。");
  }
  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  try {
    while (true) {
      const part = await reader.read();
      if (part.done) break;
      total += part.value.byteLength;
      if (total > maxBytes) {
        await reader.cancel();
        throw new RunApiError("补丁超过 1 MiB 限制，已停止保存。");
      }
      chunks.push(part.value);
    }
  } finally {
    reader.releaseLock();
  }
  const bytes = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
  const patchSha = Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)), byte => byte.toString(16).padStart(2, "0")).join("");
  if (response.headers.get("x-patch-sha256") !== patchSha) {
    throw new RunApiError("补丁摘要与服务端记录不一致，已停止保存。");
  }
  const blob = new Blob([bytes], { type: "text/x-diff" });
  const href = URL.createObjectURL(blob);
  const filename = response.headers.get("content-disposition")?.match(/filename="(agent-repair-[12]-[0-9a-f]{8}\.patch)"/)?.[1] ?? "agent-repair.patch";
  const anchor = document.createElement("a"); anchor.href = href; anchor.download = filename; anchor.click();
  setTimeout(() => URL.revokeObjectURL(href), 0);
}
