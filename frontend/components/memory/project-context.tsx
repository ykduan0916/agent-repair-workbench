"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { loadOverview, loadProjects, memoryRequest, safeMessage, type Overview, type PageInfo } from "../../lib/memory";

export function useMemoryProject() {
  const [projects, setProjects] = useState<{ project_id: string; created_at: number }[]>([]);
  const [project, setProject] = useState(""); const [overview, setOverview] = useState<Overview | null>(null);
  const [error, setError] = useState(""); const [busy, setBusy] = useState(false); const [loading, setLoading] = useState(true);
  const [projectPage, setProjectPage] = useState(0); const [projectPagination, setProjectPagination] = useState<PageInfo>(); const [pages, setPages] = useState<Record<string, number>>({});
  const pageRef = useRef(pages); pageRef.current = pages; const projectPageRef = useRef(projectPage); projectPageRef.current = projectPage;
  const version = useRef(0); const current = useRef(project); current.current = project;
  const refresh = useCallback(async () => {
    const ticket = ++version.current; setLoading(true); setError("");
    try { const loaded = await loadProjects(undefined, projectPageRef.current); const all = loaded.projects; if (ticket !== version.current) return; setProjects(all); setProjectPagination(loaded.pagination);
      const wanted = current.current || new URLSearchParams(location.search).get("project") || "";
      const chosen = wanted || all[0]?.project_id || "";
      if (chosen !== current.current) { setProject(chosen); setOverview(null); return; }
      const next = chosen ? await loadOverview(chosen, undefined, pageRef.current) : null; if (ticket === version.current) setOverview(next);
    } catch (e) { if (ticket === version.current) { setOverview(null); setError(safeMessage(e)); } }
    finally { if (ticket === version.current) setLoading(false); }
  }, []);
  useEffect(() => { void refresh(); return () => { version.current++; }; }, [project, projectPage, pages, refresh]);
  const select = (value: string) => { setProject(value); setPages({}); setOverview(null); const url = new URL(location.href); url.searchParams.set("project", value); url.searchParams.delete("memory"); history.replaceState(null, "", url); };
  const execute = async (action: () => Promise<unknown>, success?: () => void) => {
    if (busy) return; setBusy(true); setError("");
    try { await action(); success?.(); await refresh(); } catch (e) { setError(safeMessage(e)); } finally { setBusy(false); }
  };
  return { projects, project, projectPage, setProjectPage, projectPagination, pages, setPages, overview, error, busy, loading, refresh, select, execute, setError };
}
export type ProjectContext = ReturnType<typeof useMemoryProject>;
export function ProjectBar({ context }: { context: ProjectContext }) {
  const [adding, setAdding] = useState(false); const [name, setName] = useState("");
  return <div className="memory-project-bar">
    <label>项目<select value={context.project} disabled={context.busy || context.loading} onChange={e => context.select(e.target.value)}><option value="" disabled>选择项目</option>{context.project && !context.projects.some(p => p.project_id === context.project) && <option>{context.project}</option>}{context.projects.map(p => <option key={p.project_id}>{p.project_id}</option>)}</select></label>
    {context.projectPagination && (context.projectPage > 0 || context.projectPagination.has_more) && <Pager label="项目" info={context.projectPagination} disabled={context.busy || context.loading} onChange={context.setProjectPage} />}
    <button className="btn btn-sm" disabled={context.busy} onClick={() => setAdding(!adding)}>新建项目</button>
    <button className="btn btn-ghost btn-sm" disabled={context.busy || context.loading} onClick={() => void context.refresh()}>刷新</button>
    {adding && <form className="memory-inline-form" onSubmit={e => { e.preventDefault(); void context.execute(() => memoryRequest("/api/memory/projects", { method: "POST", body: { project_id: name } }), () => { context.select(name); setAdding(false); setName(""); }); }}><label>项目标识<input required maxLength={96} pattern="[A-Za-z0-9_-]+" placeholder="my-agent" value={name} onChange={e => setName(e.target.value)} /></label><button className="btn btn-primary btn-sm" disabled={context.busy}>创建</button><p className="muted small">字母、数字、短横线或下划线。仅在本机创建。</p></form>}
  </div>;
}

export function Pager({ label, info, disabled, onChange }: { label: string; info?: PageInfo; disabled: boolean; onChange: (page: number) => void }) { if (!info || (!info.page && !info.has_more)) return null; return <nav className="memory-pager" aria-label={`${label}分页`}><button className="btn btn-ghost btn-sm" disabled={disabled || info.page === 0} onClick={() => onChange(info.page - 1)}>上一页</button><span className="small muted">第 {info.page + 1} 页</span><button className="btn btn-ghost btn-sm" disabled={disabled || !info.has_more} onClick={() => onChange(info.next_page ?? info.page + 1)}>下一页</button></nav>; }
export function OverviewPager({ context, section, label }: { context: ProjectContext; section: "connections" | "events" | "memories"; label: string }) { return <Pager label={label} info={context.overview?.pagination?.[section]} disabled={context.busy || context.loading} onChange={page => context.setPages(pages => ({ ...pages, [section]: page }))} />; }
