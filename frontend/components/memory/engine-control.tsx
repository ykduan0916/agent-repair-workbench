"use client";
import { useEffect, useState } from "react";
import { z } from "zod";
import { MemoryApiError, memoryRequest, newRequest, projectPath, safeMessage, type Memory } from "../../lib/memory";
import type { ProjectContext } from "./project-context";

const jobSchema = z.object({ state: z.enum(["pending", "running", "done", "unknown"]), reason: z.string().max(128).nullable().optional(), result: z.object({ removed_roots: z.number().int().nonnegative().optional() }).nullable().optional(), accounting: z.object({ reserved_tokens: z.number().int().nonnegative(), calls: z.array(z.object({ actual_tokens: z.number().int().nonnegative().nullable() })).max(100) }).optional() });
/** Explicit bounded background work; no write retries after an unknown response. */
export function EngineControl({ context, memory }: { context: ProjectContext; memory?: Memory }) {
  const [request, setRequest] = useState(""); const [status, setStatus] = useState(""); const [pending, setPending] = useState(false); const [resolved, setResolved] = useState(true); const [usage, setUsage] = useState("");
  const path = `${projectPath(context.project)}/engine`;
  const storage = `awb-memory-engine:${context.project}:${memory?.receipt.resource_id ?? "cleanup"}`;
  useEffect(() => { try { const saved = sessionStorage.getItem(storage); if (saved && /^[a-f0-9-]{36}$/.test(saved)) { setResolved(false); setPending(true); setRequest(saved); } } catch { /* Pending identity is also available in technical details. */ } }, [storage]);
  useEffect(() => {
    if (!request) return; const controller = new AbortController(); let timer: ReturnType<typeof setTimeout>; let polls = 0;
    const poll = async () => {
      try { const job = jobSchema.parse(await memoryRequest(`${path}/${request}`, { signal: controller.signal })); if (controller.signal.aborted) return;
        if (job.accounting) { const known = job.accounting.calls.reduce((sum, call) => sum + (call.actual_tokens ?? 0), 0); const incomplete = job.accounting.calls.some(call => call.actual_tokens === null); setUsage(`已知用量 ${known} tokens${incomplete ? "，仍有未知用量" : ""}；预留 ${job.accounting.reserved_tokens} tokens。金额尚无计价依据。`); }
        if (job.state === "done" || job.state === "unknown") { setStatus(job.state === "done" ? job.result?.removed_roots !== undefined ? `清理完成，移除 ${job.result.removed_roots} 个派生目录。` : "索引已建立。后续检索仍会核对确认状态、授权与适用版本。" : "处理结果未知，已停止自动重试。请核对任务记录。"); setResolved(job.state === "done"); setPending(false); return; }
        setStatus("处理中，离开页面不会重复提交。任务标识保留在技术详情。");
        if (++polls < 100) timer = setTimeout(poll, 2000); else { setStatus("等待已超时，请稍后重新查询。不会重复执行。"); setPending(false); }
      } catch (e) { if (!controller.signal.aborted) { setStatus(safeMessage(e)); setPending(false); } }
    }; void poll(); return () => { controller.abort(); clearTimeout(timer); };
  }, [request, path]);
  async function start(operation: "index" | "cleanup") {
    setPending(true); setResolved(false); const id = newRequest(); setStatus("正在提交…");
    try { sessionStorage.setItem(storage, id); } catch { setStatus("无法保存任务标识，已停止提交。请允许本机页面的会话存储后重试。"); setPending(false); setResolved(true); return; }
    try { await memoryRequest(path, { method: "POST", body: { request_id: id, operation, ...(memory ? { memory_id: memory.receipt.resource_id } : {}) } }); setRequest(id); }
    catch (e) { setStatus(safeMessage(e)); setPending(false);
      if (e instanceof MemoryApiError && e.status >= 400 && e.status < 500) { try { sessionStorage.removeItem(storage); } catch { /* Browser storage can become unavailable. */ } setResolved(true); }
      else setRequest(id);
    }
  }
  return <div className="memory-engine"><p className="small muted">{memory ? "索引会将已授权正文发送至配置的模型与向量服务。先在来源授权中允许对应内容的索引用途。" : "清理已撤回、删除或过期修订的派生索引，不重新调用模型。"}</p>
    <button className="btn btn-sm" disabled={pending || !resolved || context.busy || (memory && memory.receipt.state !== "confirmed")} onClick={() => void start(memory ? "index" : "cleanup")}>{memory ? "建立索引" : "清理派生索引"}</button>
    {status && <p role="status" className="small">{status}</p>}
    {usage && <p className="small muted">{usage}</p>}
    {request && <details><summary>技术详情</summary><code>{request}</code><button className="btn btn-sm" disabled={pending} onClick={() => { const id = request; setRequest(""); requestAnimationFrame(() => { setPending(true); setRequest(id); }); }}>查询处理状态</button></details>}
  </div>;
}
