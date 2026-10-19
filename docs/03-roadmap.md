# 优先级、迭代与验收台账

版本 0.1 / 2026-10-13。此文件是完成进度的唯一来源。

## 1. 优先级定义

- **Feature P0**：建立“真实失败→局部修复→独立回归”闭环不可缺少的工作；按依赖分期，不表示今天全部开发。
- **Feature P1**：闭环通过后，让开发者持续使用、验证适配性的工作。
- **Feature P2**：真实使用证据支持后再排期的扩展。
- **Defect P0/P1/P2/P3**：代码缺陷严重程度，见质量门禁；与功能优先级无映射关系。

状态：planned / ready / in_progress / in_review / done / blocked。done 必须附测试和评审证据。预计工作量为单人专注开发的粗估，不是截止日期；复杂度超预期必须拆分，不削减验收。

## 2. P0 台账

| ID | 迭代 / 工作项 | 依赖 | 估计 | 状态 | 完成证据 |
|---|---|---|---|---|---|
| DOC-01 | 范围、约束、架构、台账、质量和首项规格 | 无 | 本次 | done | [DOC-01 评审](reviews/DOC-01.md) |
| P0-01 | M0 契约检查器、CLI、结构化证据报告 | DOC-01 | 1–2 天 | done | 86 tests、Ruff、mypy、锁检查通过；[独立代码评审及复审](reviews/P0-01-code-review.md) |
| UI-01 | 产品首页、完整模块预览与真实检查 API | P0-01 | 本次 | done | 99 Python / 9 E2E；[代码评审](reviews/UI-01-code-review.md)、[文档复审](reviews/UI-01-P0-02-design-review.md) |
| UI-02 | 三态主题、交互动效、完整历史与功能进度同步 | UI-01 | 本次 | done | 18 E2E、浅深色 36 组合、短桌面导航复审；[独立 UI 评审](reviews/UI-02-independent-review.md) |
| DOC-02 | 产品 Agent 分目录及维护指南 | DOC-01 | 本次 | done | [维护规范](09-agent-modules.md)；[独立评审](reviews/P0-02-independent-review.md)，不计 Agent 运行实现完成 |
| P0-02 | M1 MCP stdio 真实调用与协议适配 | P0-01 | 2–3 天 | done | 156 Python tests（含独立环境公开案例）；[独立代码复审](reviews/P0-02-independent-review.md) |
| P0-03 | M1 受控执行、故障注入、取消与进程回收 | P0-02 | 3–5 天 | done | 284 Python（含18真实Docker）/18 E2E；[验收证据](11-controlled-execution.md)、[Docker独立评审](reviews/p0-03-docker-review.md)、[UI复审](reviews/P0-03-ui-review.md)；状态按planned → ready → in_progress → in_review → done流转 |
| UI-03 | 模块状态与首屏能力进度同步 | P0-03 | 本次 | done | [完成记录](13-ui-progress.md)、[独立评审](reviews/UI-03-review.md)；19 E2E/build/typecheck通过；planned → ready → in_progress → in_review → done |
| P0-04 | M1 运行状态、产物、恢复对账 | P0-03 | 2–4 天 | done | [后端验收](14-run-store.md)、[真实记录页](15-run-history-ui.md)、[store评审](reviews/P0-04-store-review.md)、[协调评审](reviews/P0-04-coordinator-review.md)、[UI评审](reviews/P0-04-ui-review.md)；340完整Python+4独立反例，29主UI+20隔离UI通过；planned → ready → in_progress → in_review → done |
| UI-04 | 产品信息入口与工作导航分层 | P0-04 | 本次 | done | [设计及验收](16-navigation-hierarchy.md)、[独立复审](reviews/UI-04-review.md)；build/typecheck及8项修复后定向验收通过；planned → ready → in_progress → in_review → done |
| DOC-03 | 产品闭环、Agent/tools边界与测试用例规格 | P0-04 | 本次 | done | [实施规格](17-agent-tools-testing-plan.md)、[独立文档复审](reviews/DOC-03-review.md)；8工具/26验收用例矩阵及链接核对，D01/D02修复复审；planned → ready → in_progress → in_review → done；不计Agent运行实现完成 |
| UI-06 | 站内工程文档阅读器与排版 | UI-05 | 本次 | done | [规格](21-ui-06-document-reader.md)；planned → ready → in_progress → in_review → done；[复审](reviews/UI-06-code-review.md) |
| UI-05 | 功能引导、文档入口、主题修复与公开源码 | P0-05a | 本次 | done | [范围与验收](20-ui-05-public-source.md)；planned → ready → in_progress → in_review → done |
| P0-05 | M2 三角色有限循环、模型适配与预算 | P0-04 | 3–5 天 | done | planned → ready → in_progress → in_review → done；[P0-05a](19-p0-05a-delivery.md)、[P0-05b交付验收](25-p0-05b-delivery.md)、[独立交付复审](reviews/P0-05b-delivery-review.md)；554默认Python、28真实Docker、真实模型全流程、82主UI与独立21+1通过；建议未应用 |
| UI-07 | 导航语义、诊断结果优先与紧凑过程记录 | P0-05b | 本次 | done | planned → ready → in_progress → in_review → done；[验收记录](28-ui-07-analysis-density.md)、[独立复审](reviews/UI-07-review.md)，97事件分页、8次axe与真实记录核对通过 |
| P0-06 | M2 隔离补丁、冻结验收、基线候选比较 | P0-05 | 3–5 天 | done | planned → ready → in_progress → in_review → done；两固定案例12次冻结模型实验11通过/1不可判定，全部保留；独立分批评审与总体验收见[31](31-p0-06-delivery.md) |
| UI-08 | 产品化界面：中性工程风视觉、信息架构重组、文案规范、共享外壳与环境状态 | UI-07、P1-01b | 本次 | done | planned → ready → in_progress → in_review → done；生产浏览器181 passed/9 skipped、真实控制台/修复读取通过；P3维护项与独立复审见[33](33-ui-08-delivery.md) |

