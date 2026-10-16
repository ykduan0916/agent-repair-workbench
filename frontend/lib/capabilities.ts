/** UI-08: user-facing availability of each module.
 * Four statuses only (AGENTS.md 前端能力同步门禁). Milestone IDs and acceptance
 * history live in docs/03-roadmap.md, never in the product UI.
 * Update this file together with the roadmap whenever a capability changes.
 */
export type CapabilityStatus = "web" | "web_readonly" | "cli" | "planned";

export const capabilityLabels: Record<CapabilityStatus, string> = {
  web: "网页可用",
  web_readonly: "网页只读",
  cli: "CLI 可用",
  planned: "待开发",
};

export const capabilities = {
  checks: { status: "web", note: "导入或编辑调用证据，在本机检查并下载报告。" },
  tasks: { status: "web", note: "运行四种内置故障场景；暂不支持接入自己的工具。" },
  diagnosis: { status: "web", note: "对两个内置案例发起分析；修复建议不会自动应用。" },
  // B4: human decisions and exact diff download are available; project write-back remains closed.
  repairs: { status: "web", note: "运行两个内置修复案例，审阅候选与回归，接受或拒绝并下载已核对补丁；不自动写回代码。" },
  connections: { status: "web", note: "查看本机环境与接口；自定义工具接入待开发。" },
  memory: { status: "planned", note: "记录经确认的项目经验，尚未开放。" },
} as const satisfies Record<string, { status: CapabilityStatus; note: string }>;

export type CapabilityKey = keyof typeof capabilities;
