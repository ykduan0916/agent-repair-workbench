"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { useAllowNavigation } from "../ui/navigation-guard";

export function MemoryNavigation({ current, project }: { current: "library" | "hooks"; project: string }) {
  const allow = useAllowNavigation();
  const [initialProject, setInitialProject] = useState<string | null>(null);
  useEffect(() => { setInitialProject(new URLSearchParams(location.search).get("project") ?? ""); }, []);
  const selected = project || initialProject || "";
  const query = selected ? `?project=${encodeURIComponent(selected)}` : "";
  return <nav className="memory-navigation" aria-label="记忆导航">
    {[{ id: "library", href: "/workspace/memory", label: "记忆库" }, { id: "hooks", href: "/workspace/memory/hooks", label: "Hook 与事件" }].map(item =>
      <Link key={item.id} href={item.href + query} aria-current={current === item.id ? "page" : undefined} aria-disabled={initialProject === null || undefined} onNavigate={event => { if (initialProject === null || !allow()) event.preventDefault(); }}>{item.label}</Link>
    )}
  </nav>;
}
