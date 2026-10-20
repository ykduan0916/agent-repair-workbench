"use client";
import Link from "next/link";
import { TaskMemoryChoice } from "../memory/task-memory-choice";
import { TaskMemoryUsage } from "../memory/task-memory-usage";
import { useEffect, useRef, useState } from "react";
import { Play, Square, RefreshCw } from "lucide-react";
import { caseLabels, modeLabels, outcomes, phaseLabels, errorLabels } from "../../lib/repairs";
import { mergeReceipt, loadRepairCatalog, loadReceipt, loadRepairEvents, startRepair, stopRepair, pendingRepairSchema, REPAIR_PENDING_KEY, type PendingRepair, type RepairCatalog, type RepairReceipt, type RepairEvents } from "../../lib/repair-runs";
import { runError, RunApiError } from "../../lib/runs";
import { whenVisible } from "../../lib/analysis-runs";
import { formatNumber } from "../../lib/format";
import { useConfirm } from "../ui/confirm-dialog";
import { StatusBadge } from "../ui/status";
import { Callout } from "../ui/callout";
import ExecutionTerminal from "../execution-terminal";

const kinds = { created: "任务已创建", reserved: "已登记调用", settled: "调用已结束", finished: "任务已结束", reconciled: "已核对中断状态", comparison_slot: "回归检查项已记录" };
const roles = { single: "单角色", diagnosis: "诊断", repair: "修复", verification: "验证" };
const tools = { T01: "读取证据", T02: "查询工具契约", T03: "读取授权源码", T04: "检查调用契约", T05: "隔离复现", T06: "生成隔离候选", T07: "冻结回归" };
const operations = { model: "模型", tool: "工具", candidate: "候选", comparison: "回归", reproduction: "复现" };

