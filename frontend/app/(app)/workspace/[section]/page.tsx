import type { Metadata } from "next";
import { notFound } from "next/navigation";
import ConnectionsConsole from "../../../../components/connections-console";
import AnalysisHistory from "../../../../components/analysis-history";
import RepairWorkspace from "../../../../components/repair-workspace";
import MemoryWorkspace from "../../../../components/memory-workspace";
import RunHistory from "../../../../components/run-history";
import { modules, type ModuleKey } from "../../../../lib/modules";

export function generateStaticParams() {
  return Object.keys(modules).map((section) => ({ section }));
}
export const dynamicParams = false;

export async function generateMetadata({ params }: { params: Promise<{ section: string }> }): Promise<Metadata> {
  const { section } = await params;
  return Object.hasOwn(modules, section) ? { title: modules[section as ModuleKey].title, description: modules[section as ModuleKey].description } : {};
}

export default async function ModulePage({ params }: { params: Promise<{ section: string }> }) {
  const { section } = await params;
  if (!Object.hasOwn(modules, section)) notFound();
  if (section === "repairs") return <RepairWorkspace section="repairs" />;
  if (section === "diagnosis") return <AnalysisHistory />;
  if (section === "memory") return <MemoryWorkspace />;
  if (section === "connections") return <ConnectionsConsole />;
  return <RunHistory />;
}