负责人：实现 Codex，产品验收 Yukun，质量门禁由专职 reviewer 出具证据。所有 Defect P0/P1 必须在对应任务标 done 前修复；不能用上述估计作为豁免。

### P0-01 验收

- 输入/结果 schema 校验、工具名与调用 ID 关联、错误状态、缺失结果等按 05 规格输出。
- 有效调用通过；schema 无法支持或检查器无法完成时 inconclusive，绝不伪装通过。
- 显式记录检查范围：输入检查可通过，但缺 output schema 的输出检查为 not_checked。
- JSON Pointer 与错误代码定位；不输出原始参数值、结果值、异常消息。
- 输入限制、严格 JSON、schema 限制、CLI 限时与退出码有负向测试；报告含版本和规范化证据哈希。
- 至少提供合成成功、合成失败两个样本，清楚标注来源。pytest、静态检查、独立 code review 通过。

### P0-02 验收

- 固定版本官方 SDK 连接受控本地 stdio server，实际执行 list_tools/call_tool 并保存版本化证据；不使用 mock 代替唯一集成验证。
- 适配 missing output schema、isError、非结构化内容、关联 ID，拒绝未经规范支持的数据转换。
- 至少复现一个有来源和固定版本的公开问题；若无法复现，记录失败原因并保持此门禁未完成。
- 外部工具启动需显式配置，不将输入中的 shell 指令直接执行；参数使用 argv。

### P0-03 验收

- 固定 seed 注入超时、工具错误、响应丢失、重复结果；区分传输错误与工具失败。
- 子进程树取消/超时回收，限制 CPU/内存/输出；写副作用超时进入 outcome_unknown。
- 测试进程在执行前/执行中/结果返回前被杀；不得挂死或误报成功。

### P0-04 验收

- 原子状态事件、乐观并发、唯一 run/attempt ID；两个 worker 竞争不能双占有同一执行。
- 重启对账、过期 lease、重复事件、数据库 busy 和磁盘写失败有测试。
- schema migration 升/降级演练；本地产物摘要可校验；清理不删除正在使用的产物。
- 固定本地保留默认 30 天，可配置；提供 dry-run 删除和实际删除命令，原始凭据永不存储。

### P0-05 验收

详细开发批次、工具 T01～T05、必测 A01～A15 见 [17](17-agent-tools-testing-plan.md)。P0-05a 先评审角色/tool schema、摘要存储与只读 API，再开始运行实现；本文补充规格不代表 P0-05 已完成。

- 现有 DeepSeek 接口完成一次真实受控调用，密钥不入库；支持用 fake adapter 跑确定性 CI。
- 角色输入/输出和权限有 schema；本阶段仅 fake 或只读协作，模型可建议补丁但不能应用或执行生成代码。真实候选编辑须待 P0-06 的隔离、白名单和固定验收前置检查通过。
- 最多 2 次候选建议，任务默认 10 分钟，token 预算由配置明确提供。
- 调用前预留 max_output_tokens，超预算不得继续调模型；计费未知须显示未知。
- 假设无法复现返回 inconclusive；注入文本不能修改权限、验收或预算。

