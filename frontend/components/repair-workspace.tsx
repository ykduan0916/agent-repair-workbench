"use client";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { AlertTriangle, BookOpen, CheckCircle2, CircleDashed, FileX2, GitCompareArrows, RefreshCw, XCircle } from "lucide-react";
import { PageHeader } from "./ui/page-header";
import { StatusBadge, StatusDot, type Tone } from "./ui/status";
import { CopyButton, CopyId } from "./ui/copy-id";
import { RelativeTime } from "./ui/relative-time";
import { Skeleton, SkeletonRows } from "./ui/skeleton";
import { EmptyState } from "./ui/empty-state";
import { Callout } from "./ui/callout";
import { LinkTabs } from "./ui/tabs";
import CapabilityBadge from "./capability-badge";
import { DiffViewer } from "./repairs/diff-viewer";
import { RegressionTable } from "./repairs/regression-table";
import RepairRunControls from "./repairs/run-controls";
import { TechDetails } from "./repairs/tech-details";
import { ApprovalPanel } from "./repairs/approval-panel";
import {
  REPAIR_COMMAND, REPAIR_DOCS, caseLabels, comparisonOutcomes, errorLabels, loadDiff, loadRepair, loadRepairs, modeLabels, outcomes, phaseLabels, reasonText,
  type Candidate, type Comparison, type RepairDetail, type RepairDiff, type RepairTask,
} from "../lib/repairs";
import { runError } from "../lib/runs";
import { repairSummary } from "../lib/tones";
import { durationBetween, formatDateTime, formatDuration } from "../lib/format";
import { modules } from "../lib/modules";

type Tab = "diff" | "regression";
type Status = { tone: Tone; label: string };
type Verdict = Status & { text: string; pass?: boolean };
const UUID = /^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/;
const BASE = "/workspace/repairs";

function href(task: string, tab: Tab, candidate?: string | null) {
  const query = new URLSearchParams();
  if (task) query.set("task", task);
  if (candidate) query.set("candidate", candidate);
  if (tab === "regression") query.set("tab", "regression");
  const value = query.toString();
  return value ? `${BASE}?${value}` : BASE;
}
const errorText = (code: keyof typeof errorLabels | null) => code ? <>{errorLabels[code]}（<code translate="no">{code}</code>）</> : null;
const duration = (task: RepairTask) => task.finished_at ? formatDuration(durationBetween(task.started_at, task.finished_at)) : "进行中";

/** Recorded outcome from the list summary; a recorded pass stays neutral until the detail re-verifies it. */
const summaryStatus = (task: RepairTask): Status => repairSummary(task);
/** A recorded pass only stands while at least one candidate still verifies against the current versions. */
function taskStatus(detail: RepairDetail, withdrawn: ReadonlySet<string> = new Set()): Status {
  if (detail.task.recorded_outcome !== "candidate_verified") return summaryStatus(detail.task);
  const verified = detail.comparisons.some(c => c.verification === "verified" && c.current_outcome === "candidate_verified" && !withdrawn.has(c.candidate_id));
  return verified ? { tone: "pass", label: outcomes.candidate_verified } : { tone: "unknown", label: "结果已过期" };
}
function candidateVerdict(candidate: Candidate, comparison: Comparison | undefined, diffError: string, running = false): Verdict {
  if (diffError) return { tone: "unknown", label: "无法核对代码差异", text: "没有读取到与记录一致的代码差异，暂不显示通过结果。" };
  if (!comparison) {
    if (candidate.status === "failed" || candidate.status === "outcome_unknown") return { tone: candidate.status === "failed" ? "fail" : "unknown", label: candidate.status === "failed" ? "候选生成失败" : "候选结果未知", text: candidate.error_code ? `原因：${errorLabels[candidate.error_code]}。没有运行回归。` : "没有运行回归。" };
    if (candidate.status === "preparing") return { tone: "running", label: "候选生成中", text: "候选还在生成，完成后会运行回归。" };
    if (running) return { tone: "running", label: "等待回归结果", text: "回归完成后会显示结果。" };
    return { tone: "neutral", label: "尚无回归结果", text: "这个候选没有回归记录，因此不能算作通过。" };
  }
  if (comparison.verification === "unavailable") return { tone: "unknown", label: "结果已过期", text: "候选文件或检查项在记录后有改动，记录时的结果已撤回。请在上方开始一次新修复。" };
  if (comparison.verification === "not_finished") return { tone: "unknown", label: "回归未完成", text: "回归没有执行完，没有可用的结果。" };
  const outcome = comparison.current_outcome!;
  const text = comparison.reason ? reasonText[comparison.reason] : "";
  if (outcome === "candidate_verified") return { tone: "pass", label: comparisonOutcomes[outcome], text, pass: true };
  const tone = ({ rejected: "fail", inconclusive: "unknown", cancelled: "neutral", outcome_unknown: "unknown" } as const)[outcome];
  return { tone, label: comparisonOutcomes[outcome], text };
}
const verdictIcons = { pass: CheckCircle2, fail: XCircle, unknown: AlertTriangle, neutral: CircleDashed, running: CircleDashed, accent: CircleDashed };

