"use client";
import { PageHeader } from "./ui/page-header";
import CapabilityBadge from "./capability-badge";
import AgentContracts from "./agent-contracts";
import { modules } from "../lib/modules";
import { EnvSection } from "./environment/section";
import { StatusSection } from "./environment/status-section";
import { ModelSection } from "./environment/model-section";
import { DataSection } from "./environment/data-section";
import { CliSection } from "./environment/cli-section";
import { useEffect } from "react";

const toc = [
  { id: "status", label: "本机环境" },
  { id: "model", label: "模型" },
  { id: "data", label: "本机数据" },
  { id: "contracts", label: "角色与工具接口" },
  { id: "cli", label: "命令行" },
];

/** Local environment and explicit project/source management. */
export default function ConnectionsConsole() {
  const { title, description } = modules.connections;
  useEffect(() => {
    const redirectMemory = () => {
      if (location.hash !== "#memory") return;
      const project = new URLSearchParams(location.search).get("project");
      location.replace(`/workspace/memory/hooks${project ? `?project=${encodeURIComponent(project)}` : ""}`);
    };
    redirectMemory();
    window.addEventListener("hashchange", redirectMemory);
    return () => window.removeEventListener("hashchange", redirectMemory);
  }, []);
  return <div className="env-page">
    <PageHeader title={title} description={description} badge={<CapabilityBadge section="connections" />} />
    <nav className="env-toc" aria-label="本页内容">
      {toc.map(item => <a key={item.id} href={`#${item.id}`}>{item.label}</a>)}
    </nav>
    <StatusSection />
    <ModelSection />
    <DataSection />
    <EnvSection id="contracts" title="角色与工具接口" description="每个角色负责什么、能调用哪些工具，以及它们的数据契约。只读，不会运行工具或调用模型。">
      <AgentContracts />
    </EnvSection>
    <CliSection />
  </div>;
}