### P0-06 验收

候选工具 T06/T07、必测 R01～R11 和对照方案见 [17](17-agent-tools-testing-plan.md)。

- 基线/候选独立副本；固定验收文件前后哈希一致；文件白名单之外修改即拒绝。
- 原始失败变通过、固定回归无新增失败才标 candidate_verified；人工接受另记状态。
- 有“补丁通过新用例却破坏旧行为”的失败候选测试；保留它的证据。
- 同任务、模型、工具、预算的单 Agent 对照和成本统计；不能只展示最好一次。

## 3. P1 / P2

| ID | 优先级 | 工作 | 依赖 | 验收 | 状态 |
|---|---|---|---|---|---|
| P1-01a | P1 | 固定场景网页受控复现、停止与报告操作 | P0-03/04 | [规格](22-web-reproduction.md)、[设计复审](reviews/P1-01a-design-review.md)；W01～W12，[验收记录](23-p1-01a-delivery.md)；planned → ready → in_progress → in_review → done | done |
| P1-01b | P1 | 固定案例网页分析与执行控制台 | P0-05 | [实施规格](29-web-analysis-console.md)；planned → ready → in_progress；接收账本、服务、前端控制台、证据阅读与交付文档已独立评审；真实网页模型、容器及T05取消回收链路通过，见[交付记录](30-web-analysis-delivery.md) | done |
| P1-01 | P1 | 完整 Web 运行/审批接入（不含已拆分的固定场景入口） | P0-06 | planned → ready → in_progress → in_review；[网页修复设计](34-p1-01-web-repair.md)、[B1a](36-p1-01-b1a-delivery.md)、[B1b](37-p1-01-b1b-delivery.md)、[B2](38-p1-01-b2-delivery.md)、[B3](39-p1-01-b3-web-controls.md)、[B4](40-p1-01-b4-approval-download.md)、[B5](41-p1-01-b5-delivery.md)与[独立复审](reviews/P1-01-B5-quality-review.md)；in_review → done，审批不等于写回 | done |
| P1-02 | P1 | 项目经验记忆与授权事件接入 | P0-06 | [当前业务与接入规格](44-p1-02-memory-product-and-integration.md)；M0-B done（[独立评审](reviews/P1-02-memory-business-review.md)）；MR-01 done（[选型与实测](45-mr-01-cognee-contract-validation.md)，实验Conditional Pass）；M1-R done（[领域存储](46-m1-r-authority-event-store.md)）；M2-R done（[引擎接入与取用验证](47-m2-r-engine-and-retrieval.md)）；下一项M3-R Agent参考与内部经验生成；[42](42-p1-02-memory-research-design.md)旧规格及M1 ready已被取代；记忆领域及本机SDK/CLI检索已实现；网页管理/Agent自动取用/外部接入尚未开放 | in_progress |
| P1-03 | P1 | CLI/CI 打包与接入指南 | P0-06 | 三个不同工具项目接入，其中一个非开发 fixture；安装/卸载/升级验证 | planned |
| P1-04 | P1 | 使用验证与独立评测集 | P0-03 | ≥10 个来源明确的故障案例；真实/合成分报；记录真实开发者试用反馈或明确未取得 | planned |
| P2-01 | P2 | Streamable HTTP MCP | P1-03 | 认证、重连、版本、SSRF/网络边界方案与集成测试 | planned |
| P2-02 | P2 | 多用户服务、Postgres、任务队列 | 用户需求证据 | 先 ADR 与容量/租户安全验收，再开发 | planned |
| P2-03 | P2 | 新角色/A2A/向量检索 | 实测瓶颈 | 对照证明收益并覆盖兼容与失败语义 | planned |

## 4. 迭代规则

2026-10-13 用户调整顺序：先交付 UI-01，再完成 P0-02 的详细设计和测试矩阵。DOC-01、P0-01 已完成；完整产品 UI 在 UI-01 先行；P1-01 真实运行/审批能力按原依赖排期。每项代码评审后将范围、命令、结果、风险、报告路径补入台账。后一项开始前核对前置门禁；模型凭据等缺失只阻塞依赖它的项，不伪造集成结果。

功能完成度以任务及验收条目描述，不按写了几个模块计算百分比。不把 M0 成功称为 M2 完成；独立评审不等于市场验证。

## 5. 2026-10-13 迭代记录

