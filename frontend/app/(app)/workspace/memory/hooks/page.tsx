import type { Metadata } from "next";
import { PageHeader } from "../../../../../components/ui/page-header";
import { ConnectionManager } from "../../../../../components/memory/connection-manager";

export const metadata: Metadata = { title: "Hook 与事件 · 记忆", description: "管理记忆来源与外部 Hook，核对事件接收和处理记录。" };
export default function MemoryHooksPage() {
  return <div className="memory-page">
    <PageHeader title="记忆" description="从事件来源到可参考的经验，按项目管理。" />
    <section id="memory" aria-label="Hook 与事件"><ConnectionManager /></section>
  </div>;
}
