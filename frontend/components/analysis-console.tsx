"use client";

import Link from "next/link";
import { TaskMemoryChoice } from "./memory/task-memory-choice";
import { TaskMemoryUsage } from "./memory/task-memory-usage";
import { type MemoryBinding } from "../lib/memory";
import { useEffect, useRef, useState } from "react";
import { ChevronDown, Play, Square } from "lucide-react";
import { AnalysisDetail, analysisStatus, describeEvent, intentLabels, roleLabels } from "../lib/analyses";
import { ANALYSIS_INIT_COMMAND, ANALYSIS_PENDING_KEY, AnalysisRunCatalog, PendingAnalysis, analysisCommand, caseFallback, intentFallback, intentHints, loadAnalysisCatalog, pendingAnalysisSchema, reconcileCommand, startAnalysis, stopAnalysis, whenVisible } from "../lib/analysis-runs";
import { RunApiError, runError } from "../lib/runs";
import { analysisTone } from "../lib/tones";
import { durationBetween, formatDuration, formatNumber, toDate } from "../lib/format";
import { modelReasonMessage } from "../lib/environment";
import { useEnvironmentStatus } from "./ui/environment-context";
import { useConfirm } from "./ui/confirm-dialog";
import { useToast } from "./ui/toast";
import { StatusBadge } from "./ui/status";
import { Callout } from "./ui/callout";
import { CopyButton } from "./ui/copy-id";
import ExecutionTerminal, { TerminalEntry } from "./execution-terminal";

type Notice = { tone: "neutral" | "fail" | "unknown"; text: string } | null;
type Props = {
  detail: AnalysisDetail | null; onSelect: (id: string) => void; onRefresh: () => void;
  refreshKey: number; cliOpen: boolean; onCliOpenChange: (open: boolean) => void;
  /** The server has no record for the pending request ID (its detail read returned 404). */
  pendingMissing?: boolean;
  onPendingChange?: (id: string | null) => void;
};

export function CommandLine({ value, label }: { value: string; label: string }) {
  return <div className="code-block an-command" role="group" aria-label={label}><pre translate="no">{value}</pre><CopyButton value={value} label={label} className="btn btn-ghost btn-sm btn-icon" /></div>;
}

