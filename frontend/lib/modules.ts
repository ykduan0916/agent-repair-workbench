/** UI-08: page titles and one-line descriptions for workspace routes. */
export const modules = {
  tasks: { title: "复现", description: "在本机隔离容器里运行故障场景，查看执行事件与核验报告。" },
  diagnosis: { title: "分析", description: "让诊断、修复、验证三个角色在预算内分析内置案例，并核对每条结论的证据。" },
  repairs: { title: "修复", description: "运行内置案例，核对隔离候选与回归结果，人工决定后下载补丁。" },
  memory: { title: "项目经验", description: "核对来源、确认经验，并管理适用范围和外发授权。" },
  connections: { title: "环境与接入", description: "查看本机服务、复现环境与模型配置，管理事件来源、投递凭据与内容授权。" },
} as const;
export type ModuleKey = keyof typeof modules;