type Props = { selected: string; onSelect: (id: string) => void; onRefresh: () => void };
export default function RepairRunControls({ selected, onSelect, onRefresh }: Props) {
  const confirm = useConfirm();
  const [choice, setChoice] = useState<Omit<PendingRepair, "request_id">>({ case_id: "output_mismatch", mode: "three_role" });
  const [pending, setPending] = useState<PendingRepair | null>(null);
  const [storageReady, setStorageReady] = useState(false);
  const [catalog, setCatalog] = useState<RepairCatalog | null>(null);
  const [receipt, setReceipt] = useState<RepairReceipt | null>(null);
  const [events, setEvents] = useState<RepairEvents | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [queryError, setQueryError] = useState("");
  const [uncertain, setUncertain] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [expanded, setExpanded] = useState(true);
  const callbacks = useRef({ onSelect, onRefresh }); callbacks.current = { onSelect, onRefresh };
  const writing = useRef(false), alive = useRef(true);
  const requests = useRef(new Set<AbortController>());
  const revisions = useRef(new Map<string, number>());
  const target = pending?.request_id || selected;
  const targetRef = useRef(target); targetRef.current = target;
  const current = receipt?.task_id === target ? receipt : null;
  const running = current && current.task.phase !== "finished";

  useEffect(() => {
    alive.current = true;
    try {
      const raw = sessionStorage.getItem(REPAIR_PENDING_KEY);
      if (raw !== null) {
        if (raw.length > 1024) throw new Error();
        const saved = pendingRepairSchema.parse(JSON.parse(raw));
        setPending(saved); setChoice(saved); setUncertain(true); callbacks.current.onSelect(saved.request_id);
      }
      sessionStorage.setItem("awb-repair-storage-probe", "1"); sessionStorage.removeItem("awb-repair-storage-probe"); setStorageReady(true);
    } catch { setError("无法读取或保存提交记录，已禁用开始操作。请恢复本站会话存储；损坏记录需先保留并核对，不能直接丢弃后重建任务。"); }
    return () => { alive.current = false; requests.current.forEach(c => c.abort()); };
  }, []);

  useEffect(() => {
    const controller = new AbortController(); let timer: ReturnType<typeof setTimeout>; let visible = () => {}; let failures = 0;
    const later = (ms: number) => { timer = setTimeout(() => { visible = whenVisible(() => void poll()); }, ms); };
    async function poll() {
      try {
        const c = await loadRepairCatalog(controller.signal);
        if (controller.signal.aborted) return;
        setCatalog(c); setQueryError("");
        if (target) {
          let r: RepairReceipt;
          try { r = await loadReceipt(target, controller.signal, pending ?? undefined); }
          catch (e) {
            if (controller.signal.aborted) return;
            if (e instanceof RunApiError && e.status === 404 && !pending) { setReceipt(null); setEvents(null); failures = 0; later(10000); return; }
            throw e;
          }
          if (controller.signal.aborted) return;
          setReceipt(old => mergeReceipt(old, r)); setUncertain(false);
          if (revisions.current.get(target) !== r.task.revision) { revisions.current.set(target, r.task.revision); callbacks.current.onRefresh(); }
          if (pending && r.task.phase === "finished" && !r.active) {
            try { sessionStorage.removeItem(REPAIR_PENDING_KEY); setPending(null); }
            catch { setStorageReady(false); setError("任务已结束，但无法清除本机提交记录。请恢复会话存储后刷新。"); }
          }
          const e = await loadRepairEvents(target, controller.signal);
          if (controller.signal.aborted) return;
          setEvents(old => old?.task_id === e.task_id && old.revision > e.revision ? old : e);
        } else { setReceipt(null); setEvents(null); }
        failures = 0;
        later(c.slot.status === "busy" || pending ? 2000 : 10000);
      } catch (e) {
        if (controller.signal.aborted) return;
        setQueryError(e instanceof RunApiError && e.status === 404 ? "暂未查到这次提交。可以继续查询，或明确重新提交同一请求。" : runError(e));
        if (++failures <= 5) later(Math.min(1000 * 2 ** (failures - 1), 10000));
      }
    }
    visible = whenVisible(() => void poll());
    return () => { controller.abort(); clearTimeout(timer); visible(); };
  }, [target, pending, refresh]);

  const blocked = !catalog ? "正在读取运行条件。" : !catalog.accepting ? "服务正在停止，请稍后刷新。"
    : catalog.slot.status === "recovery_required" ? "上次运行的资源需要核对，暂不能开始新修复。"
    : catalog.slot.status === "busy" ? "已有修复占用执行槽，结束并完成资源回收后可开始下一次。"
    : !catalog.model.configured ? "模型尚未就绪，请到环境页检查配置。"
    : !catalog.image.prepared ? "修复镜像尚未就绪，请到环境页检查。" : "";
  const disabled = !storageReady || !!pending || submitting || !!blocked || !!queryError;
  async function submit(existing?: PendingRepair) {
    if (writing.current || !storageReady || (!existing && disabled)) return;
    const request = existing ?? { ...choice, request_id: crypto.randomUUID() };
    try { sessionStorage.setItem(REPAIR_PENDING_KEY, JSON.stringify(request)); }
    catch { setStorageReady(false); setError("修复未开始：无法保存提交记录，请允许本站使用会话存储。"); return; }
    writing.current = true; setSubmitting(true); setPending(request); setUncertain(true); setNotice(""); setExpanded(true);
    callbacks.current.onSelect(request.request_id);
    const controller = new AbortController(); requests.current.add(controller);
    try {
      const r = await startRepair(request, controller.signal);
      if (!alive.current) return;
      setReceipt(old => mergeReceipt(old, r)); setUncertain(false); setNotice(r.reused ? "已找到原任务，没有重复执行。" : "修复已接收，正在运行。"); callbacks.current.onRefresh();
    } catch (e) { if (alive.current) { setUncertain(true); setNotice(`${runError(e)} 已保留这次提交，请查询原任务。`); } }
    finally { writing.current = false; requests.current.delete(controller); if (alive.current) { setSubmitting(false); setRefresh(v => v + 1); } }
  }
  async function stop() {
    if (!current || !running || stopping || writing.current || current.cancel_requested) return;
    const id = current.task_id;
    const ok = await confirm({ title: "停止这次修复？", message: "停止请求会保存到本机。正在进行的调用需要收尾，已用预算和候选记录会保留。停止后不会自动续跑。", confirmLabel: "停止修复", danger: true });
    if (!ok || !alive.current || targetRef.current !== id || writing.current) return;
    writing.current = true; setStopping(true);
    const controller = new AbortController(); requests.current.add(controller);
    try { const r = await stopRepair(id, controller.signal); if (alive.current && targetRef.current === id) { setReceipt(old => mergeReceipt(old, r)); setNotice("停止请求已登记，等待实际执行结束。"); } }
    catch { if (alive.current) setNotice("无法确认停止请求，请查询原任务后再决定是否重试停止。"); }
    finally { writing.current = false; requests.current.delete(controller); if (alive.current) { setStopping(false); setRefresh(v => v + 1); } }
  }
  const task = current?.task;
  const known = task ? task.usage.known_input_tokens + task.usage.known_output_tokens : 0;
  const entries = events?.task_id === target ? events.events.map(e => ({ id: `${target}:${e.revision}`, at: Date.parse(e.at), source: e.role ? roles[e.role] : "系统",
    message: `${kinds[e.kind]}${e.tool ? `：${tools[e.tool]}` : e.operation ? `：${operations[e.operation]}` : ""}${e.error_code ? `；${errorLabels[e.error_code]}` : ""}`, level: e.error_code ? "error" as const : undefined })) : [];
  return <>
    {task && <TaskMemoryUsage kind="repair" taskId={task.task_id} revision={task.revision} agentTokens={known} agentComplete={task.usage.usage_complete} />}
    <section className="pane rp-launch" aria-label="新建修复">
      <div className="rp-launch-row">
        <div className="field"><label htmlFor="repair-case">案例</label><select className="select" id="repair-case" value={choice.case_id} disabled={!!pending || submitting} onChange={e => setChoice(v => ({ ...v, case_id: e.target.value as PendingRepair["case_id"] }))}>{Object.entries(caseLabels).map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select></div>
        <div className="field"><label htmlFor="repair-mode">协作方式</label><select className="select" id="repair-mode" value={choice.mode} disabled={!!pending || submitting} onChange={e => setChoice(v => ({ ...v, mode: e.target.value as PendingRepair["mode"], memory: e.target.value === "single_role" ? undefined : v.memory }))}>{Object.entries(modeLabels).map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select></div>
        <button className="btn btn-primary" disabled={disabled} aria-busy={submitting || undefined} onClick={() => void submit()}><Play size={14} aria-hidden="true" />{submitting ? "正在提交…" : "开始修复"}</button>
        <button className="btn btn-ghost" disabled={submitting} onClick={() => setRefresh(v => v + 1)}><RefreshCw size={14} aria-hidden="true" />查询状态</button>
      </div>
      <TaskMemoryChoice value={choice.memory} onChange={memory => setChoice(v => ({ ...v, memory }))} disabled={submitting || !!pending} supported={choice.mode === "three_role"} />
      <p className="muted small">运行内置案例 → 核对代码差异与回归 → 人工决定 → 下载补丁。不会写回你的代码。 <Link className="link" href="/docs/27-local-development-guide#网页修复与本机常驻服务">操作指南</Link></p>
      {catalog && <details className="rp-budget"><summary>本次预算上限</summary><p className="muted small">Agent 模型 {catalog.budget.model_calls} 次 · 工具 {catalog.budget.tool_calls} 次 · 候选 {catalog.budget.candidates} 个 · {catalog.budget.timeout_seconds} 秒 · {formatNumber(catalog.budget.total_tokens)} tokens（单次输出 {formatNumber(catalog.budget.output_tokens)}）。启用经验时，另有一次检索，最多 6 次检索 LLM 与 40 次向量请求；检索 100,000 与 Agent 1,900,000 tokens 共同受总额约束。格式纠正最多一次，计入 Agent 预算；超时和未知结果不重试。费用未配置，未知用量保留预留。</p></details>}
      {blocked && <p className="small muted">{blocked} <Link className="link" href="/workspace/connections">查看环境</Link>{catalog?.slot.task_id && <button className="link rp-inline" onClick={() => callbacks.current.onSelect(catalog.slot.task_id!)}>查看活动修复</button>}</p>}
      {error && <Callout tone="fail" title="提交存储不可用">{error}</Callout>}
      {queryError && <p role="status" className="small">{queryError}</p>}
      {notice && <p role="status" className="small">{notice}</p>}
      {pending && uncertain && <Callout tone="unknown" title="提交结果待确认" actions={<button className="btn btn-sm" disabled={submitting || !storageReady} onClick={() => void submit(pending)}>重新提交同一请求</button>}>刷新只查询，不会重新执行。如果上次没有送达，明确重新提交会首次启动；如果已接收，会返回原任务。</Callout>}
      {pending && target !== selected && <button className="link rp-inline" onClick={() => callbacks.current.onSelect(pending.request_id)}>返回正在跟踪的修复</button>}
    </section>
    {current && <ExecutionTerminal expanded={expanded || !!running} idle={<button className="link rp-inline" onClick={() => setExpanded(true)}>展开本次执行事件</button>}
      heading={<><strong>{caseLabels[current.case_id]}</strong><StatusBadge tone={running ? "running" : "neutral"}>{running && current.cancel_requested ? "正在停止" : task?.outcome ? (task.outcome === "candidate_verified" ? "候选曾通过，需核对当前回归" : outcomes[task.outcome]) : phaseLabels[task!.phase]}</StatusBadge></>}
      meta={<span className="tabular">模型 {task!.usage.model_calls} 次 · 工具 {task!.usage.tool_calls} 次 · 已知 {formatNumber(known)} tokens{!task!.usage.usage_complete && " · 用量待确认"}</span>}
      actions={running ? <button className="btn btn-sm btn-danger" disabled={stopping || current.cancel_requested} onClick={() => void stop()}><Square size={12} aria-hidden="true" />{stopping || current.cancel_requested ? "正在停止" : "停止修复"}</button> : <button className="btn btn-ghost btn-sm" onClick={() => setExpanded(false)}>收起</button>}
      entries={entries} empty="正在读取已保存的事件…" />}
  </>;
}