function Header({ actions }: { actions?: React.ReactNode }) {
  return <PageHeader title={modules.repairs.title} description={modules.repairs.description} badge={<CapabilityBadge section="repairs" />} actions={actions} />;
}
function DetailSkeleton() {
  return <div className="rp-detail-skeleton" role="status" aria-busy="true">
    <span className="sr-only">正在读取修复记录…</span>
    <Skeleton width="38%" height={20} /><Skeleton width="56%" height={12} />
    <Skeleton height={64} /><Skeleton width="30%" height={14} /><Skeleton height={220} />
  </div>;
}
function DiffSkeleton() {
  return <div className="rp-diff" role="status" aria-busy="true">
    <span className="sr-only">正在读取代码差异…</span>
    <div className="rp-diff-head"><Skeleton width={180} height={14} /></div>
    <div className="rp-diff-skeleton">{[72, 48, 64, 80, 36, 58].map((w, i) => <Skeleton key={i} width={`${w}%`} height={12} />)}</div>
  </div>;
}
function Command() {
  return <div className="code-block rp-command" role="group" aria-label="修复命令">
    <pre translate="no">{REPAIR_COMMAND}</pre>
    <CopyButton value={REPAIR_COMMAND} label="修复命令" />
  </div>;
}

export default function RepairWorkspace(_props: { section?: string }) {
  return <Suspense fallback={<><Header /><div className="rp-layout"><div className="pane rp-list"><SkeletonRows rows={4} label="正在读取修复记录…" /></div><DetailSkeleton /></div></>}>
    <Workspace />
  </Suspense>;
}

