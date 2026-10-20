"use client";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { z } from "zod";
import { key, memoryRequest } from "../../lib/memory";

const ref = z.object({ memory_id: key, revision: z.number().int(), classification: z.enum(["exact", "historical_hint"]), current_state: z.enum(["draft", "confirmed", "revoked", "deleted", "changed", "unavailable"]).optional() });
const schema = z.object({ state: z.enum(["disabled", "pending", "running", "done", "unknown", "unavailable"]), reason: z.string().max(96).optional(), project_id: key.optional(), references: z.array(ref).max(8), uses: z.array(z.object({ role: z.enum(["diagnosis", "repair", "verification"]), refs: z.array(ref).max(8) })).max(12), accounting: z.object({ state: z.string(), calls: z.array(z.object({ kind: z.enum(["llm", "embedding"]), state: z.string(), actual_tokens: z.number().nullable() })).max(46) }).optional() });
const currentLabels = { draft: "待重新确认", confirmed: "当前已确认", revoked: "已撤回", deleted: "已删除", changed: "内容已变化", unavailable: "当前不可读取" };
const roles = { diagnosis: "诊断", repair: "修复", verification: "验证" };

export function TaskMemoryUsage({ kind, taskId, revision, agentTokens, agentComplete }: { kind: "analysis" | "repair"; taskId: string; revision: number; agentTokens: number; agentComplete: boolean }) {
  const [refresh, setRefresh] = useState(0);
  const previous = useRef(""); const [loading, setLoading] = useState(false);
  const [value, setValue] = useState<z.infer<typeof schema> | null>(null); const [failed, setFailed] = useState(false);
  useEffect(() => { const controller = new AbortController(); if (previous.current !== `${kind}:${taskId}`) { setValue(null); previous.current = `${kind}:${taskId}`; } setFailed(false); setLoading(true); void memoryRequest(`/api/${kind === "analysis" ? "analysis-runs" : "repair-runs"}/${encodeURIComponent(taskId)}/memory`, { signal: controller.signal }).then(data => { if (!controller.signal.aborted) setValue(schema.parse(data)); }).catch(() => { if (!controller.signal.aborted) setFailed(true); }).finally(() => { if (!controller.signal.aborted) setLoading(false); }); return () => controller.abort(); }, [kind, taskId, revision, refresh]);
  if (!value || value.state === "disabled") return failed ? <p className="small muted" role="status">经验引用暂时无法核对，请刷新后查看。</p> : null;
  const calls = value.accounting?.calls ?? []; const known = calls.reduce((sum, call) => sum + (call.actual_tokens ?? 0), 0);
  const complete = value.state === "done" && agentComplete && calls.every(call => call.state === "received" && call.actual_tokens !== null);
  const used = value.uses.filter(use => use.refs.length);
  const status = value.state === "unknown" ? "检索结果或费用未知" : value.state === "running" || value.state === "pending" ? "正在检索参考经验" : value.reason?.startsWith("context_") ? "当前参考权限已变化，后续停止取用" : value.reason === "matched" ? "已检索到参考" : value.reason === "no_match" ? "没有匹配经验" : value.reason?.includes("unavailable") || !value.references.length ? "没有可用参考，请展开核对" : "已检索到参考";
  return <details className="pane memory-task-usage"><summary>参考经验 · {status} · {used.length} 次调用获准使用</summary>
    <button type="button" className="btn btn-ghost btn-sm" disabled={loading} onClick={() => setRefresh(v => v + 1)}>{loading ? "正在核对经验状态…" : "刷新经验状态"}</button>
    {failed && <p role="alert" className="memory-error">刷新未完成，下方是上次读取的记录，当前有效性尚未确认。</p>}
    <p className="small muted">检索命中不代表模型已使用；下方记录调用获准发送的引用，不能证明模型采纳了建议。验证仍依据本次测试。</p>
    <p className="small">检索已知 {known.toLocaleString()} tokens（LLM {calls.filter(c => c.kind === "llm").length} 次、向量 {calls.filter(c => c.kind === "embedding").length} 次）；Agent 已知 {agentTokens.toLocaleString()} tokens。合计已知 {(known + agentTokens).toLocaleString()} tokens，{complete ? "用量已核对" : "总用量尚未完全确认"}；金额未知。</p>
    {value.project_id && value.references.map(reference => <p className="small" key={reference.memory_id}><Link className="link" href={`/workspace/memory?project=${value.project_id}&memory=${reference.memory_id}`}>查看来源 · 修订 {reference.revision}</Link> · {reference.classification === "exact" ? "同版本参考" : "历史版本线索"}{reference.current_state && ` · ${currentLabels[reference.current_state]}`}</p>)}
    {value.uses.map((use, index) => <p className="small" key={index}>{roles[use.role]}第 {index + 1} 次请求：{use.refs.length ? `${use.refs.length} 条经验获准发送` : "未引用经验"}</p>)}
    {value.reason && <details><summary>状态详情</summary><code>{value.reason}</code></details>}
  </details>;
}
