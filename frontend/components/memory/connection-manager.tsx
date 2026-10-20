"use client";
import Link from "next/link";
import { useState } from "react";
import { z } from "zod";
import { OverviewPager, ProjectBar, useMemoryProject, type ProjectContext } from "./project-context";
import { EngineControl } from "./engine-control";
import { ConnectionGrants } from "./connection-grants";
import { useConfirm } from "../ui/confirm-dialog";
import { dateLabel, memoryRequest, newRequest, projectPath, type Connection } from "../../lib/memory";

const eventLabels: Record<string, string> = { test_completed: "测试完成", contract_changed: "契约变化", user_correction: "用户纠正" };
const processingLabels: Record<string, string> = { pending: "待处理", leased: "处理中", done: "已处理", failed: "处理失败", unknown: "结果未知", capacity_limited: "项目容量已满", unmanaged: "旧记录，未纳入处理", cancelled: "已取消", paused: "已暂停" };

export function ConnectionManager() {
  const context = useMemoryProject(); const [adding, setAdding] = useState(false); const [name, setName] = useState(""); const [origin, setOrigin] = useState("external_report");
  return <div className="memory-page memory-connections">
    <ProjectBar context={context} />
    {context.error && <p role="alert" className="memory-error">{context.error}</p>}
    {context.loading && <p role="status" className="muted">正在读取接入状态…</p>}
    {context.project && context.overview && <>
      <div className="memory-toolbar"><button className="btn btn-sm" disabled={context.busy} onClick={() => setAdding(!adding)}>添加事件来源</button><Link className="link small" href={`/workspace/memory?project=${context.project}`}>管理项目经验</Link><Link className="link small" href="/docs/48-m4-r-web-memory-hooks">接入指南</Link></div>
      {adding && <form className="memory-form pane memory-create" onSubmit={e => { e.preventDefault(); void context.execute(() => memoryRequest(`${projectPath(context.project)}/connections`, { method: "POST", body: { connection_id: name, origin } }), () => { setAdding(false); setName(""); }); }}><label>来源标识<input required maxLength={96} pattern="[A-Za-z0-9_-]+" placeholder="local-tests" value={name} onChange={e => setName(e.target.value)} /></label><label>来源类型<select value={origin} onChange={e => setOrigin(e.target.value)}><option value="external_report">外部测试或 Hook</option><option value="local_user">本机手动记录</option></select></label><p className="small muted">创建不会自动安装插件、读取会话或启用外发。</p><button className="btn btn-primary btn-sm" disabled={context.busy}>创建来源</button></form>}
      {!context.overview.connections.length && <p className="memory-empty muted">添加事件来源后，配置本地投递凭据；收到事件才能确认连接实际工作。</p>}
      <OverviewPager context={context} section="connections" label="来源" /><div className="pane">{context.overview.connections.map(connection => <ConnectionRow key={`${context.project}:${connection.connection_id}`} context={context} connection={connection} />)}</div>
      <details className="memory-events"><summary>最近事件与处理结果（{context.overview.events.length}）</summary><p className="small muted">接收成功只表示记录已保存。处理生成待确认记录，不代表已复现、修复或通过回归。</p>
        <div className="memory-event-list">{context.overview.events.length ? context.overview.events.slice(0, 100).map(event => <div className="memory-event-row" key={`${event.connection_id}/${event.event_id}`}><div><strong>{eventLabels[event.event_type] ?? "外部事件"}</strong><p className="small muted">{event.connection_id} · {dateLabel(event.received_at)}</p></div><div className="small"><span>已接收</span> / <span>{processingLabels[event.processing] ?? "待核对"}</span><p>来源报告：{event.outcome === "passed" ? "通过" : event.outcome === "failed" ? "失败" : "未知"}</p>{event.memory_id && <Link className="link" href={`/workspace/memory?project=${context.project}&memory=${event.memory_id}`}>查看草稿</Link>}</div><details><summary>标识</summary><code>{event.event_id}</code></details></div>) : <p className="muted small">还没有接收到事件。</p>}</div>
        <OverviewPager context={context} section="events" label="事件" />
      </details><details className="memory-events"><summary>索引维护</summary><EngineControl context={context} /></details>
    </>}
    {!context.project && !context.loading && <p className="muted">先创建项目，再添加测试、Hook 或本机手动记录来源。</p>}
  </div>;
}