- DOC-01：初审发现执行隔离前置、M3 接入时机及状态语义问题，已修改并独立复审通过。
- P0-01：完成离线 envelope 校验、schema 子集、输入/结果关联、错误/缺失结果处理、JSON 证据报告、限时 CLI、合成样本及 CI 配置。
- 代码评审 CR-01 发现数值精度误判，已修复并独立复审；报告记录原反例、修复位置及最终哈希。
- 实际验证：`uv run --frozen pytest` 86 passed；Ruff check/format、mypy、`uv lock --check` 通过；两个 CLI 样本分别退出 0/1；23 个已扫描依赖无已知漏洞。
- P0-01 完成时的边界：当时尚未运行真实 MCP、模型修复、Web、远端 CI 或公开安装验证。P0-01 是后续验收基础，不是完整 Agent 产品。
- 后续用户调整：先交付 UI-01 完整产品界面，再按 [08](08-mcp-integration.md) 开始 P0-02；SDK 兼容实验与公开案例仍需实际执行。


### UI-01 完成记录

- 首版产品首页、七个工作区入口（六个明确标注的交互预览）、本地真实检查API、导入编辑与报告下载完成。
- 顺序记录：UI-01 由 in_progress → in_review → done；代码审查发现的取消执行槽、BOM保真、草稿导航、手机溢出、文字对比度、非法路由问题均修复并独立复审。
- 验证：99 Python tests、9 Playwright E2E、16个页面/视口组合axe零违规及无整页溢出；Ruff、mypy、TS、production build、uv lock通过；pip-audit/npm audit当前0已知漏洞。
- 本地网页3010/API8030运行；没有推送、运行远端CI或公网部署。Agent诊断/修复、MCP真实接入和项目记忆存储仍未实现。
- P0-02详细设计与测试计划独立复审通过，状态 planned → ready；下一动作是官方SDK固定版本兼容实验，不能把设计通过计为实现完成。


### P0-02 / DOC-02 完成记录

- P0-02：ready → in_progress → in_review → done。官方 SDK 2.3.0 固定、真实 stdio 握手/分页/预检/单次调用、规范化证据报告与 CLI 完成。仅允许两项经审核 profile，不接受任意 executable/argv/env。
- 固定 python-sdk #3644 提交真实复现：声明 required user_id，实际 serialization alias 不一致，由产品 CLI 确认 output_schema_violation，预期退出 1。1717 个源码文件完整核验并记录源码树摘要。
- 独立审查发现的额外源码文件注入 P1、预检期间取消状态 P2 均已修复复审。审查者新增真实 CLI 污染反例均拒绝，未开始握手。
- 验证覆盖 99 项既有回归、41 项 MCP、15 项来源完整性及 1 项显式公共案例；Ruff / mypy / 锁检查通过；主环境与独立实验依赖均为 0 已知公告漏洞。
- 仍未完成：任意外部代码的容器隔离、进程树/CPU/内存硬限制、状态恢复、模型修复；网页接入仍为预览。SDK 规范化证据不宣称原始 wire 保真。
- DOC-02：in_progress → in_review → done。按用户要求建立 diagnosis / repair / verification 独立职责目录，补新增角色与测试发现维护指南，独立评审通过。未创建空壳 Agent 实现，不提前计入 P0-05。
- 旧 Steam 自启动项已停用，旧目录及归档由用户删除；当前产品只在 agent-workbench 开发，不重建旧目录。


### UI-02 完成记录

- in_progress → in_review → done。新增浅色/深色/跟随系统主题，跨刷新保留偏好、响应系统变化、受限存储降级、键盘操作及 reduced-motion。
- 调整云蓝/深海蓝主题、标题与证据面板入场、用户操作和历史展开动效；未添加装饰波浪。前端同步 MCP CLI 实际完成状态，网页连接仍清楚标为预览。
- 新增完整开发里程碑历史：UI-02、DOC-02、P0-02、UI-01、P0-01、DOC-01。按实际台账补录，不虚构已发布版本。
- 主目录真实 API 的原 9 项 E2E 与新增 8 项外观 E2E 已通过；新增短桌面回归及独立复审通过。36 个浅深色/视口/路由组合 axe 零违规且无横向溢出。
- 独立审查发现的 1280×720 侧栏历史入口不可达 P2 已通过桌面侧栏滚动修复，真实点击、浅深色键盘导航复审通过。最终整合命令与结果见 UI-02 评审/设计记录。

- UI-02 最终主目录整合：production build / TypeScript 通过；完整18项E2E通过（23.6秒），含新增短桌面点击与真实API流程。[最终整合记录](reviews/UI-02-integration.md)。