export default function AnalysisConsole({ detail, onSelect, onRefresh, refreshKey, cliOpen, onCliOpenChange, pendingMissing = false, onPendingChange }: Props) {
  const [memory, setMemory] = useState<MemoryBinding>();
  const env = useEnvironmentStatus();
  const confirm = useConfirm();
  const toast = useToast();
  const [catalog, setCatalog] = useState<AnalysisRunCatalog | null>(null);
  const catalogAt = useRef(0);
  const [caseName, setCase] = useState<PendingAnalysis["case_name"]>("output_mismatch");
  const [intentChoice, setIntent] = useState<PendingAnalysis["intent"] | null>(null);
  const [pending, setPending] = useState<PendingAnalysis | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [uncertain, setUncertain] = useState(false);
  const [stoppingTask, setStoppingTask] = useState<string | null>(null);
  const [watched, setWatched] = useState<ReadonlySet<string>>(() => new Set());
  const [collapsed, setCollapsed] = useState<string | null>(null);
  const currentTask = useRef(detail?.summary.task_id); currentTask.current = detail?.summary.task_id;
  const stopGuard = useRef(new Set<string>());
  const stopping = stoppingTask === detail?.summary.task_id && detail?.summary.state.phase !== "finished";
  useEffect(() => {
    if (detail?.summary.task_id === stoppingTask && detail?.summary.state.phase === "finished") setStoppingTask(null);
  }, [detail?.summary.task_id, detail?.summary.state.phase, stoppingTask]);
  const [storageReady, setStorageReady] = useState(false);
  const [notice, setNotice] = useState<Notice>(null);
  const [catalogError, setCatalogError] = useState("");
  const [refresh, setRefresh] = useState(0);
  const writing = useRef(false);
  const alive = useRef(true);
  const requests = useRef(new Set<AbortController>());
  const select = useRef(onSelect); select.current = onSelect;
  const pendingChange = useRef(onPendingChange); pendingChange.current = onPendingChange;
  useEffect(() => { pendingChange.current?.(pending?.request_id ?? null); }, [pending?.request_id]);
  const watch = (id: string) => setWatched(old => old.has(id) ? old : new Set(old).add(id));
  useEffect(() => {
    alive.current = true;
    try {
      const raw = sessionStorage.getItem(ANALYSIS_PENDING_KEY);
      if (raw) {
        if (raw.length > 1024) throw new Error("invalid");
        const value = pendingAnalysisSchema.parse(JSON.parse(raw));
        setMemory(value.memory);
        setPending(value); setCase(value.case_name); setIntent(value.intent); setUncertain(true); watch(value.request_id);
        select.current(value.request_id);
        setNotice({ tone: "unknown", text: "无法确认上次提交是否已开始，正在读取这次提交的记录（只读取，不会重新运行）。" });
      }
      sessionStorage.setItem("awb-storage-probe", "1"); sessionStorage.removeItem("awb-storage-probe"); setStorageReady(true);
    } catch { setNotice({ tone: "fail", text: "浏览器禁用了会话存储，网页无法提交分析。请允许本站使用存储后刷新，或在终端运行。" }); }
    return () => { alive.current = false; requests.current.forEach(controller => controller.abort()); };
  }, []);
  // The shell's environment poller already reads the catalog; reuse its first answer so the two
  // readers don't collide on the server's owner lock. Later refreshes read it here directly.
  const envReady = !env.loading;
  const envCatalog = useRef(env.analysis); envCatalog.current = env.analysis;
  // Local follow-up polling (busy slot) is running; when it is not and the shell reports a busy slot, restart it.
  const following = useRef(false);
  // Adopt the shell's catalog only from a successful read that started after our own latest read.
  // A refused read (429) keeps the shell's old value and never advances analysisAt, so it cannot overwrite newer state.
  useEffect(() => {
    if (!env.analysis || !env.analysisAt || env.analysisAt <= catalogAt.current) return;
    setCatalog(env.analysis); catalogAt.current = env.analysisAt;
    if (env.analysis.slot.status === "busy" && !following.current) setRefresh(v => v + 1);
  }, [env.analysis, env.analysisAt]);
  useEffect(() => {
    if (!envReady) return;
    const controller = new AbortController(); let timer: ReturnType<typeof setTimeout> | undefined; let cancelVisible = () => {};
    let busyRetries = 0;
    const later = (ms: number) => { following.current = true; timer = setTimeout(() => { cancelVisible = whenVisible(() => void read()); }, ms); };
    async function read() {
      const startedAt = Date.now();
      try {
        const result = await loadAnalysisCatalog(controller.signal);
        if (controller.signal.aborted) return;
        if (startedAt >= catalogAt.current) { setCatalog(result); catalogAt.current = startedAt; }
        setCatalogError(""); busyRetries = 0;
        // Only a busy slot changes on its own; recovery needs a terminal command and is left to the shell's 15 s poll.
        if (result.slot.status === "busy") later(2000); else following.current = false;
      } catch (error) {
        if (!controller.signal.aborted) {
          if (error instanceof RunApiError && error.status === 429 && busyRetries++ < 5) {
            setCatalogError("服务正忙，1 秒后重试…");
            later(1000);
          } else { following.current = false; setCatalogError(runError(error)); }
        }
      }
    }
    const seeded = envCatalog.current;
    if (refresh === 0 && refreshKey === 0 && seeded) { if (seeded.slot.status === "busy") later(2000); }
    else void read();
    return () => { following.current = false; controller.abort(); clearTimeout(timer); cancelVisible(); };
  }, [refresh, refreshKey, envReady]);
  useEffect(() => {
    if (!detail) return;
    if (detail.summary.state.phase !== "finished") watch(detail.summary.task_id);
    if (detail.summary.task_id !== pending?.request_id) return;
    setUncertain(false); setNotice(null);
    if (detail.summary.state.phase === "finished") {
      try { sessionStorage.removeItem(ANALYSIS_PENDING_KEY); setPending(null); }
      catch { setStorageReady(false); setNotice({ tone: "neutral", text: "分析已结束。刷新页面即可清除残留的提交信息。" }); }
      setStoppingTask(null); setRefresh(v => v + 1);
    }
  }, [detail, pending?.request_id]);

  const running = !!detail && detail.summary.state.phase !== "finished";
  const cases = catalog?.cases ?? caseFallback;
  const intents = catalog?.intents ?? intentFallback;
  const modelReady = catalog?.model.configured ?? true;
  const intent = intentChoice ?? (catalog && !catalog.model.configured ? "check_only" : "diagnose");
  const intentNeedsModel = intents.find(item => item.id === intent)?.requires_model ?? intent !== "check_only";
  const checkLabel = intents.find(item => item.id === "check_only")?.label ?? intentLabels.check_only;

  async function submit(existing?: PendingAnalysis) {
    if (writing.current || !storageReady || (!existing && (pending || !catalog || catalogError || catalog.slot.status !== "available"))) return;
    const request = existing ?? { request_id: crypto.randomUUID(), case_name: caseName, intent, ...(memory && intent !== "check_only" ? { memory } : {}) };
    try { sessionStorage.setItem(ANALYSIS_PENDING_KEY, JSON.stringify(request)); }
    catch { setStorageReady(false); setNotice({ tone: "fail", text: "分析未开始：浏览器无法保存请求 ID。请允许本站使用存储后重试。" }); return; }
    writing.current = true; setSubmitting(true); setPending(request); setNotice(null); watch(request.request_id); setCollapsed(null);
    const controller = new AbortController(); requests.current.add(controller);
    try {
      const result = await startAnalysis(request, controller.signal);
      if (!alive.current) return;
      setUncertain(false); setStoppingTask(result.cancel_requested ? result.task_id : null); onSelect(result.task_id); onRefresh();
      toast.show({ tone: "pass", message: result.reused ? "这次提交已在运行，没有重复开始。" : "分析已开始。" });
    } catch (error) {
      if (!alive.current) return;
      // A retry can be rejected while the original HTTP owner still admits it.
      // Keep its identity until the original task's outcome is actually known.
      if (!existing && error instanceof RunApiError && [400, 403, 409, 410, 429].includes(error.status ?? 0)) {
        try { sessionStorage.removeItem(ANALYSIS_PENDING_KEY); setPending(null); } catch { setStorageReady(false); }
        setUncertain(false);
        setNotice({ tone: "fail", text: error.status === 429 ? "已有一个分析在运行，同一时间只能运行一个。" : `服务拒绝了这次提交（HTTP ${error.status}）。请刷新页面后重试。` });
      } else { setUncertain(true); onSelect(request.request_id); setNotice({ tone: "unknown", text: "连接中断，无法确认分析是否已开始。可以用同一请求 ID 重新提交，不会重复运行；不要新建分析。" }); }
    } finally { requests.current.delete(controller); writing.current = false; if (alive.current) { setSubmitting(false); setRefresh(v => v + 1); } }
  }
  async function abandon() {
    if (!pending || submitting) return;
    const id = pending.request_id;
    const ok = await confirm({ title: "放弃这次提交？", message: "网页将不再跟踪这个请求 ID，也不会再用它提交。这不代表分析没有执行：如果上次提交其实已送达，记录仍会出现在分析列表中。", confirmLabel: "放弃" });
    if (!ok || !alive.current) return;
    try { sessionStorage.removeItem(ANALYSIS_PENDING_KEY); } catch { setStorageReady(false); }
    setPending(previous => previous?.request_id === id ? null : previous); setUncertain(false); setNotice(null); setRefresh(v => v + 1);
  }
  async function stop() {
    if (!detail || stopping || stopGuard.current.has(detail.summary.task_id)) return;
    const taskId = detail.summary.task_id;
    const ok = await confirm({ title: "停止这次分析？", message: "当前的模型或工具调用结束后停止。已记录的事件和用量会保留，停止后不能继续这次分析。", confirmLabel: "停止", danger: true });
    if (!ok || !alive.current || currentTask.current !== taskId || stopGuard.current.has(taskId)) return;
    const controller = new AbortController(); requests.current.add(controller); stopGuard.current.add(taskId); setStoppingTask(taskId);
    try {
      const result = await stopAnalysis(taskId, controller.signal);
      if (alive.current && currentTask.current === taskId) {
        toast.show({ message: result.status === "already_finished" ? "分析已结束，正在刷新。" : "正在停止，等待当前模型或工具调用结束。" });
        onRefresh();
      }
    } catch {
      if (alive.current) {
        setStoppingTask(previous => previous === taskId ? null : previous);
        if (currentTask.current === taskId) toast.show({ tone: "fail", message: "无法确认是否已停止。请刷新查看，必要时再点一次「停止」。" });
      }
    }
    finally { requests.current.delete(controller); stopGuard.current.delete(taskId); }
  }

  const stoppable = running && (pending?.request_id === detail.summary.task_id || catalog?.slot.task_id === detail.summary.task_id);
  const entries: TerminalEntry[] = (detail?.events ?? []).map(item => {
    const { text, error } = describeEvent(item.event);
    return { id: String(item.revision), at: toDate(item.created_at).getTime(), source: item.event.role ? roleLabels[item.event.role] : "系统", message: text, level: error ? "error" : undefined };
  });
  const followed = detail && (running || watched.has(detail.summary.task_id)) && collapsed !== detail.summary.task_id;
  const expanded = !!followed || submitting || (!!pending && !detail);
  const summary = detail?.summary;
  const elapsed = summary ? formatDuration(durationBetween(summary.started_at, summary.finished_at ?? new Date())) : null;

  const blocked = !storageReady ? null
    : catalogError ? <p className="an-launch-reason" data-tone="fail" role="alert">{catalogError}</p>
    : catalog?.slot.status === "recovery_required" ? <p className="an-launch-reason" data-tone="fail">上次分析意外中断，需要先核对状态才能开始新分析。<Link className="link" href="/docs/27-local-development-guide#运行记录与恢复">如何核对</Link>{catalog.slot.task_id && <button type="button" className="link an-inline-button" onClick={() => onSelect(catalog.slot.task_id!)}>查看中断的分析</button>}</p>
    : catalog?.slot.status === "busy" ? catalog.slot.task_id === detail?.summary.task_id && running
      ? <p className="an-launch-reason">选中的分析正在运行，结束后才能开始新的分析。</p>
      : <p className="an-launch-reason">已有分析在运行，同一时间只能运行一个。<button type="button" className="link an-inline-button" onClick={() => onSelect(catalog.slot.task_id!)}>查看当前分析</button></p>
    : catalog && intentNeedsModel && !modelReady ? <p className="an-launch-reason" data-tone="unknown">{catalog.model.reason === "MODEL_NOT_CONFIGURED" ? "本机服务未配置模型" : modelReasonMessage(catalog.model.reason).replace(/。$/, "")}，目前只能运行「{checkLabel}」。<Link className="link" href="/workspace/connections#model">配置模型</Link></p>
    : null;
  const disabled = !storageReady || !catalog || !!catalogError || submitting || !!pending || catalog.slot.status !== "available" || (intentNeedsModel && !catalog.model.configured);

  return <>
    {detail && <TaskMemoryUsage kind="analysis" taskId={detail.summary.task_id} revision={detail.summary.summary_revision} agentTokens={detail.summary.usage.known_input_tokens + detail.summary.usage.known_output_tokens} agentComplete={detail.summary.usage.usage_complete} />}
    <section className="pane an-launch" aria-labelledby="analysis-launch-heading" id="analysis-launch">
      <h2 id="analysis-launch-heading" className="sr-only">新建分析</h2>
      <div className="an-launch-row">
        <div className="field an-launch-field"><label htmlFor="analysis-case">案例</label>
          <select id="analysis-case" className="select" value={caseName} disabled={submitting || !!pending} onChange={e => setCase(e.target.value as PendingAnalysis["case_name"])}>
            {cases.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}
          </select></div>
        <div className="field an-launch-field"><label htmlFor="analysis-intent">目标</label>
          <select id="analysis-intent" className="select" value={intent} disabled={submitting || !!pending} onChange={e => { setIntent(e.target.value as PendingAnalysis["intent"]); if (e.target.value === "check_only") setMemory(undefined); }}>
            {intents.map(item => <option key={item.id} value={item.id} disabled={item.requires_model && !modelReady}>{item.label}{item.requires_model && !modelReady ? "（需配置模型）" : ""}</option>)}
          </select></div>
        <button type="button" className="btn btn-primary an-launch-start" disabled={disabled} aria-describedby="analysis-launch-reason" aria-busy={submitting || undefined} onClick={() => void submit()}>
          <Play size={14} aria-hidden="true" />{submitting ? "正在提交…" : "开始分析"}</button>
        <button type="button" id="analysis-cli-toggle" className="btn btn-ghost btn-sm an-cli-toggle" aria-expanded={cliOpen} aria-controls="analysis-cli" onClick={() => onCliOpenChange(!cliOpen)}>
          在终端运行<ChevronDown size={13} aria-hidden="true" /></button>
      </div>
      <TaskMemoryChoice value={memory} onChange={setMemory} disabled={submitting || !!pending} supported={intent !== "check_only"} />
      <div id="analysis-launch-reason">{blocked ?? <p className="an-launch-reason">{intentHints[intent]}</p>}</div>
      {notice && !(uncertain && pendingMissing) && <p className="an-launch-notice" data-tone={notice.tone} role="status">{notice.text}</p>}
      {uncertain && pending && <Callout tone="unknown" title={pendingMissing ? "服务端没有这次提交的记录" : "提交结果待确认"} actions={<>
        <button type="button" className="btn btn-sm" disabled={submitting} onClick={() => void submit(pending)}>用同一 ID 重新提交</button>
        <button type="button" className="btn btn-sm btn-ghost" disabled={submitting} onClick={() => void abandon()}>放弃这次提交</button>
      </>}>
        {pendingMissing ? "上次提交可能没有送达。" : ""}重新提交时，如果上次没有送达，这次会开始分析；如果已经送达，只会找回原分析，不会重复运行。
      </Callout>}
      <div id="analysis-cli" className="an-cli" hidden={!cliOpen}>
        <p className="muted small">也可以在终端运行，模型密钥从环境变量读取。</p>
        <ol>
          <li><span>首次使用前初始化记录库（不调用模型）</span><CommandLine value={ANALYSIS_INIT_COMMAND} label="初始化命令" /></li>
          <li><span>按当前选择运行分析</span><CommandLine value={analysisCommand({ case_name: caseName, intent })} label="分析命令" /></li>
          <li><span>分析中断后核对状态，不会自动继续</span><CommandLine value={reconcileCommand(detail?.summary.task_id)} label="核对命令" /></li>
        </ol>
        <Link className="link small" href="/docs/27-local-development-guide#模型分析与配置">模型配置说明</Link>
      </div>
    </section>
    <ExecutionTerminal expanded={expanded}
      idle={catalog?.slot.status === "busy" ? "另一个分析正在运行，选中它即可查看实时事件。" : "未开始。开始分析后，事件会显示在这里。"}
      heading={summary ? <><strong>{intentLabels[summary.state.intent]}</strong><StatusBadge tone={analysisTone(summary)}>{stopping ? "正在停止…" : analysisStatus(summary)}</StatusBadge></> : <><strong>{intentLabels[pending?.intent ?? intent]}</strong>{submitting ? <StatusBadge tone="running">正在提交…</StatusBadge> : <StatusBadge tone="unknown">{pendingMissing ? "服务端无记录" : "待确认"}</StatusBadge>}</>}
      meta={summary && <span className="tabular">用时 {elapsed} · 模型 {formatNumber(summary.usage.model_calls)} 次 · 工具 {formatNumber(summary.usage.tool_calls)} 次</span>}
      actions={<>
        {running && stoppable && <button type="button" className="btn btn-sm btn-danger" disabled={stopping} aria-busy={stopping || undefined} onClick={() => void stop()}><Square size={11} aria-hidden="true" />{stopping ? "正在停止…" : "停止"}</button>}
        {summary && !running && <button type="button" className="btn btn-ghost btn-sm" onClick={() => setCollapsed(summary.task_id)}>收起</button>}
      </>}
      entries={entries} />
  </>;
}
