"use client";
import Link from "next/link";
import { z } from "zod";
import { useEffect, useRef, useState } from "react";
import { PageHeader } from "./ui/page-header";
import { useConfirm } from "./ui/confirm-dialog";
import CapabilityBadge from "./capability-badge";
import { OverviewPager, ProjectBar, useMemoryProject, type ProjectContext } from "./memory/project-context";
import { MemoryEditor } from "./memory/memory-editor";
import { kindLabels, stateLabels, memorySchema, safeMessage, memoryRequest, newRequest, projectPath, type Memory } from "../lib/memory";

export default function MemoryWorkspace() {
  const context = useMemoryProject(); const [selected, setSelected] = useState(""); const [creating, setCreating] = useState(false); const [editing, setEditing] = useState(false);
  const [state, setState] = useState(""); const [kind, setKind] = useState(""); const [tool, setTool] = useState("");
  const list = useRef<HTMLDivElement>(null); const detail = useRef<HTMLDivElement>(null);
  useEffect(() => { setSelected(new URLSearchParams(location.search).get("memory") ?? ""); setCreating(false); }, [context.project]);
  const all = context.overview?.memories ?? [];
  const rows = all.filter(m => (!state || m.receipt.state === state) && (!kind || m.kind === kind) && (!tool || m.content?.tool_id === tool));
  const [remote, setRemote] = useState<Memory>();
  useEffect(() => { setRemote(undefined); if (!selected || !context.project || !context.overview || all.some(m => m.receipt.resource_id === selected)) return; const controller = new AbortController(); void memoryRequest(`${projectPath(context.project)}/memories/${encodeURIComponent(selected)}`, { signal: controller.signal }).then(value => { if (!controller.signal.aborted) setRemote(memorySchema.parse(value)); }).catch(error => { if (!controller.signal.aborted) context.setError(safeMessage(error)); }); return () => controller.abort(); }, [selected, context.project, context.overview]);
  const memory = all.find(m => m.receipt.resource_id === selected) ?? (remote?.receipt.resource_id === selected ? remote : undefined);
  function choose(id: string) { setSelected(id); setCreating(false); const url = new URL(location.href); url.searchParams.set("memory", id); history.replaceState(null, "", url); requestAnimationFrame(() => detail.current?.focus()); }
  function close() { setSelected(""); setCreating(false); const url = new URL(location.href); url.searchParams.delete("memory"); history.replaceState(null, "", url); (list.current?.querySelector<HTMLButtonElement>(`button[data-memory-id="${CSS.escape(selected)}"]`) ?? list.current?.querySelector<HTMLButtonElement>("button"))?.focus(); }
  return <div className="memory-page">
    <PageHeader title="项目经验" description="核对来源、确认经验，并决定哪些内容可以用于后续排查。" badge={<CapabilityBadge section="memory" />} />
    <ProjectBar context={context} />
    {context.error && <p className="memory-error" role="alert">{context.error}</p>}
    {context.loading && <p role="status" className="muted">正在读取项目记录…</p>}
    {!context.loading && !context.project && <div className="memory-empty"><h2>先创建一个项目</h2><p>项目用于隔离经验与事件来源。创建后，在环境与接入中添加来源。</p></div>}
    {context.project && context.overview && <>
      <div className="memory-toolbar">
        <label>状态<select value={state} onChange={e => setState(e.target.value)}><option value="">全部状态</option>{Object.entries(stateLabels).map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label>
        <label>类型<select value={kind} onChange={e => setKind(e.target.value)}><option value="">全部类型</option>{Object.entries(kindLabels).map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label>
        <label>工具<select value={tool} onChange={e => setTool(e.target.value)}><option value="">全部工具</option>{[...new Set(all.flatMap(m => m.content ? [m.content.tool_id] : []))].map(t => <option key={t}>{t}</option>)}</select></label>
        <button className="btn btn-primary btn-sm" disabled={context.busy || !context.overview.events.length} onClick={() => { setSelected(""); setCreating(true); requestAnimationFrame(() => detail.current?.focus()); }}>新增经验</button>
        <Link className="link small" href={`/workspace/connections?project=${context.project}#memory`}>管理事件来源</Link>
      </div>
      <p className="small muted">筛选当前页的经验；翻页查看更早的记录。</p><OverviewPager context={context} section="memories" label="经验" />
      <div className="memory-layout" data-detail={Boolean(memory || creating)}>
        <div className="memory-list pane" ref={list} aria-label="经验列表">
          {rows.length === 0 ? <div className="memory-empty"><h2>{all.length ? "没有符合条件的经验" : "还没有经验"}</h2><p>{all.length ? "更换筛选条件，或新增一条经验。" : "先在接入页接收事件，再从事件补充故障、修复方法或项目约束。"}</p></div> : rows.map(m => <button key={m.receipt.resource_id} className="memory-list-row" data-memory-id={m.receipt.resource_id} aria-pressed={selected === m.receipt.resource_id} onClick={() => choose(m.receipt.resource_id)}>
            <span className="memory-row-title">{m.content?.title ?? "已删除的经验"}</span><span className="memory-state">{stateLabels[m.receipt.state]}</span>
            <span className="muted small" title={m.content?.source_version}>{kindLabels[m.kind]}{m.content ? ` · ${m.content.tool_id} · 版本 ${m.content.source_version.length > 16 ? `${m.content.source_version.slice(0, 12)}…` : m.content.source_version}` : " · 正文已清除"}</span>
          </button>)}
        </div>
        {(memory || creating) && <div ref={detail} className="memory-detail pane" tabIndex={-1} aria-label="经验详情"><div className="memory-detail-head"><h2>{creating ? "新增经验" : "经验详情"}</h2><button className="btn btn-ghost btn-sm" onClick={close}>关闭详情</button></div>
          <MemoryEditor key={`${context.project}:${memory?.receipt.resource_id ?? "new"}:${memory?.receipt.version ?? 0}`} context={context} memory={memory} onSaved={close} onEditing={setEditing} />
          {memory && !editing && <MemoryActions context={context} memory={memory} />}
        </div>}
      </div>
    </>}
  </div>;
}

function MemoryActions({ context, memory }: { context: ProjectContext; memory: Memory }) {
  const confirm = useConfirm(); const { resource_id: id, version, state } = memory.receipt;
  const [source, setSource] = useState<{ task_id: string; source_current_valid: boolean; current_decision: string | null } | null>(null);
  const [sourceReady, setSourceReady] = useState(false);
  useEffect(() => { const controller = new AbortController(); setSourceReady(false); void memoryRequest(`${projectPath(context.project)}/memories/${id}/repair-source`, { signal: controller.signal }).then(value => { const result = z.object({ source: z.object({ task_id: z.string().uuid(), source_current_valid: z.boolean(), current_decision: z.string().nullable() }).nullable() }).parse(value); if (!controller.signal.aborted) { setSource(result.source); setSourceReady(true); } }).catch(() => { if (!controller.signal.aborted) context.setError("无法核对修复来源，暂不能确认经验，请刷新。"); }); return () => controller.abort(); }, [context.project, id, version, context.overview]);
  async function change(next: "confirmed" | "revoked" | "deleted") {
    if (next === "deleted" && !await confirm({ title: "删除这条经验？", message: "逻辑正文会被清除并立即停止取用。派生索引需要另行清理；已下载、备份及在途请求不受此次删除影响。", confirmLabel: "删除经验", danger: true })) return;
    await context.execute(() => memoryRequest(`${projectPath(context.project)}/memories/${id}`, { method: "PATCH", body: { request_id: newRequest(), expected_version: version, state: next } }));
  }
  return <div className="memory-actions">
    {source && <p className="small">修复来源：<Link className="link" href={`/workspace/repairs?task=${source.task_id}`}>查看候选与回归</Link> · {source.source_current_valid ? "当前仍为接受且证据有效" : "来源决定或证据已变化，暂不能确认"}。</p>}
    {state !== "deleted" && <><p className="small muted">确认只表示你认可内容。索引、外发和后续参考需要单独授权；历史经验不能代替当前测试。</p><div className="memory-action-row">
      {state !== "confirmed" && <button className="btn btn-primary btn-sm" disabled={context.busy || !sourceReady || source?.source_current_valid === false} onClick={() => void change("confirmed")}>确认经验</button>}
      {state === "confirmed" && <button className="btn btn-sm" disabled={context.busy} onClick={() => void change("revoked")}>撤回经验</button>}
      <button className="btn btn-ghost btn-sm" disabled={context.busy} onClick={() => void change("deleted")}>删除经验</button>
    </div></>}
    <details><summary>技术详情</summary><dl className="memory-meta"><dt>经验标识</dt><dd>{id}</dd><dt>内容修订</dt><dd>{memory.receipt.revision}</dd><dt>状态版本</dt><dd>{version}</dd><dt>内容摘要</dt><dd>{memory.digest}</dd></dl></details>
  </div>;
}