### P0-04 完成记录

- 运行状态与报告存入本地 SQLite（SQLAlchemy + Alembic）；claim 先提交，再执行工具；终态/事件/报告同事务。
- CAS 与 lease 阻止重复领取和晚到写入。宿主 SIGKILL 后显式对账为结果未知，保持原 attempt，不自动重投；默认30天保留，清理先dry-run且排除未确认资源与无可靠报告的中断记录。
- 任务总览解锁为“记录可用”，显示CLI真实运行列表、状态事件、摘要和报告下载；不把开发测试数量作为用户任务，不自动灌入合成任务记录。当前项目fixture场景的来源有明确标注。
- 340项完整Python回归零skip（21真实Docker、1公开历史案例），再纳入4项reviewer独立反例并通过；Ruff/format/mypy/lock和依赖公告检查通过。
- 主3010前端最终29项通过；唯一临时数据库专用用例在独立8031/3011套件执行，该套件20项通过，独立reviewer复跑通过并补下载取消负例。构建与类型检查通过。
- 实际CLI执行正常调用/响应丢失，经生产本地API→真实页面→下载文件核对SHA一致。只读网页接通；网页发起/取消/审批仍属于P1-01。
- 分批专职review通过，无未闭Defect P0/P1/P2；设计预审D01/D02已补齐并复审关闭。报告与源码SHA清单在reviews目录。
- 下一项按依赖为P0-05：三角色受限协作、已有DeepSeek兼容接口、预算及结构化角色契约。P0-04不宣称模型诊断或自动修复已经实现。

### DOC-03 完成记录

- 按用户要求统一产品闭环、三角色职责、工具输入输出/权限/副作用、自研与第三方边界、模型外发及项目记忆规则。
- 明确 T01～T08、A01～A15、R01～R11；测试是后续验收规格，不作为已通过用例。程序控制器不是第四个模型，P0-05不执行生成补丁。
- 独立文档初审发现任务意图提前截断、候选进程可伪造测试成功两项设计P1；通过任务分支与独立黑盒验收修复复审。
- 同步架构旧状态、SQLite报告BLOB、显式对账及已实现runner/run_store目录说明；七文档链接与矩阵核对，无运行代码/产品能力变更。
- 下一步 P0-05a 冻结角色/tool schema、前台协调与Agent摘要存储/API，之后接入T01～T05和现有DeepSeek；P0-05/06状态仍为planned。

### P0-05a 批次完成记录

- 批次 planned → ready → in_progress → in_review → done；父任务P0-05保持in_progress，不单独累计一份里程碑完成度。
- 独立设计预审后实现三角色严格契约、五类受控工具请求契约（尚无handler）、宿主权限/状态纯策略和AnalysisSummary类型；存储/API写入及模型循环仍属b。
- 实际GET /api/agent-contracts与前端目录接通；工具接入/诊断页可切换角色、查看schema、下载JSON；模型执行明确未接通，迭代历史同步。
- 395项非容器/非公开准备回归通过，22项原执行器门禁本次未重跑；86项后端定向含9个独立反例，前端8项主验收及5项独立故障探针通过；Ruff/format、无缓存mypy40生产文件、锁检查、TypeScript/build通过。最终417项可发现。
- 3个后端P2修复复审；独立[设计](reviews/P0-05a-design-review.md)、[后端](reviews/P0-05a-code-review.md)、[前端](reviews/P0-05a-ui-review.md)报告冻结。详见[实际验收记录](19-p0-05a-delivery.md)。
- 用户现有网关列出27模型；DeepSeek flash一次合成工具调用兼容验证成功，仅算接口探针，非Agent验收完成；密钥未保存。下一批实现P0-05b网关/真实工具/有界协作和安全结果持久化。

### P1-01a 顺序调整（2026-10-13）

用户要求最近运行页提供实际操作空间及复现命令。提前拆出已有P0-03/04支撑的固定场景网页复现，不提前实施模型修复/候选审批。设计预审D01（暂停旧worker穿越lease边界）通过owner存活锁及同锁对账关闭，设计复审通过。P1-01a进入in_progress；P0-05父任务和P1-01父任务均未完成。本批完成后继续P0-05b。

### P1-01a 完成记录（2026-10-14）

