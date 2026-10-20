"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { z } from "zod";
import { type DecisionReceipt } from "../../lib/decisions";
import { key, memoryRequest, safeMessage, MemoryApiError, type MemoryBinding } from "../../lib/memory";
import { BindingPicker } from "./task-memory-choice";

const hash = z.string().regex(/^[a-f0-9]{64}$/);
const requestSchema = z.strictObject({ request_id: key, project_id: key, connection_id: key, expected_decision_revision: z.number().int().nonnegative(), expected_candidate_sha256: hash, expected_comparison_sha256: hash });
const proofSchema = z.object({ project_id: key, memory_id: key, state: z.enum(["draft", "confirmed", "revoked", "deleted"]), source_current_valid: z.boolean() });

export function SaveRepairMemory({ taskId, candidateId, receipt }: { taskId: string; candidateId: string; receipt: DecisionReceipt }) {
  const [open, setOpen] = useState(false); const [binding, setBinding] = useState<MemoryBinding>(); const [pending, setPending] = useState<z.infer<typeof requestSchema>>();
  const [proof, setProof] = useState<z.infer<typeof proofSchema>>(); const [error, setError] = useState(""); const [busy, setBusy] = useState(false); const [ready, setReady] = useState(false);
  const storage = `awb-memory-draft:${taskId}:${candidateId}`;
  const path = `/api/repairs/${encodeURIComponent(taskId)}/candidates/${encodeURIComponent(candidateId)}/memory-draft`;
  useEffect(() => { try { const raw = sessionStorage.getItem(storage); if (raw) { if (raw.length > 2048) throw new Error(); const saved = requestSchema.parse(JSON.parse(raw)); setPending(saved); setBinding({ project_id: saved.project_id, connection_id: saved.connection_id, allow_historical: false }); setOpen(true); } setReady(true); } catch { setError("无法核对本机保存请求，已暂停提交。请恢复会话存储后刷新。"); } }, [storage]);
  async function recover() {
    const project = pending?.project_id ?? binding?.project_id; if (!project || busy) return;
    setBusy(true); setError("");
    try { const result = proofSchema.parse(await memoryRequest(`${path}?project_id=${encodeURIComponent(project)}`)); if (result.project_id !== project) throw new Error(); setProof(result); sessionStorage.removeItem(storage); setPending(undefined); }
    catch (e) { setError(e instanceof MemoryApiError && e.status === 404 ? "尚未找到已保存草稿。可按原请求提交，服务会核对是否已经保存。" : safeMessage(e)); }
    finally { setBusy(false); }
  }
  async function save() {
    if (!binding || !receipt.can_download || !ready || busy) return;
    const body = pending ?? requestSchema.parse({ request_id: crypto.randomUUID(), project_id: binding.project_id, connection_id: binding.connection_id, expected_decision_revision: receipt.revision, expected_candidate_sha256: receipt.candidate_sha256, expected_comparison_sha256: receipt.comparison_sha256 });
    try { sessionStorage.setItem(storage, JSON.stringify(body)); } catch { setError("无法保存请求标识，尚未发送。请恢复会话存储。"); return; }
    setPending(body); setBusy(true); setError("");
    try { const result = proofSchema.parse(await memoryRequest(path, { method: "POST", body })); if (result.project_id !== body.project_id) throw new Error(); setProof(result); sessionStorage.removeItem(storage); setPending(undefined); }
    catch (e) { setError(safeMessage(e)); }
    finally { setBusy(false); }
  }
  if (!receipt.can_download && !pending && !proof) return null;
  return <details className="memory-task-choice" open={open} onToggle={e => setOpen(e.currentTarget.open)}><summary>保存经验草稿</summary>
    <p className="small muted">保存案例、适用源码版本与当前回归和决定的摘要。不保存完整补丁或模型对话，不会自动确认、索引或外发。</p>
    {open && !proof && <>
      {!pending && <BindingPicker disabled={busy} localOnly onSelect={setBinding} />}
      {binding && <p className="small">目标：{binding.project_id} / {binding.connection_id}</p>}
      <div className="memory-action-row"><button className="btn btn-sm" disabled={!binding || busy || !ready || !receipt.can_download} onClick={() => void save()}>{pending ? "按原请求保存" : "保存到待确认经验"}</button><button className="btn btn-ghost btn-sm" disabled={!binding || busy} onClick={() => void recover()}>查询保存结果</button></div>
      {pending && <p className="small muted">此请求可能已经写入，请先查询。重试保留原请求，不会重复生成草稿。</p>}
    </>}
    {proof && <p role="status">草稿记录已找到。<Link className="link" href={`/workspace/memory?project=${proof.project_id}&memory=${proof.memory_id}`}>查看并核对经验</Link>{!proof.source_current_valid && " · 来源决定已变化，请重新核对。"}</p>}
    {error && <p className="memory-error" role="alert">{error}</p>}
  </details>;
}
