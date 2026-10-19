"use client";
import Link from "next/link";
import { StatusBadge } from "./ui/status";
import { PageHeader } from "./ui/page-header";
import CapabilityBadge from "./capability-badge";
import AgentContracts from "./agent-contracts";
import { modules } from "../lib/modules";
import { EnvSection, SettingRow } from "./environment/section";
import { StatusSection } from "./environment/status-section";
import { ModelSection } from "./environment/model-section";
import { DataSection } from "./environment/data-section";
import { CliSection } from "./environment/cli-section";

const toc = [
  { id: "status", label: "本机环境" },
  { id: "model", label: "模型" },
  { id: "data", label: "本机数据" },
  { id: "memory", label: "经验与事件" },
  { id: "contracts", label: "角色与工具接口" },
  { id: "cli", label: "命令行" },
];

/** 环境与接入: read-only settings surface. Reproductions are launched only from /workspace/tasks. */
export default function ConnectionsConsole() {
  const { title, description } = modules.connections;
  return <div className="env-page">
    <PageHeader title={title} description={description} badge={<CapabilityBadge section="connections" />} />
    <nav className="env-toc" aria-label="本页内容">
      {toc.map(item => <a key={item.id} href={`#${item.id}`}>{item.label}</a>)}
    </nav>
    <StatusSection />
    <ModelSection />
    <DataSection />
    <EnvSection id="memory" title="经验与事件" description="让后续排查能参考经确认的经验；当前不会自动采集外部会话。">
      <div className="pane env-pane">
        <SettingRow label="项目经验" aside={<CapabilityBadge section="memory" />}>
          <p className="muted">在命令行索引与检索已授权的确认经验。结果附来源和适用版本，撤回后停止取用；网页管理和 Agent 自动参考尚未接通。</p>
          <Link className="link small" href="/docs/47-m2-r-engine-and-retrieval#6-本机使用">查看配置与使用方法</Link>
        </SettingRow>
        <SettingRow label="外部事件" aside={<StatusBadge tone="neutral">待开发</StatusBadge>}>
          <p className="muted">计划接收授权的测试结果、工具契约变化与用户纠正。Codex 等客户端插件尚未接通。</p>
          <Link className="link small" href="/docs/44-p1-02-memory-product-and-integration">查看接入方案</Link>
        </SettingRow>
      </div>
    </EnvSection>
    <EnvSection id="contracts" title="角色与工具接口" description="每个角色负责什么、能调用哪些工具，以及它们的数据契约。只读，不会运行工具或调用模型。">
      <AgentContracts />
    </EnvSection>
    <CliSection />
  </div>;
}