- 固定四种合成场景可从任务页发起、请求停止、查询真实进度与核验报告；提供配置下载与同场景 CLI 命令，技术标识折叠。
- 接收/run/claim 同事务，持久请求墓碑与跨进程 owner 锁阻止重复执行；取消请求不冒充最终取消，未知结果不自动重试。
- 显式迁移到 0002，旧运行/事件/报告保真；本机先一致性备份、停写升级、逐表摘要核对，再重启 API。
- 存储、服务/API、前端三批独立评审通过；真实四场景 Docker、隔离浏览器开始→结果→取消链路通过。具体测试计数与证据类型见[23](23-p1-01a-delivery.md)。
- 父任务 P1-01 仍 planned，P0-05 仍 in_progress。本批完成后继续 P0-05b；模型诊断效果、自动修复成功率尚无验收结果。

### P0-05b 实施记录（2026-10-14）

- 用户授权进入本批；设计planned → ready → in_progress → in_review，设计独立复审已通过。模型网关、持久预算、五工具和LangGraph有限协作进入独立代码评审与实测。
- 用户授权按官方模型上限配置现有网关；输入预算采用条件性信任，不称已验证网关强制上限。单次输出diagnosis/verification 4096、repair 8192，task默认2M预留额度不等于实际消耗。
- 网关评审修复整请求时限与合法usage丢失；工具评审修复原生取消遗留子任务。A14真实模型及A15前端门禁完成前，父P0-05保持in_progress。


### P0-05b 完成记录（2026-10-14）

- 本批与父 P0-05 已按 in_progress → in_review → done 完成。三角色有限协作、五类受控工具、现有模型网关、持久预算与只读诊断页已接通。
- 真实模型三次完整尝试全部保留：HTTP400未知、调用预算停止、最终 proposal_only；最后7次模型/4次工具/1份建议，14.131秒，实际输入41,644/输出1,541 tokens。不得计算为真实项目修复率。
- 默认Python554通过/30显式跳过；另28项真实Docker通过；前端82通过/3显式live跳过，运行记录专用21通过、分析专用1通过。静态检查/类型/构建/锁及运行依赖审计通过。具体命令、重叠计数与边界见[25](25-p0-05b-delivery.md)。
- 主库显式升级0003且原记录哈希保真；实现分批独立评审、缺陷修复复审、交付文档增量复审通过。诊断页提供真实记录、步骤、用量、证据与摘要下载，旧开发历史保留。
- 该历史阶段的下一项为P0-06；P0-05b交付时不产生candidate_verified、网页不发起模型分析。后续新增能力以表格台账及P1-01b/P0-06交付记录为准；记忆系统仍属于P1-02。

### 2026-10-15：UI-08与P0-06完成，进入P1-01设计

- 后端默认1396 passed/66 skipped；两案例真实Docker10 passed；完整生产浏览器181 passed/9 skipped。跳过项不计通过，真实链路单独验收。
- 两案例冻结模型实验12次全部保留，11通过、1不可判定；单角色5/6、三角色6/6，后者成本和平均耗时更高，不据小样本宣称优势。
- 独立代码/实链路/材料/文档复审路径见31/33。27项已登记UI P3归后续维护，没有未闭阻断项。
- 下一项P1-01仅开始设计：在现有修复页接入开始/停止、候选审阅和人工决定；B1详细契约尚待冻结，不把框架预审当成功能上线。

### 2026-10-15：阶段基线推送与P1-01 B1设计冻结

- 已验收P0-06/UI-08阶段基线推送为 `08d54e5`；敏感文件/发布范围独立核对通过，既有冻结生产文件未变。
- [B1详细规格](35-p1-01-b1-admission.md)补齐0008迁移、原子接收/取消、一次claim、超时恢复、跨槽范围及14项测试契约；[独立设计复审](reviews/P1-01-web-repair-design-review.md)通过。
- 下一批为B1a内部实现；B1b恢复终结依据及B2组合资源上限须分别验收。页面仍只读，不因设计完成标为网页可执行。


### 2026-10-15：P1-01 B1a实施

- 用户批准B1a，批次planned → ready → in_progress → in_review → done。显式0008迁移、严格请求、一次性claim、原子接收/取消/消费已实现，最终门禁见[36](36-p1-01-b1a-delivery.md)。
- 独立评审三项P1涉及私有令牌编码、consumed回退和锁生命周期，均已修复并补反例。全量1454 passed/66 skipped，静态/构建通过，125页与119站内链接无断链，独立复审通过。
- 本批不开放HTTP写入口、不迁移用户主库；站内文档更新，修复页面保持只读。下一批B1b，不跳到B2/B3。

### 2026-10-15：P1-01 B1b 实施

