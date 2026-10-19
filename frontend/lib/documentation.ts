/** Documentation index shown on /docs and in the reader sidebar.
 * The user guide comes first; internal engineering records are grouped under 「工程记录」.
 */
export type DocumentationEntry = { title: string; slug: string; summary: string };
export type DocumentationGroup = { id: string; title: string; description: string; entries: readonly DocumentationEntry[] };

export const documentationGroups: readonly DocumentationGroup[] = [
  {
    id: "guide",
    title: "使用指南",
    description: "安装、启动和每个功能的用法。",
    entries: [
      { title: "项目介绍与快速上手", slug: "getting-started", summary: "解决什么问题、一个具体例子，以及第一次检查的命令。" },
      { title: "本地运行指南", slug: "27-local-development-guide", summary: "启动网页、配置模型、准备 Docker 镜像、运行记录的备份与恢复。" },
    ],
  },
  {
    id: "reference",
    title: "参考",
    description: "支持范围、架构和各模块的规则。",
    entries: [
      { title: "产品范围与约束", slug: "01-constraints", summary: "面向谁、支持什么、明确不做什么。" },
      { title: "架构与技术决策", slug: "02-architecture", summary: "组件边界、数据流和技术选型。" },
      { title: "调用检查规则", slug: "05-contract-checker", summary: "调用证据的格式、检查项、错误代码和退出码。" },
      { title: "复现容器的隔离策略", slug: "11-controlled-execution", summary: "容器限制、输出上限、取消与「结果未知」的含义。" },
      { title: "Agent 角色、工具与测试", slug: "17-agent-tools-testing-plan", summary: "三个角色的职责、可用工具和逐项测试方案。" },
    ],
  },
  {
    id: "records",
    title: "工程记录",
    description: "开发过程中的计划、规格、交付和评审记录，内容停留在写作当时；当前功能以更新记录为准。",
    entries: [
      { title: "经验与事件存储", slug: "46-m1-r-authority-event-store", summary: "项目归属、确认与修订、事件去重、后台任务恢复和使用边界。" },
      { title: "记忆引擎选型与契约验证", slug: "45-mr-01-cognee-contract-validation", summary: "Cognee、现有模型、来源和删除的实测结果，以及生产接入门禁。" },
      { title: "长期记忆业务与接入设计", slug: "44-p1-02-memory-product-and-integration", summary: "业务定位、外部Hook、记忆职责、页面操作和验收门禁；尚未实现。" },
      { title: "模型回复诊断与格式纠正", slug: "43-rel-01-02-model-recovery", summary: "拒绝阶段、一次纠正的边界、预算保护和测试证据。" },
      { title: "项目经验记忆早期设计", slug: "42-p1-02-memory-research-design", summary: "早期调研及历史规格，当前方案见长期记忆业务与接入设计。" },
      { title: "网页修复闭环验收", slug: "41-p1-01-b5-delivery", summary: "真实模型、容器、网页审批与下载的集成门禁，以及测试边界。" },
      { title: "人工决定与精确补丁下载", slug: "40-p1-01-b4-approval-download", summary: "决定账本、版本核对、幂等、并发和补丁下载边界。" },
      { title: "网页修复操作与常驻服务", slug: "39-p1-01-b3-web-controls", summary: "页面开始/停止、刷新恢复、迁移与本机服务的实现和验收。" },
      { title: "修复运行接口与停止说明", slug: "38-p1-01-b2-delivery", summary: "开始、查询与停止接口的实现记录，断连处理及当前部署限制。" },
      { title: "修复执行与恢复记录", slug: "37-p1-01-b1b-delivery", summary: "内部执行接续、停止处理与资源恢复的历史验收记录。" },
      { title: "开发计划与验收台账", slug: "03-roadmap", summary: "任务依赖、状态与验收依据。" },
      { title: "隔离修复交付记录", slug: "31-p0-06-delivery", summary: "候选、回归与真实模型试验的结果。" },
      { title: "隔离候选与回归规格", slug: "26-p0-06-candidate-verification", summary: "候选生成、冻结检查项与判定规则。" },
      { title: "网页执行交付记录", slug: "30-web-analysis-delivery", summary: "网页开始与停止、证据阅读的验收。" },
      { title: "模型协作交付记录", slug: "25-p0-05b-delivery", summary: "三角色协作、预算与真实模型运行。" },
      { title: "角色契约交付记录", slug: "19-p0-05a-delivery", summary: "角色与工具契约的实现和测试。" },
      { title: "完整文档目录", slug: "index", summary: "全部规格、交付和独立评审记录。" },
    ],
  },
];

export const documentationEntries: readonly DocumentationEntry[] = documentationGroups.flatMap(group => group.entries);

/** Group a document belongs to; unlisted documents (specs, reviews) are engineering records. */
export function documentationGroupOf(slug: string): DocumentationGroup {
  return documentationGroups.find(group => group.entries.some(entry => entry.slug === slug)) ?? documentationGroups[documentationGroups.length - 1];
}

export const documentationUrl = (slug: string) => `/docs/${slug.split("/").map(encodeURIComponent).join("/")}`;