function ConnectionRow({ context, connection }: { context: ProjectContext; connection: Connection }) {
  const [token, setToken] = useState(""); const [expanded, setExpanded] = useState(false); const confirm = useConfirm();
  const [workspace, setWorkspace] = useState(""); const [commands, setCommands] = useState("");
  const path = `${projectPath(context.project)}/connections/${connection.connection_id}`;
  const received = context.overview?.events.filter(e => e.connection_id === connection.connection_id) ?? [];
  async function credential(action: "rotate" | "revoke") {
    if (!await confirm({ title: action === "rotate" ? "生成新的投递凭据？" : "撤销投递凭据？", message: action === "rotate" ? "原凭据立即失效。新凭据仅展示这一次，请保存到本机私有配置。" : "外部客户端将无法投递或查询事件。已接收记录会保留。", confirmLabel: action === "rotate" ? "生成凭据" : "撤销凭据", danger: action === "revoke" })) return;
    setToken(""); await context.execute(async () => { const response = z.object({ credential_version: z.number(), credential_active: z.boolean(), token: z.string().max(512).nullable().optional() }).parse(await memoryRequest(`${path}/credential`, { method: "POST", body: { expected_version: connection.credential_version, action } })); if (response.token) setToken(response.token); });
  }
  return <section className="memory-connection" aria-label={`来源 ${connection.connection_id}`}>
    <div className="memory-connection-head"><div><h3>{connection.connection_id}</h3><p className="small muted">{connection.origin === "external_report" ? "外部报告" : connection.origin === "synthetic" ? "合成测试来源" : "本机手动记录"} · {connection.enabled ? received.length ? `当前事件页有 ${received.length} 条` : "当前事件页无此来源记录" : "已暂停"}</p></div><button className="btn btn-sm" aria-expanded={expanded} onClick={() => { setExpanded(!expanded); setToken(""); }}>{expanded ? "收起" : "管理来源"}</button></div>
    {expanded && <div className="memory-connection-detail">
      <div className="memory-action-row"><button className="btn btn-sm" disabled={context.busy} onClick={() => void context.execute(() => memoryRequest(path, { method: "PATCH", body: { expected_version: connection.version, enabled: !connection.enabled } }))}>{connection.enabled ? "暂停接收" : "恢复接收"}</button><button className="btn btn-sm" disabled={context.busy || !connection.enabled} onClick={() => void credential("rotate")}>{connection.credential_active ? "轮换凭据" : "生成投递凭据"}</button>{connection.credential_active && <button className="btn btn-ghost btn-sm" disabled={context.busy} onClick={() => void credential("revoke")}>撤销凭据</button>}</div>
      {token && <div className="memory-secret"><label>一次性投递凭据<input type="password" readOnly value={token} autoComplete="off" spellCheck={false} /></label><p className="small">仅本次可见，关闭此区域后不再显示。不写入浏览器存储；丢失后重新生成。</p><button className="btn btn-sm" onClick={() => { void navigator.clipboard.writeText(token).catch(() => context.setError("无法复制，请手动选择凭据保存。")); }}>复制凭据</button><button className="btn btn-ghost btn-sm" onClick={() => setToken("")}>隐藏凭据</button>
        <form className="memory-form" onSubmit={e => { e.preventDefault(); const allowed = commands.split("\n").map(c => c.trim()).filter(Boolean); if (allowed.length > 20 || allowed.some(c => c.length > 1024)) { context.setError("最多允许 20 条命令，每条不超过 1024 字符。"); return; } const blob = new Blob([JSON.stringify({ endpoint: "http://127.0.0.1:8030", connection_id: connection.connection_id, token, workspace, test_commands: allowed }, null, 2)], { type: "application/json" }); const url = URL.createObjectURL(blob); const link = document.createElement("a"); link.href = url; link.download = "connection.json"; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000); }}>
          <label>允许采集的项目绝对路径<input required pattern="/.*" maxLength={4096} value={workspace} placeholder="/Users/you/my-agent" onChange={e => setWorkspace(e.target.value)} /></label>
          <label>允许记录的测试命令（每行一条，精确匹配）<textarea required rows={3} maxLength={20480} value={commands} placeholder="uv run pytest" onChange={e => setCommands(e.target.value)} /></label>
          <p className="small muted">下载含凭据的本机配置；请按接入指南将目录权限设为 700、文件设为 600，再安装并信任插件。下载不会执行命令或安装 Hook。</p><button className="btn btn-sm">下载本机接入配置</button>
        </form>
      </div>}
      <div className="memory-action-row"><button className="btn btn-sm" disabled={context.busy || !connection.enabled} onClick={() => void context.execute(() => memoryRequest(`${path}/events`, { method: "POST", body: { schema_version: 1, event_id: `web_test_${newRequest()}`, event_type: "test_completed", source_run_id: "web_connection_test", occurred_at: Date.now() / 1000, outcome: "unknown", sequence: 0, artifact_digests: [] } }))}>发送连通测试事件</button><button className="btn btn-sm" disabled={context.busy || !connection.enabled} onClick={() => void context.execute(() => memoryRequest(`${path}/process`, { method: "POST" }))}>处理待办事件</button></div>
      <p className="small muted">连通测试来自此网页，不代表 Codex Hook 已安装或已投递。处理只整理来源记录，不调用模型。</p>
      <ConnectionGrants context={context} connection={connection} />
    </div>}
  </section>;
}