function Workspace() {
  const params = useSearchParams();
  const router = useRouter();
  const tab: Tab = params.get("tab") === "regression" ? "regression" : "diff";
  const raw = params.get("task") ?? params.get("repair") ?? "";
  const linked = UUID.test(raw) ? raw : "";
  const requestedCandidate = params.get("candidate");
  const [items, setItems] = useState<RepairTask[] | null>(null);
  const [listError, setListError] = useState("");
  const [listLoading, setListLoading] = useState(true);
  const [refresh, setRefresh] = useState(0);
  const [detail, setDetail] = useState<RepairDetail | null>(null);
  const [detailError, setDetailError] = useState("");
  const [detailLoading, setDetailLoading] = useState(false);
  const [diffState, setDiffState] = useState<{ key: string; diff?: RepairDiff; error?: string } | null>(null);
  const [retry, setRetry] = useState(0);
  // Diff checks that failed, keyed by task:candidate:sha. They keep the pass withdrawn on every tab until the record is reloaded.
  const [diffFailures, setDiffFailures] = useState<Record<string, string>>({});
  const detailRef = useRef<HTMLElement>(null);
  const scrollOnLoad = useRef(false);
  const selected = linked || items?.[0]?.task_id || "";
  const reload = useCallback(() => { setDiffFailures({}); setRefresh(value => value + 1); }, []);

  // Canonical URL: legacy ?repair= becomes ?task=, and the newest record is selected when none is linked.
  useEffect(() => {
    if (params.has("repair") || (!linked && selected)) router.replace(href(selected, tab, requestedCandidate), { scroll: false });
  }, [params, linked, selected, tab, requestedCandidate, router]);

  useEffect(() => {
    const controller = new AbortController();
    setListLoading(true); setListError("");
    loadRepairs(controller.signal)
      .then(values => { if (!controller.signal.aborted) setItems(values); })
      .catch(error => { if (!controller.signal.aborted) setListError(runError(error)); })
      .finally(() => { if (!controller.signal.aborted) setListLoading(false); });
    return () => controller.abort();
  }, [refresh]);

  useEffect(() => {
    setDetail(previous => previous?.task.task_id === selected ? previous : null);
    setDetailError("");
    if (!selected) return;
    const controller = new AbortController();
    setDetailLoading(true);
    loadRepair(selected, controller.signal)
      .then(value => {
        if (controller.signal.aborted) return;
        setDetail(value);
        setItems(list => list?.map(task => task.task_id === value.task.task_id ? value.task : task) ?? list);
      })
      .catch(error => { if (!controller.signal.aborted) { setDetail(null); setDetailError(runError(error)); } })
      .finally(() => { if (!controller.signal.aborted) setDetailLoading(false); });
    return () => controller.abort();
  }, [selected, refresh]);

  const current = detail?.task.task_id === selected ? detail : null;
  const candidates = current?.candidates ?? [];
  const matched = candidates.find(c => c.candidate_id === requestedCandidate);
  const candidate = matched ?? candidates[0];
  const comparison = current?.comparisons.find(c => c.candidate_id === candidate?.candidate_id);
  const diffKey = current && candidate ? `${current.task.task_id}:${candidate.candidate_id}:${current.task.sha256}` : "";

  useEffect(() => {
    setDiffState(null);
    if (!current || !candidate || tab !== "diff" || candidate.snapshot_status !== "verified") return;
    const key = `${current.task.task_id}:${candidate.candidate_id}:${current.task.sha256}`;
    const controller = new AbortController();
    loadDiff(current.task.task_id, candidate, controller.signal)
      .then(value => {
        if (controller.signal.aborted) return;
        setDiffState({ key, diff: value });
        setDiffFailures(({ [key]: _cleared, ...rest }) => rest);
      })
      .catch(error => {
        if (controller.signal.aborted) return;
        const message = runError(error);
        setDiffState({ key, error: message });
        setDiffFailures(failures => ({ ...failures, [key]: message }));
      });
    return () => controller.abort();
  }, [current, candidate, tab, retry]);

  useEffect(() => {
    if (!current || !scrollOnLoad.current) return;
    scrollOnLoad.current = false;
    if (window.matchMedia("(max-width: 1100px)").matches) detailRef.current?.scrollIntoView({ block: "start" });
  }, [current]);

  const ownDiff = diffState?.key === diffKey ? diffState : null;
  const validDiff = ownDiff?.diff && current && candidate && ownDiff.diff.task_id === current.task.task_id && ownDiff.diff.candidate_id === candidate.candidate_id ? ownDiff.diff : null;
  const diffError = ownDiff?.error ?? diffFailures[diffKey] ?? "";
  const busy = listLoading || detailLoading;
  const empty = items !== null && items.length === 0 && !linked;
  const withdrawn = new Set(current?.candidates.filter(item => diffFailures[`${current.task.task_id}:${item.candidate_id}:${current.task.sha256}`]).map(item => item.candidate_id));
  const rowStatus = (task: RepairTask) => current && current.task.task_id === task.task_id ? taskStatus(current, withdrawn) : summaryStatus(task);

  const actions = items && items.length > 0 ? <>
    <CopyButton value={REPAIR_COMMAND} label="修复命令" className="btn">复制修复命令</CopyButton>
    <button type="button" className="btn" onClick={reload} disabled={busy} aria-busy={busy || undefined}><RefreshCw size={14} aria-hidden="true" />刷新</button>
  </> : undefined;

  const controls = <RepairRunControls selected={selected} onSelect={id => router.replace(href(id, tab), { scroll: false })} onRefresh={reload} />;

  if (empty && !listError) return <>
    <Header />
    {controls}
    <div className="pane">
      <div className="empty-state rp-empty">
        <span className="empty-icon"><GitCompareArrows size={18} aria-hidden="true" /></span>
        <h2>还没有修复记录</h2>
        <p>选择上方内置案例开始修复。候选生成后，在这里审阅代码差异与回归结果。</p>
        <details><summary>也可以在终端运行</summary><Command /></details>
        <div className="empty-actions">
          <button type="button" className="btn btn-sm" onClick={reload} disabled={busy}><RefreshCw size={13} aria-hidden="true" />刷新</button>
          <Link className="btn btn-sm btn-ghost" href={REPAIR_DOCS}><BookOpen size={13} aria-hidden="true" />查看使用指南</Link>
        </div>
      </div>
    </div>
  </>;

  return <>
    <Header actions={actions} />
    {controls}
    {listError && <div className="rp-alert"><Callout tone="fail" role="alert" title="无法读取修复记录" actions={<button type="button" className="btn btn-sm" onClick={reload}>重试读取列表</button>}>{listError}</Callout></div>}
    <div className="rp-layout">
      <aside className="pane rp-list" aria-labelledby="rp-list-title" aria-busy={listLoading}>
        <div className="pane-head"><h2 id="rp-list-title">修复记录</h2>{items && <span className="subtle small tabular">最近 {items.length} 条</span>}</div>
        {items === null ? (listError ? <p className="rp-list-note subtle small">列表未读取。</p> : <SkeletonRows rows={4} label="正在读取修复记录…" />)
          : <ul className="rp-tasks">
            {linked && !items.some(task => task.task_id === linked) && <li className="rp-list-note subtle small">链接指定的记录不在最近 {items.length} 条中。</li>}
            {items.map(task => {
              const status = rowStatus(task);
              const active = task.task_id === selected;
              return <li key={task.task_id} className="rp-task" data-active={active || undefined}>
                <div className="rp-task-top">
                  <Link className="rp-task-link" href={href(task.task_id, tab)} scroll={false} aria-current={active ? "true" : undefined} onClick={() => { scrollOnLoad.current = true; }}>
                    {caseLabels[task.case_id]}<span className="sr-only">，{status.label}，开始于 {formatDateTime(task.started_at)}</span>
                  </Link>
                  <StatusBadge tone={status.tone}>{status.label}</StatusBadge>
                </div>
                <div className="rp-task-meta">
                  <span>{modeLabels[task.mode]}</span><span aria-hidden="true">·</span><RelativeTime value={task.started_at} />
                  <span aria-hidden="true">·</span><span>{duration(task)}</span>
                  <span aria-hidden="true">·</span><span>{task.candidate_count} 个候选</span>
                  <CopyId value={task.task_id} label="修复记录 ID" />
                </div>
              </li>;
            })}
          </ul>}
      </aside>
      <section className="rp-detail" ref={detailRef} aria-label="修复记录详情" aria-busy={detailLoading}>
        {detailError && <Callout tone="fail" role="alert" title="无法读取这条修复记录" actions={<button type="button" className="btn btn-sm" onClick={reload}>重试读取详情</button>}>{detailError}</Callout>}
        {current ? <Detail
          detail={current} withdrawn={withdrawn} tab={tab} candidate={candidate} comparison={comparison}
          notice={!!requestedCandidate && !matched && candidates.length > 0}
          diff={validDiff} diffError={diffError} diffLoading={tab === "diff" && candidate?.snapshot_status === "verified" && !ownDiff && !diffError}
          onRetryDiff={() => setRetry(value => value + 1)} onReload={reload} status={taskStatus(current, withdrawn)}
        /> : !detailError && (detailLoading || items === null) && !listError ? <DetailSkeleton /> : null}
      </section>
    </div>
  </>;
}

