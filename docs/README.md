# 开发文档索引

- 版本：0.1；建立日期：2026-10-13。
- 本次实现负责人：Codex；独立评审负责人：专职评审 Agent（在报告中记录实际身份）。
- 生效依据：用户授权编写约束和迭代文档并开始第一项开发。市场需求仍是待验证假设，不以此授权推断已有客户或商业验证。

| 文档 | 权威内容 | 更新触发 |
|---|---|---|
| [01 constraints](01-constraints.md) | 用户、范围、不变量、禁止事项 | 范围或风险边界变化 |
| [02 architecture](02-architecture.md) | 组件边界、数据流、技术决策 | 接口/依赖/持久化变化 |
| [03 roadmap](03-roadmap.md) | 唯一任务状态、优先级、依赖、验收 | 每项开始与结束 |
| [04 quality gates](04-quality-gates.md) | 测试、评估、评审、发布门禁 | 验收或交付方式变化 |
| [05 contract checker](05-contract-checker.md) | 首项可执行输入/输出规格 | 数据格式或检查语义变化 |
| [06 dependencies](06-dependencies.md) | 依赖版本、用途、许可证与扫描范围 | 新增/升级依赖 |
| [07 product UI](07-web-workbench.md) | 完整界面、真实能力与预览、浏览器测试 | 页面/交互/解锁范围变化 |
| [08 MCP integration](08-mcp-integration.md) | P0-02 字段映射、受控执行、公开案例和测试计划 | SDK/协议/执行边界变化 |
| [09 Agent modules](09-agent-modules.md) | 产品角色分目录、依赖边界、新增步骤与测试发现 | 角色增加、契约或维护流程变化 |
| [10 UI appearance](10-ui-02-appearance.md) | 三态主题、动效、历史口径与浏览器验收 | 配色、交互、能力状态变更 |
| [11 controlled execution](11-controlled-execution.md) | P0-03 容器策略、字节限制、取消和故障矩阵 | 执行边界与运行测试变化 |
| [12 execution UI](12-p0-03-ui-status.md) | P0-03 页面能力与历史同步 | CLI/Web状态变化 |
| [13 module progress](13-ui-progress.md) | UI-03侧栏与页面实际能力同步规则 | 每次能力解锁/状态变化 |
| [14 run store](14-run-store.md) | P0-04 状态、租约、报告事务、恢复与只读网页 | 执行持久化及恢复规则变化 |
| [15 run history UI](15-run-history-ui.md) | P0-04 真实记录页、只读边界、前端验收 | 运行记录UI及API契约变化 |
| [16 navigation hierarchy](16-navigation-hierarchy.md) | UI-04 主导航与产品信息入口分层 | 导航位置、页脚样式及离开保护变化 |
| [17 agents, tools and testing](17-agent-tools-testing-plan.md) | DOC-03 产品闭环、角色、受控工具、自研边界、A/R测试矩阵 | P0-05/06契约、权限或验收变化 |
| [18 P0-05a contracts](18-p0-05a-contracts.md) | 角色/工具请求、状态和安全摘要冻结规格 | 契约或权限变化需增量评审 |
| [19 P0-05a delivery](19-p0-05a-delivery.md) | 本批实现、验收、模型接口探针与边界 | 验收和接续批次变化 |
| [20 UI guidance and public source](20-ui-05-public-source.md) | UI-05 引导、文档、主题修复及公开源码范围 | 本批验收和发布记录变化 |
| [21 local document reader](21-ui-06-document-reader.md) | UI-06 站内正文、目录、主题与文档链接 | 文档阅读能力变化 |
| [22 web reproduction](22-web-reproduction.md) | P1-01a 网页操作、持久接收、取消与测试 | 本批实现及验收变化 |
| [23 web reproduction delivery](23-p1-01a-delivery.md) | P1-01a 实施、测试与复审记录 | 每批交付与实测结果变化 |
| [24 P0-05b runtime](24-p0-05b-runtime.md) | 模型预算、五工具、持久分析和只读诊断页 | 当前实现边界与验收变化 |
| [25 P0-05b delivery](25-p0-05b-delivery.md) | 本批模型实测、A矩阵、评审和剩余边界 | 本批交付与实测结果变化 |
| [26 P0-06 candidate verification](26-p0-06-candidate-verification.md) | 隔离候选、冻结回归与公平对照的实施设计与分批边界 | 当前设计与分批验收变化 |
| [27 local development guide](27-local-development-guide.md) | 安装、启动、模型配置、恢复与工程验收 | 可执行命令或环境配置变化 |
| reviews/ | 文档及代码评审证据 | 每次评审 |