- B1a 已提交并推送 `40d3e34`，远端 CI 通过。
- B1b planned → ready → in_progress → in_review → done：共享执行循环、持久取消、原始截止时间、held-fence 恢复与正向证据释放已验收；全量1484 passed/66 skipped、独立复审Pass，前端构建与站内文档检查通过，见[37](37-p1-01-b1b-delivery.md)。
- 前端文档与更新记录同步；网页修复仍只读。B2/B3 API 与页面运行入口尚待实施，主库未迁移。


### 2026-10-15：P1-01 B2 API 与生命周期

- B1b 提交 `a68dde0` 已推送，GitHub CI 成功。
- B2 planned → ready → in_progress → in_review → done：固定案例目录、开始/查询/停止 API、独立 owner、跨 worker 停止与 shutdown 收尾已实现；同步修复旧复现/分析槽的未知创建释放与 attach 未确认错误传播。
- 两个子范围独立评审通过；全量1524 passed/66 skipped、前端生产构建与站内130页/124交叉链接验收通过，见[38](38-p1-01-b2-delivery.md)。
- 当前主后端/主库未切换；网页修复仍只读。B3 接入页面并受控迁移，B4 审阅决定，B5 真实完整链路验收；P1-01 保持 in_progress。


### 2026-10-16：P1-01 B3 网页操作与常驻服务

- B2提交`9195df9`已推送。B3 planned → ready → in_progress → in_review → done，网页开始/停止、严格pending、只读恢复、运行事件已实现。
- API/SQLite/生产Next代理的隔离浏览器链路通过；两个独立评审子范围分开验收，全量1551 passed/67 skipped、核心浏览器36 passed/1 skipped与补充2项通过，完整记录见[39](39-p1-01-b3-web-controls.md)。
- 主库已从0007显式迁移0008，17个旧表183行逐行字节/顺序摘要一致；用户要求本机前后端由launchd常驻，两个服务独立核验PPID1、loopback监听及HTTP200，常驻交付完成。
- 下一批B4为人工审阅决定与补丁下载，B5再完成真实模型/Docker/审批完整链路；父任务仍in_progress。

### 2026-10-16：P1-01 B4 人工决定与精确补丁下载

- B4 planned → ready → in_progress → in_review → done：已实现候选接受/拒绝、任务级 CAS、旧 request 幂等、追加式决定账本、当前证据失效显示和精确 diff 下载；不写回用户项目。
- 主库已从 0009 迁移到 0010；迁移会为旧决定补齐候选关联并建立历史账本。审批线程数有界，非法请求和下载大小/摘要/关联均在边界校验。
- 定向后端 15 passed，完整 Python 测试通过；前端 `repairs.spec.ts` 27 passed；Ruff（B4 范围）、mypy、TypeScript、production build、diff check 通过。独立后端/前端评审均 Pass，B4 已完成；B5 仍负责真实模型/Docker/整体验收。

### 2026-10-19：P1-01 B5 真实闭环交付

- B4 `31a5acf` 已提交推送。B5 planned → ready → in_progress → in_review → done，父 P1-01 完成；[交付与所有尝试](41-p1-01-b5-delivery.md)、[独立评审](reviews/P1-01-B5-quality-review.md)。
- Python 1589 passed / 67 skipped，真实 Docker 分层 10+3+1+2 项，修正后生产网页 42 passed，当前网关完整闭环 3 passed；Ruff全仓 lint/420格式、mypy138、TS/build通过。跳过项与命令错误分别记录，不凑成功数。
- 实际修复4次：3次候选通过/1次模型输出不合规；另有2次模型期间停止与首轮环境预检拒绝。最终两案例精确下载和撤回、10容器回收通过；不把最后一次成功推广为通用修复率。
- UI 增加紧凑操作指南与只读繁忙退避，站内文档和更新记录同步；可信单用户、两个内置案例、隔离候选与不自动写回的边界不变。
- 下一项 P1-02 先做经验记忆设计；P1-03 三项目接入和 P1-04 独立评测/使用验证仍未完成。

### 2026-10-19：P1-02 先行调研与设计

- B5 已提交并推送 `67b50d0`。用户要求先调研业界方案、单独形成实施文档；本轮不实现运行时记忆。
- P1-02 从 planned → ready → in_progress，M0设计按planned → ready → in_progress → in_review → done完成，M1 ready；[42调研与实施设计](42-p1-02-memory-research-design.md)覆盖一手资料、SQLite方案、T08/角色边界、删除并发语义、20项测试和M1–M5拆分；[独立设计评审](reviews/P1-02-memory-design-review.md)三项发现已修复并复审。
- B5的一次INVALID_MODEL_OUTPUT保留为模型完成率问题；REL-01补安全的拒绝阶段诊断，REL-02的预算内格式纠正另行设计。不能用记忆功能替代协议修复或掩盖失败。
- 站内工程文档加入入口，memory能力仍为planned；未安装第三方记忆框架、未迁移主库、未启动新的模型试验。