function Detail({ detail, withdrawn, tab, candidate, comparison, notice, diff, diffError, diffLoading, onRetryDiff, onReload, status }: {
  detail: RepairDetail; withdrawn: ReadonlySet<string>; tab: Tab; candidate?: Candidate; comparison?: Comparison; notice: boolean;
  diff: RepairDiff | null; diffError: string; diffLoading: boolean; onRetryDiff: () => void; onReload: () => void; status: Status;
}) {
  const { task } = detail;
  const running = task.phase !== "finished";
  const verdict = candidate ? candidateVerdict(candidate, comparison, diffError, running) : null;
  const VerdictIcon = verdict ? verdictIcons[verdict.tone] : CircleDashed;
  return <>
    <header className="rp-detail-head">
      <div className="rp-detail-title">
        <h2>{caseLabels[task.case_id]}</h2>
        <StatusBadge tone={status.tone}>{status.label}</StatusBadge>
      </div>
      <p className="rp-detail-meta">
        <span>{modeLabels[task.mode]}</span>
        <span>开始于 <RelativeTime value={task.started_at} /></span>
        {task.finished_at && <span>用时 {duration(task)}</span>}
        <span>{task.candidate_count} 个候选</span>
        <CopyId value={task.task_id} label="修复记录 ID" />
      </p>
    </header>

    {task.phase !== "finished" && <Callout title={`修复进行中：${phaseLabels[task.phase]}`}>网页发起的任务会自动更新；终端任务可点击「刷新」读取。</Callout>}

    {!candidate ? <div className="rp-no-candidate">
      <EmptyState icon={FileX2} title="这次修复没有生成候选">
        {task.error_code ? <>原因：{errorText(task.error_code)}。</> : task.phase === "finished" ? "没有记录到可用的候选。" : "候选生成后会显示在这里。"}
      </EmptyState>
    </div> : <>
      {detail.candidates.length > 1 && <nav className="rp-candidates" aria-label="选择候选">
        {detail.candidates.map(item => {
          const tone = candidateVerdict(item, detail.comparisons.find(c => c.candidate_id === item.candidate_id), withdrawn.has(item.candidate_id) ? "unverified" : "", running).tone;
          return <Link key={item.candidate_id} className="rp-candidate" href={href(task.task_id, tab, item.candidate_id)} scroll={false} aria-current={item.candidate_id === candidate.candidate_id ? "true" : undefined}>
            <StatusDot tone={tone} />候选 {item.sequence}
          </Link>;
        })}
      </nav>}
      {notice && <Callout>链接中的候选不属于这条修复记录，已改为显示候选 {candidate.sequence}。</Callout>}

      {verdict && <div className="rp-verdict" data-tone={verdict.tone}>
        <VerdictIcon size={18} aria-hidden="true" />
        <div>
          <h3><span className="rp-verdict-kicker">候选 {candidate.sequence}</span>{verdict.label}</h3>
          <p>{verdict.text}</p>
          {verdict.pass && <p className="rp-verdict-note">候选已通过冻结回归，仍需人工决定；接受不会写回项目。</p>}
        </div>
      </div>}

      <LinkTabs label="候选内容" active={tab} items={[
        { key: "diff", label: "代码差异", href: href(task.task_id, "diff", candidate.candidate_id) },
        { key: "regression", label: "回归结果", href: href(task.task_id, "regression", candidate.candidate_id) },
      ]} />

      {tab === "diff" ? <div className="rp-tab-body">
        {candidate.snapshot_status !== "verified" && comparison?.verification === "unavailable"
          ? <p className="rp-tab-empty">没有可显示的代码差异。</p>
          : candidate.snapshot_status !== "verified"
          ? <Callout tone="unknown" title={candidate.snapshot_status === "not_created" ? "这个候选没有生成文件" : "候选文件与记录不一致"}
            actions={<button type="button" className="btn btn-sm" onClick={onReload}>刷新</button>}>
            {candidate.snapshot_status === "not_created"
              ? (candidate.error_code ? <>原因：{errorText(candidate.error_code)}。</> : "没有可显示的代码差异。")
              : "请用 CLI 重新运行修复命令后刷新。"}
          </Callout>
          : diffError ? <Callout tone="fail" role="alert" title="无法读取代码差异" actions={<><button type="button" className="btn btn-sm" onClick={onRetryDiff}>重试</button><button type="button" className="btn btn-sm btn-ghost" onClick={onReload}>刷新记录</button></>}>{diffError}</Callout>
          : diffLoading || !diff ? <DiffSkeleton />
          : <DiffViewer patch={diff.diff.unified_diff} fallbackPath={diff.diff.path} verified />}
      </div> : <div className="rp-tab-body">
        {diffError && <Callout tone="unknown" title="代码差异未通过核对">读取代码差异时没有得到与记录一致的结果，下面的逐项结果仅供排查，不作为通过依据。</Callout>}
        {comparison && comparison.verification !== "unavailable" && comparison.slots.length > 0
          ? <RegressionTable comparison={comparison} />
          : <p className="rp-tab-empty">没有可显示的逐项结果。</p>}
      </div>}
      {candidate.snapshot_status === "verified" && <ApprovalPanel taskId={task.task_id} candidate={candidate} comparison={comparison} onRefresh={onReload} />}
    </>}

    <TechDetails detail={detail} candidate={candidate} comparison={comparison} diff={diff} />
  </>;
}
