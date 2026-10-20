"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { BookOpen, Braces, GitCompareArrows, Home, Layers3, Menu, Plug, ScanSearch, X } from "lucide-react";
import ThemeSwitch from "./ui/theme-switch";
import EnvironmentStatus from "./environment-status";
import { EnvironmentProvider } from "./ui/environment-context";
import { ToastProvider } from "./ui/toast";
import { ConfirmProvider } from "./ui/confirm-dialog";
import { NavigationGuardProvider, useAllowNavigation } from "./ui/navigation-guard";

export const workspaceNav = [
  { href: "/workspace", label: "总览", icon: Home },
  { href: "/workbench", label: "调用检查", icon: Braces },
  { href: "/workspace/tasks", label: "复现", icon: Layers3 },
  { href: "/workspace/diagnosis", label: "分析", icon: ScanSearch },
  { href: "/workspace/repairs", label: "修复", icon: GitCompareArrows },
  { href: "/workspace/memory", label: "项目经验", icon: BookOpen },
] as const;
const setupNav = [{ href: "/workspace/connections", label: "环境与接入", icon: Plug }] as const;

const DRAWER_QUERY = "(max-width: 900px)";
function useDrawerLayout() {
  return useSyncExternalStore(
    change => { const media = window.matchMedia(DRAWER_QUERY); media.addEventListener("change", change); return () => media.removeEventListener("change", change); },
    () => window.matchMedia(DRAWER_QUERY).matches,
    () => false,
  );
}

function Sidebar({ open, hidden, onNavigate }: { open: boolean; hidden: boolean; onNavigate: () => void }) {
  const pathname = usePathname();
  const allow = useAllowNavigation();
  const guard = (event: { preventDefault: () => void }) => { if (!allow()) event.preventDefault(); else onNavigate(); };
  const link = ({ href, label, icon: Icon }: { href: string; label: string; icon: typeof Home }) =>
    <Link key={href} href={href} className="side-link" onNavigate={guard} aria-current={pathname === href ? "page" : undefined}>
      <Icon size={16} aria-hidden="true" />{label}
    </Link>;
  // A closed drawer is off-screen; inert keeps its links out of the Tab order and the accessibility tree.
  return <aside className="side-nav" data-open={open} id="workspace-nav" aria-label="工作区" inert={hidden || undefined}>
    <Link href="/" className="side-brand" onNavigate={guard} aria-label="Agent 修复台产品介绍">
      <span className="brand-glyph" aria-hidden="true">{"{}"}</span>Agent 修复台
    </Link>
    <nav aria-label="产品模块" className="side-group">{workspaceNav.map(link)}</nav>
    <nav aria-label="设置" className="side-group">
      <div className="side-group-label">设置</div>
      {setupNav.map(link)}
    </nav>
    <div className="side-foot">
      <EnvironmentStatus />
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
        <nav className="side-foot-links" aria-label="帮助">
          <Link href="/docs" onNavigate={guard}>文档</Link>
          <Link href="/updates" onNavigate={guard}>更新记录</Link>
        </nav>
        <ThemeSwitch />
      </div>
    </div>
  </aside>;
}

function Frame({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  const [openedAt, setOpenedAt] = useState(pathname);
  const drawer = useDrawerLayout();
  const toggle = useRef<HTMLButtonElement>(null);
  if (open && openedAt !== pathname) { setOpen(false); setOpenedAt(pathname); }
  if (open && !drawer) setOpen(false);
  const dismiss = () => { setOpen(false); toggle.current?.focus(); };
  useEffect(() => {
    if (!open) return;
    // Move focus into the drawer; the background is inert while it is open.
    document.querySelector<HTMLElement>("#workspace-nav .side-link")?.focus();
    const key = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || event.defaultPrevented || document.querySelector("dialog[open]")) return;
      setOpen(false); toggle.current?.focus();
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [open]);
  const current = [...workspaceNav, ...setupNav].find(item => item.href === pathname);
  return <div className="app-shell">
    <a className="skip" href="#main">跳到主要内容</a>
    <Sidebar open={open} hidden={drawer && !open} onNavigate={() => setOpen(false)} />
    {open && <div className="side-scrim" onClick={dismiss} aria-hidden="true" />}
    <div className="app-main">
      <header className="mobile-bar">
        <button type="button" ref={toggle} className="btn btn-ghost btn-icon" aria-expanded={open} aria-controls="workspace-nav" onClick={() => { setOpenedAt(pathname); setOpen(value => !value); }} aria-label={open ? "关闭导航" : "打开导航"}>
          {open ? <X size={18} aria-hidden="true" /> : <Menu size={18} aria-hidden="true" />}
        </button>
        <span className="mobile-title">{current?.label ?? "Agent 修复台"}</span>
      </header>
      {/* The menu button stays reachable so keyboard users can close the drawer; only the content goes inert. */}
      <main id="main" className="app-content" tabIndex={-1} inert={(drawer && open) || undefined}>{children}</main>
    </div>
  </div>;
}

export default function AppShell({ children }: { children: React.ReactNode }) {
  return <ToastProvider><ConfirmProvider><NavigationGuardProvider><EnvironmentProvider>
    <Frame>{children}</Frame>
  </EnvironmentProvider></NavigationGuardProvider></ConfirmProvider></ToastProvider>;
}
