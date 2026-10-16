"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Check, Download, RefreshCw, X } from "lucide-react";
import { Callout } from "../ui/callout";
import { RunApiError } from "../../lib/runs";
import {
  clearPendingDecision,
  downloadDecision,
  loadDecision,
  loadPendingDecision,
  saveDecision,
  savePendingDecision,
  type DecisionChoice,
  type DecisionReceipt,
  type PendingDecision,
} from "../../lib/decisions";
import type { Candidate, Comparison } from "../../lib/repairs";

function actionError(error: unknown, fallback: string) {
  if (error instanceof RunApiError && error.status === 409) {
    return "审批状态已经变化，先刷新当前记录，再决定是否继续。";
  }
  return error instanceof Error ? error.message : fallback;
}

function samePending(value: PendingDecision | null, receipt: DecisionReceipt | null) {
  return !!value && !!receipt
    && value.expected_revision === receipt.revision
    && value.expected_candidate_sha256 === receipt.candidate_sha256
    && value.expected_comparison_sha256 === receipt.comparison_sha256;
}

export function ApprovalPanel({ taskId, candidate, comparison, onRefresh }: { taskId: string; candidate: Candidate; comparison?: Comparison; onRefresh: () => void }) {
  const [receipt, setReceipt] = useState<DecisionReceipt | null>(null);
  const [pending, setPending] = useState<PendingDecision | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [refreshVersion, setRefreshVersion] = useState(0);
  const alive = useRef(true);

  const refreshDecision = useCallback(() => setRefreshVersion(value => value + 1), []);

  useEffect(() => {
    alive.current = true;
    const controller = new AbortController();
    setError("");
    setReceipt(null);
    const stored = loadPendingDecision(taskId, candidate.candidate_id);
    setPending(stored);
    loadDecision(taskId, candidate.candidate_id, controller.signal)
      .then(value => {
        if (!alive.current) return;
        setReceipt(value);
        // A successful GET is the recovery point after a lost POST response.
        if (value.decision && stored && (value.decision.request_id === stored.request_id || value.revision > stored.expected_revision)) {
          clearPendingDecision(taskId, candidate.candidate_id);
          setPending(null);
        }
      })
      .catch(value => {
        if (alive.current && !controller.signal.aborted) {
          setError(actionError(value, "审批记录暂不可用，请刷新后重试。"));
        }
      });
    return () => { alive.current = false; controller.abort(); };
  }, [taskId, candidate.candidate_id, candidate.snapshot_sha256, comparison?.comparison_id, refreshVersion]);

  const decision = receipt?.decision;
  const currentValid = receipt?.current_valid === true;
  const canAccept = receipt?.can_accept === true;
  const canReject = receipt?.can_reject === true;

  async function decide(choice: DecisionChoice) {
    if (!receipt || busy || (choice === "accepted" ? !canAccept : !canReject)) return;
    const existing = samePending(pending, receipt) && pending?.decision === choice ? pending : null;
    const request: PendingDecision = existing ?? {
      request_id: crypto.randomUUID(),
      decision: choice,
      expected_revision: receipt.revision,
      expected_candidate_sha256: receipt.candidate_sha256 ?? "",
      expected_comparison_sha256: receipt.comparison_sha256 ?? "",
    };
    if (!request.expected_candidate_sha256 || !request.expected_comparison_sha256) {
      setError("当前证据没有完整版本摘要，不能记录人工决定。请刷新后重试。");
      return;
    }
    if (!existing && !savePendingDecision(taskId, candidate.candidate_id, request)) {
      setError("浏览器无法暂存这次请求，未发送审批。请允许本地会话存储后重试。");
      return;
    }
    setPending(request);
    setBusy(true);
    setError("");
    const controller = new AbortController();
    try {
      const value = await saveDecision(taskId, candidate.candidate_id, request, controller.signal);
      if (alive.current) {
        clearPendingDecision(taskId, candidate.candidate_id);
        setPending(null);
        setReceipt(value);
        onRefresh();
      }
    } catch (value) {
      if (alive.current) setError(actionError(value, "审批没有保存，请刷新状态后重试。"));
    } finally {
      controller.abort();
      if (alive.current) setBusy(false);
    }
  }

  async function download() {
    if (!receipt?.can_download || busy) return;
    setBusy(true);
    setError("");
    const controller = new AbortController();
    try {
      await downloadDecision(taskId, candidate.candidate_id, controller.signal);
    } catch (value) {
      if (alive.current) setError(actionError(value, "补丁下载失败，请刷新后重试。"));
    } finally {
      controller.abort();
      if (alive.current) setBusy(false);
    }
  }

  const decisionStale = !!decision && !currentValid;
  const retryPending = !!pending && !decision && samePending(pending, receipt);

  return <section className="rp-approval pane" aria-label="人工审批" aria-busy={busy || undefined}>
    <div className="rp-approval-head">
      <div><h3>人工决定</h3><p className="muted small">决定只记录当前候选，不会写回项目。</p></div>
      {decision && <span className={`rp-decision rp-decision-${decisionStale ? "stale" : decision.decision}`}>
        {decisionStale ? `历史${decision.decision === "accepted" ? "已接受" : "已拒绝"}` : decision.decision === "accepted" ? "已接受" : "已拒绝"}
      </span>}
    </div>
    {decisionStale && <Callout tone="unknown" title="当前证据需要重新核对">
      这条决定保留在历史中，但候选或回归证据已经变化。刷新后重新核对，当前版本通过后才能再次决定或下载。
    </Callout>}
    {!receipt && !error && <p className="small muted" role="status">正在读取审批状态…</p>}
    {receipt && !receipt.can_accept && !receipt.can_reject && !decision && <p className="small muted">候选或回归证据尚未通过当前版本核对，暂不能审批。</p>}
    {error && <Callout tone="fail" role="alert" title="审批未完成" actions={<button type="button" className="btn btn-sm" onClick={refreshDecision} disabled={busy}><RefreshCw size={13} aria-hidden="true" />刷新审批状态</button>}>{error}</Callout>}
    {pending && !decision && <Callout tone="unknown" title="上次请求待确认" actions={<button type="button" className="btn btn-sm" onClick={() => void decide(pending.decision)} disabled={busy || !samePending(pending, receipt)}>{busy ? "正在提交…" : "按原请求重试"}</button>}>请求可能已经写入服务。先刷新可以恢复已记录的决定；重试会复用同一个请求 ID，不会新建决定。</Callout>}
    <div className="rp-approval-actions" aria-live="polite">
      <button type="button" className="btn btn-sm btn-primary" disabled={!canAccept || busy} onClick={() => void decide("accepted")}><Check size={14} aria-hidden="true" />{pending?.decision === "accepted" && !decision ? "按原请求接受" : "接受候选"}</button>
      <button type="button" className="btn btn-sm btn-ghost" disabled={!canReject || busy} onClick={() => void decide("rejected")}><X size={14} aria-hidden="true" />{pending?.decision === "rejected" && !decision ? "按原请求拒绝" : "拒绝候选"}</button>
      {receipt?.can_download && <button type="button" className="btn btn-sm" disabled={busy} onClick={() => void download()}><Download size={14} aria-hidden="true" />下载补丁</button>}
    </div>
    {decision && <p className="muted small">第 {decision.revision} 次决定 · 当前任务修订 {receipt?.revision ?? decision.revision} · 历史 {decision.history.length} 条。</p>}
    {retryPending && <span className="sr-only">可以按原请求重试。</span>}
  </section>;
}