## 冲突、版本和决策

用户最新明确指令 > 产品约束 > 当前任务规格 > 架构 > 示例。台账决定实现状态；评审报告决定该快照的质量结论。发现冲突需同步修订并标原因，不让不同文档长期给出相反规则。

新增范围先在架构记录 ADR（背景、决策、替代方案、代价、重新评估条件），再给台账任务及验收。数据协议破坏性修改增加 `schema_version`；旧报告不可静默重新解释。文档状态和产品版本独立，0.1 文档不意味着已可发布。

## 变更记录

| 日期 | 变更 | 原因 | 状态 |
|---|---|---|---|
| 2026-10-13 | 建立局部工具调用可靠性方向及 P0-01 规格 | 限制个人开发范围、先有独立验收依据 | 已完成独立评审与修订，见 reviews/DOC-01.md |
| 2026-10-13 | 明确数值保真边界、精确 multipleOf、CLI 参数不回显 | 独立代码评审发现及文档增量复审 | 文档增量复审通过 |
| 2026-10-13 | UI-01产品界面先行及P0-02详细测试规格 | 用户要求先完成产品UI，后台逐项解锁 | 独立增量复审通过，见 reviews/UI-01-P0-02-design-review.md |
| 2026-10-13 | DOC-03统一Agent/tools职责、自研边界和26项后续验收用例 | 用户要求开发前明确闭环及测试；同步架构当前状态 | 两项设计问题修复复审，见 [DOC-03](reviews/DOC-03-review.md)；不计模型实现完成 |

公开版说明：历史评审中的本机绝对路径已脱敏为 `<project-root>` / `<review-workspaces>`；快照哈希记录的是当时文件，文档脱敏及后续迭代会改变文档哈希。历史代码快照和验收事实不据此重写。

- [28 UI-07 diagnosis density](28-ui-07-analysis-density.md)

- [29：网页分析与执行控制台](29-web-analysis-console.md)

- [30：网页执行交付与实际验收](30-web-analysis-delivery.md)

- [31：隔离修复与冻结回归交付](31-p0-06-delivery.md)
- [32：产品界面改造规格](32-ui-08-product-redesign.md)
- [33：产品界面整合验收](33-ui-08-delivery.md)

- [34：网页修复与人工审批设计](34-p1-01-web-repair.md)

- [35：网页修复接收与一次执行权](35-p1-01-b1-admission.md)

- [36：网页修复接收账本实现与验收](36-p1-01-b1a-delivery.md)

- [37：执行接续与安全恢复实现与验收](37-p1-01-b1b-delivery.md)

- [38：修复运行 API 与任务生命周期](38-p1-01-b2-delivery.md)

- [39：网页修复操作与本机常驻服务](39-p1-01-b3-web-controls.md)

- [40：人工决定与精确补丁下载](40-p1-01-b4-approval-download.md)

- [41：真实网页修复闭环验收](41-p1-01-b5-delivery.md)

- [42：项目经验记忆调研与实施设计](42-p1-02-memory-research-design.md) — 早期历史方案；当前业务及接入以44为准，运行功能未实现。

- [43 模型回复诊断与格式纠正](43-rel-01-02-model-recovery.md)

- [44：长期记忆业务、外部接入与产品交互](44-p1-02-memory-product-and-integration.md) — 当前业务依据：三类记忆、参考与验证边界、业务Hook、接入契约、页面操作和验收门禁；尚未实现。

- [45：Cognee选型与契约验证](45-mr-01-cognee-contract-validation.md) — 独立环境的版本、既有模型兼容、来源与删除实测；不等于产品记忆上线。

- [MR-01：Cognee选型独立评审](reviews/MR-01-cognee-review.md) — 有条件通过实验；生产上线未验收。

- [46：经验权威领域与事件存储](46-m1-r-authority-event-store.md) — 领域存储实现；客户端接入与自动记忆尚未开放。

- [M1-R：权威领域与事件存储独立评审](reviews/M1-R-authority-review.md) — 限定领域范围通过，实际引擎和外部Hook尚待接入验收。