### 2026-10-19：REL-01 / REL-02 优先修复

- 用户明确要求先实现回复诊断与一次格式纠正；planned → ready → in_progress → in_review → done。规格和门禁见[43](43-rel-01-02-model-recovery.md)。
- P1-02 M1保持ready，记忆尚未实现；本批没有替换冻结回归、放宽预算或重放未知操作。全量1623通过/67跳过、专项35通过、生产UI42通过、真实网关与Docker三项门禁通过，独立复审见[报告](reviews/REL-01-02-review.md)。

### 2026-10-19：长期记忆业务与外部接入需求修订

- 用户要求将业务定位、记忆职责、Hook接入形式及UI操作路径落成专项文档；[44](44-p1-02-memory-product-and-integration.md)为当前依据。
- M0-B planned → ready → in_progress → in_review → done；独立文档评审通过，生产构建及7项站内阅读检查通过。本轮是文档与站内阅读交付，未安装引擎、修改数据库、开启采集或开发新运行能力。
- 42中的仅同版本查询、排除Cognee及原M1 ready属于旧范围；MR-01先验证公开引擎版本、来源/删除/费用与模型兼容，再冻结M1-R数据契约。原存储批次不直接开工。
- 权威账本与参考记忆分开；外部测试报告不自动成为内部验证事实；产品范围不扩大到任意仓库自动执行或多租户托管。

### 2026-10-19：MR-01 选型验证启动

- 用户授权进入MR-01，planned → ready → in_progress。检查公开发行包、依赖和许可证，隔离验证模型、来源、删除与预算契约；不改主库、不启用采集、不安装新模型。
- 运行实验和不满足项均保留；未验收前不推进M1-R实现。

- MR-01进入in_review：Cognee隔离SDK链路、既有DeepSeek与授权bge-m3、来源映射及墓碑拒用有实际证据；14项实验测试通过。依赖安全残余与生产未实现项详见45；不启用记忆运行功能。

- MR-01按in_review → done完成（仅选型实验）：[独立评审](reviews/MR-01-cognee-review.md)关闭3项P1，保留MR01-SC-01供应链P2；14测试、全仓Ruff435文件、前端构建/类型与7项文档浏览器验收通过。生产记忆未开放，下一项M1-R先冻结数据契约及迁移方案。

### 2026-10-19：M1-R权威领域与事件存储

- 用户授权实施，planned → ready → in_progress；[46](46-m1-r-authority-event-store.md)冻结schema/迁移/限额与验收。领域层不开放HTTP或Hook，M2-R接引擎，M4-R接客户端与管理页面。

- M1-R按in_progress → in_review → done完成；[独立评审](reviews/M1-R-authority-review.md)1项P1/1项P2修复复审关闭。全量1669通过/67跳过（最后只读helper前），最终专项46通过；Ruff445/mypy145、前端构建/类型与15项浏览器通过。0010→0011主库备份迁移，20旧表183行一致；9新表全空。前端边界说明、站内文档和launchd常驻更新完成。M2-R再接实际Cognee/模型与参考有效性，Hook客户端属M4-R。

### 2026-10-19：M2-R 引擎接入与参考复核

- 用户授权先推送既有成果，`f8a6f35`已推main；随后按planned → ready → in_progress → in_review → done交付[47](47-m2-r-engine-and-retrieval.md)。本批新改动尚未提交。
- 授权/策略/来源scope绑定实际请求预留，Cognee独立私有根及临时网关，exact/historical_hint、冲突与撤回拒用、账单/取消/物理清理接通本机SDK/CLI；没有接入产品Agent或Hook。
- 独立权威与运行评审共7项P1修复复审，UI锚点P2关闭；上游diskcache风险在可信本机边界书面接受补偿并持续跟踪。全量1720通过/68跳过，最后专项98通过，15浏览器通过。真实门禁3次：1unknown失败保留、2次通过，非业务效果统计。
- 主库0011→0012备份迁移，29旧表228行一致，前端CLI状态/文档/迭代历史与常驻服务更新。下一项M3-R不自动扩大外发或执行权限。
