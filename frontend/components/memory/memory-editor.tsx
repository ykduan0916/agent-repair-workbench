"use client";
import { z } from "zod";
import { useEffect, useState } from "react";
import { dateLabel, kindLabels, policySchema, grantSchema, safeMessage, memoryRequest, newRequest, projectPath, type Draft, type Memory } from "../../lib/memory";
import { OverviewPager, type ProjectContext } from "./project-context";
import { EngineControl } from "./engine-control";

export function MemoryEditor({ context, memory, onSaved, onEditing }: { context: ProjectContext; memory?: Memory; onSaved: () => void; onEditing: (editing: boolean) => void }) {
  const events = context.overview?.events ?? [];
  const [content, setContent] = useState<Draft>(memory?.content ?? { kind: "repair_experience", connection_id: events[0]?.connection_id ?? "", event_id: events[0]?.event_id ?? "", tool_id: "", source_version: "", title: "", body: "" });
  const [edit, setEdit] = useState(!memory);
  useEffect(() => { onEditing(edit); return () => onEditing(false); }, [edit, onEditing]);
  const deleted = memory?.receipt.state === "deleted";
  const [policy, setPolicy] = useState<z.infer<typeof policySchema> | null>(null); const [policyReady, setPolicyReady] = useState(false);
  useEffect(() => { if (!memory) return; const controller = new AbortController(); setPolicyReady(false); void memoryRequest(`${projectPath(context.project)}/memories/${memory.receipt.resource_id}/context`, { signal: controller.signal }).then(value => { const parsed = z.object({ policy: policySchema.nullable(), grants: z.array(grantSchema).max(12) }).parse(value); if (!controller.signal.aborted) { setPolicy(parsed.policy); setHistorical(parsed.policy?.allow_historical ?? false); setConflictKey(parsed.policy?.conflict_key ?? ""); setConflictValue(parsed.policy?.conflict_value ?? ""); setPolicyReady(true); } }).catch(error => { if (!controller.signal.aborted) context.setError(safeMessage(error)); }); return () => controller.abort(); }, [context.project, context.overview, memory?.receipt.resource_id]);
  const [days, setDays] = useState(30); const [historical, setHistorical] = useState(policy?.allow_historical ?? false);
  const [conflictKey, setConflictKey] = useState(policy?.conflict_key ?? ""); const [conflictValue, setConflictValue] = useState(policy?.conflict_value ?? "");
  const field = <K extends keyof Draft>(key: K, value: Draft[K]) => setContent(c => ({ ...c, [key]: value }));
  return <>
    {deleted ? <p className="muted">正文已删除。来源与状态记录仍保留，用于核对删除和清理过程。</p> : edit ? <form className="memory-form" onSubmit={e => { e.preventDefault(); if (new TextEncoder().encode(content.body).byteLength > 16384) { context.setError("正文超过 16 KiB，请缩短内容。"); return; } void context.execute(() => memoryRequest(`${projectPath(context.project)}/memories${memory ? `/${memory.receipt.resource_id}` : ""}`, { method: memory ? "PATCH" : "POST", body: { request_id: newRequest(), ...(memory ? { expected_version: memory.receipt.version } : {}), content } }), () => { setEdit(false); if (!memory) onSaved(); }); }}>
      <label>标题<input required maxLength={160} value={content.title} onChange={e => field("title", e.target.value)} /></label>
      <label>类型<select value={content.kind} onChange={e => field("kind", e.target.value as Draft["kind"])}>{Object.entries(kindLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
      <label>来源事件<select required value={`${content.connection_id}/${content.event_id}`} onChange={e => { const [connection_id, event_id] = e.target.value.split("/"); setContent(c => ({ ...c, connection_id, event_id })); }}>{content.connection_id && content.event_id && !events.some(event => event.connection_id === content.connection_id && event.event_id === content.event_id) && <option value={`${content.connection_id}/${content.event_id}`}>{content.connection_id} · {content.event_id}（当前来源）</option>}{events.map(event => <option key={`${event.connection_id}/${event.event_id}`} value={`${event.connection_id}/${event.event_id}`}>{event.connection_id} · {event.event_type} · {dateLabel(event.received_at)}</option>)}</select></label>
      <OverviewPager context={context} section="events" label="来源事件" />
      <div className="memory-field-pair"><label>工具标识<input required pattern="[A-Za-z0-9_-]+" maxLength={96} value={content.tool_id} onChange={e => field("tool_id", e.target.value)} /></label><label>适用版本<input required pattern="[A-Za-z0-9_-]+" maxLength={96} value={content.source_version} onChange={e => field("source_version", e.target.value)} /></label></div>
      <label>经验内容<textarea required rows={8} maxLength={16384} value={content.body} onChange={e => field("body", e.target.value)} placeholder="说明故障条件、处理方式和适用限制。不要填入密钥或原始对话。" /></label>
      <p className="small muted">保存为待确认草稿。修改已确认经验会暂停旧修订的取用。</p><div className="memory-action-row"><button className="btn btn-primary btn-sm" disabled={context.busy}>保存草稿</button>{memory && <button type="button" className="btn btn-sm" onClick={() => { setContent(memory.content!); setEdit(false); }}>取消修改</button>}</div>
    </form> : <div className="memory-content"><h3>{content.title}</h3><p className="small muted" title={content.source_version}>{kindLabels[content.kind]} · {content.tool_id} · 版本 {content.source_version.length > 16 ? `${content.source_version.slice(0, 12)}…` : content.source_version}</p><p className="memory-body">{content.body}</p><p className="small muted">来源：{content.connection_id}。来源记录不代表已通过内部回归。</p><button className="btn btn-sm" disabled={context.busy} onClick={() => setEdit(true)}>修订内容</button></div>}
    {memory && !deleted && !edit && <details className="memory-policy"><summary>适用范围与索引</summary><form className="memory-form" onSubmit={e => { e.preventDefault(); void context.execute(() => memoryRequest(`${projectPath(context.project)}/memories/${memory.receipt.resource_id}/policy`, { method: "POST", body: { expected_version: policy?.version ?? 0, expected_generation: memory.receipt.version, policy: { expires_at: Date.now() / 1000 + days * 86400, allow_historical: historical, conflict_key: conflictKey || null, conflict_value: conflictValue || null } } })); }}>
      <p className="muted small">{!policyReady ? "正在核对当前适用策略…" : policy ? `当前有效期至 ${dateLabel(policy.expires_at)}` : "尚未设置适用策略，不会进入检索。"}</p>
      <label>从现在起有效天数<input type="number" required min={1} max={365} value={days} onChange={e => setDays(Number(e.target.value))} /></label>
      <label className="memory-check"><input type="checkbox" checked={historical} onChange={e => setHistorical(e.target.checked)} />允许作为其他版本的历史参考</label>
      <div className="memory-field-pair"><label>冲突主题（可选）<input maxLength={96} pattern="[A-Za-z0-9_-]*" value={conflictKey} onChange={e => setConflictKey(e.target.value)} /></label><label>主题取值{conflictKey && "（必填）"}<input required={Boolean(conflictKey)} maxLength={96} pattern="[A-Za-z0-9_-]*" value={conflictValue} onChange={e => setConflictValue(e.target.value)} /></label></div>
      <button className="btn btn-sm" disabled={context.busy || !policyReady}>保存适用策略</button>
    </form><EngineControl context={context} memory={memory} /></details>}
  </>;
}
