"use client";
import Link from "next/link";
import { useState } from "react";
import { type MemoryBinding } from "../../lib/memory";
import { OverviewPager, ProjectBar, useMemoryProject } from "./project-context";

export function TaskMemoryChoice({ value, onChange, disabled, supported = true }: { value?: MemoryBinding; onChange: (value?: MemoryBinding) => void; disabled: boolean; supported?: boolean }) {
  const [open, setOpen] = useState(false);
  return <details className="memory-task-choice" open={open} onToggle={event => setOpen(event.currentTarget.open)}>
    <summary>参考项目经验 · {value && supported ? value.project_id : "未启用"}</summary>
    {!supported ? <p className="small muted">此模式不使用项目经验。请选择模型分析或多角色修复。</p> : <>
      {value && <p className="small">当前来源：{value.connection_id} · {value.allow_historical ? "包含历史版本线索" : "仅当前版本"} <button type="button" className="link" disabled={disabled} onClick={() => onChange(undefined)}>停用参考</button></p>}
      {open && <BindingPicker disabled={disabled} onSelect={onChange} />}
      <p className="small muted">需先确认、授权并建立索引。一次检索最多 6 次 LLM、40 次向量请求和 100,000 tokens，Agent 保留 1,900,000 tokens；验证角色仍依据当前测试。<Link className="link" href="/workspace/memory">管理经验</Link></p>
    </>}
  </details>;
}

export function BindingPicker({ disabled, onSelect, localOnly = false }: { disabled: boolean; onSelect: (binding: MemoryBinding) => void; localOnly?: boolean }) {
  const context = useMemoryProject(); const [connection, setConnection] = useState(""); const [historical, setHistorical] = useState(false);
  const sources = context.overview?.connections.filter(c => c.enabled && (!localOnly || c.origin !== "external_report")) ?? [];
  const selected = sources.find(c => c.connection_id === connection);
  return <fieldset className="memory-page memory-form memory-binding-fields" disabled={disabled}><legend className="sr-only">选择项目经验来源</legend>
    <ProjectBar context={context} />
    {context.error && <p role="alert" className="memory-error">{context.error}</p>}
    <label>经验来源<select disabled={disabled || context.loading} value={selected ? connection : ""} onChange={event => setConnection(event.target.value)}><option value="">选择来源</option>{sources.map(c => <option key={c.connection_id}>{c.connection_id}</option>)}</select></label>
    <OverviewPager context={context} section="connections" label="经验来源" />
    {!localOnly && <label className="memory-check"><input type="checkbox" disabled={disabled} checked={historical} onChange={event => setHistorical(event.target.checked)} />允许已授权的跨版本历史线索</label>}
    <button type="button" className="btn btn-sm" disabled={disabled || context.loading || !context.project || !selected} onClick={() => onSelect({ project_id: context.project, connection_id: connection, allow_historical: historical })}>使用此来源</button>
    {context.project && !context.loading && sources.length === 0 && <p className="small muted">当前页没有可用来源。请到环境与接入中添加{localOnly ? "本机手动记录" : "经验"}来源。</p>}
  </fieldset>;
}
