# 企业代码质量审计报告 — P1-02 记忆设计

- **审计对象**：`docs/42-p1-02-memory-research-design.md`，及本轮架构、台账、文档索引增量；这是设计审查，不是记忆运行时代码验收。
- **审计版本**：B5 基线 `67b50d056213c00c1350c81c47de779d05b20866` + 下方 SHA-256 文件清单。
- **技术栈**：既有 Python / LangGraph / SQLAlchemy / SQLite / Alembic / FastAPI / Next.js；本轮未安装第三方记忆框架。
- **业务场景**：General SaaS，可信单用户本地工具修复工作台。
- **审查严苛度**：Mission-Critical，适用于 M0 设计闭合及 M1 开工判断。
- **审计时间**：2026-10-19（Asia/Shanghai）。
- **独立性**：专职 reviewer 只读取设计与直接相关源码，只写本报告；未修改实现或方案、未调用真实模型、未读取私密配置。

## 1. 代码质量评估卡

- **综合质量得分**：修订前 **79 / 100**；复审后 **100 / 100**，仅表示本次设计未留下已确认缺陷，不是生产可靠性或模型效果评分。
- **上线评估结论**：**✅ Pass / M0 设计可完成，M1 存储批次可进入 ready**。运行时记忆、T08、管理 UI 仍未实现；此结论不准予宣称功能上线，不关闭 P1-02 父任务。
- **审计范围**：单份设计及其架构/台账/站内索引增量，低于 2,000 行；核对既有 T01–T07、模型请求预留、角色上下文、案例授权边界、B5 失败记录。
- **未评估项**：实现代码、迁移实跑、模型效果、前端运行交互、第三方框架供应链与性能——本轮不存在该实现或未引入依赖，均不属于 M0 交付范围，必须由 M1–M5 后续门禁验证。

**缺陷汇总（未修复）**

| P0 | P1 | P2 | P3 |
|---|---|---|---|
| 0 | 0 | 0 | 0 |

**评分判定轨迹**

```text
STRICTNESS: Mission-Critical
SCENARIO: General SaaS
INITIAL: 2 confirmed P1 × 8 + 1 confirmed P2 × 3 = 19
INITIAL RAW: 81; P1 cap => 79
FIXED: D1 / D2 / D3 全部修订并独立复核
FINAL DEDUCTIONS: none; FINAL SCORE: 100
VERDICT: Pass for design only; M1 may start
NOT ASSESSED: 后续实现与效果，明确排除在本次设计交付之外
```

**核验证据**

1. 读取 `AGENTS.md`、约束、架构记忆段、台账、质量门禁和工具规格；设计仍符合已有精确过滤、人工确认、隔离候选、冻结验收、未知拒判的边界。
2. 读取 `repair_store/store.py::PERMISSIONS/_reserve/reserve_model`、`repair_runtime/controller.py::_model/_handoff`、`repair_runtime/context.py`、`model_gateway/gateway.py::decode_reply`、`repair_contracts/codec.py`、`repair_cases/profiles.py/bindings.py`。确认当前运行时只有 T01–T07；请求预留已有本地事务和预算，T08 与 memory_refs 是新增方案。确认案例 failure_code 属于验收器私有，设计明确禁止泄漏到模型提示。
3. 对照 B5 文档：`INVALID_MODEL_OUTPUT` 的泛化错误码确不足以定位精确字段或交接根因。设计没有把一次拒判描述成已修复问题，没有拿三次成功抹去失败，没有把记忆视为确定修法；REL-01/02 分离合理。
4. 2026-10-19 独立打开六类一手来源：LangGraph scope/Store、LangMem 热路径与后台整合、Mem0 当前 ADD-only 说明、Letta read_only blocks、Graphiti 时态/混合查询与图存储依赖、AgentCore episode/reflection。来源支持设计表内机制；“适配本项目”被明确标为工程推论。未跑这些框架基准，不为其性能或安全背书。
5. 本地链接核对脚本实际输出：`{'files': 4, 'missing_local_links': [], 'review_report_pending': True}`。四文件为设计、架构、台账、docs/README；当时唯一待生成报告为本文件。最终加入本报告重新核对：`{'markdown_files': 5, 'missing_local_links': []}`；`git diff --check` 退出码 0。没有将历史测试数量计作 P1-02 新通过项。

一手核验链接：[LangGraph](https://docs.langchain.com/oss/python/concepts/memory)、[LangMem](https://langchain-ai.github.io/langmem/concepts/conceptual_guide/)、[Mem0](https://docs.mem0.ai/core-concepts/memory-operations/add)、[Letta](https://docs.letta.com/v1-sdk/memory/memory-blocks)、[Graphiti](https://github.com/getzep/graphiti)、[AgentCore](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/episodic-memory-strategy.html)。

## 2. 生产事故风险推演

以下是已修正的设计反例，不是声称当前运行时已经发生事故。

### D1：第 51 条冲突绕过有限查询

- **触发条件**：同作用域/版本已有超过 50 条候选，第 51 条与前面的 rule_key 构成相反约束。
- **原失效机制**：先截断 50 条再检查冲突，只标 limited 而不禁止输出部分 items，可以把未经完整冲突核验的经验送给模型。
- **用户可见症状**：界面声称采用适用经验，但遗漏了相反约束。
- **爆炸半径**：该次模型上下文；冻结回归仍需执行，但错误参考已越过声明的检索边界。
- **复审结果**：51 条元数据溢出探针、最多 50 条来源核对；limited/unavailable 必须 items=[]、零注入，禁止通过排除原因泄漏正文；M13 必测第 51 条相反规则。

### D3：正文修订无法承担状态 CAS

- **触发条件**：两个客户端持相同正文 revision，同时撤回或删除一个已确认条目。
- **原失效机制**：只有产生新正文才增加 revision，而状态更新仅比较这个字段；先执行的撤回不改变 CAS 字段，后执行的删除仍可通过同一预期版本，破坏“一次获胜、一次冲突”的写契约。
- **用户可见症状**：第二位用户依旧使用过期视图完成变更，未收到应有的 409。
- **爆炸半径**：单条记忆与多条 supersedes 操作的一致性。
- **复审结果**：新增独立 row_version；所有正文或状态变更递增、同请求重放不递增，SQL 同时校验 expected_version/expected_revision/前置状态；supersedes 所有参与条目原子 CAS，M07 固定竞争反例。

## 3. 详细审计明细

### 🚨 P0 — 阻断性缺陷

none。

### ⚠️ P1 — 严重缺陷

#### D1. 有界候选检索未冻结截断后的注入行为 — 已修复

- **位置**：`docs/42-p1-02-memory-research-design.md:161–163` / §7.1，§9.1 M13。
- **维度**：5 性能边界、6 安全与数据合规、9 可测试性。
- **置信度**：`[confirmed]`，由上述 51 条候选反例直接推导。
- **最小设计补丁**：保留查询上限，增加单条溢出探针；无法穷尽时明确 `items=[]`，不需要引入无界扫描或新检索框架。
- **验证**：独立复读当前协议与 M13，文字已同时规定溢出/来源错误/超时零注入。实际查询测试属于 M1/M2。

#### D3. 状态写入与正文修订共用 CAS 字段 — 已修复

- **位置**：设计 §6.1 memories/memory_events、§6.2–6.3、§8 写接口、§9.1 M07。
- **维度**：3 并发、8 契约、11 数据与事务完整性。
- **置信度**：`[confirmed]`，同正文 revision 的两次状态更新构成反例。
- **最小设计补丁**：`row_version` 与 `current_revision` 分离；每次状态写入更新 row_version，所有写接口携带 `expected_version`，保留正文 revision 核对。
- **验证**：字段、SQL 前置条件、同请求重放、替代事务和并发测试矩阵已同步。不能用 scope_epoch 代替条目 CAS；实际并发测试属于 M1。

### ⚡ P2 — 主要缺陷

#### D2. 修改/续期时当前草稿与已确认修订的关系不明确 — 已修复

- **位置**：设计 §6.2 状态图及修订规则，§9.1 M06。
- **维度**：8 类型与契约、9 可测试性。
- **置信度**：`[confirmed]`，初稿只有一个 current_revision/status，却未定义新 draft 是否停止旧 confirmed 的使用。
- **最小设计补丁**：明确“立即失效再确认”：同事务撤回旧修订、指向新 draft、递增 epoch；编辑前告知用户暂停使用；撤回恢复也必须新修订/新确认；墓碑不可恢复。
- **验证**：状态图补上 confirmed/revoked→draft，旧 revision/row_version 确认 409，M06 同步覆盖。没有暗中引入第二个 active 指针。

### 💡 P3 — 次要缺陷

none。

## 4. 维度覆盖声明

| # | 维度 | 未修复发现 | 设计级结论 |
|---|---|---|---|
| 1 | 架构与边界 | 0 | 工作上下文/运行事实/经验分离；不另造 runtime 或独立 MCP Server |
| 2 | SOLID 与现代设计 | 0 | 存储、适用性、服务、API 分责；实现类结构尚未产生，本轮不评分 |
| 3 | 并发、异步与资源安全 | 0 | row_version CAS、模型预留线性化、查询期限明确；实际竞争执行留 M1/M3 |
| 4 | 容错与防御性编程 | 0 | unavailable≠miss，记忆失效降级，核心账本失败停止；无无界重试 |
| 5 | 性能与伸缩性 | 0 | 51 元数据探针/50 来源/3 条输出/4KiB/1秒约束；性能实测留实现 |
| 6 | 安全与合规 | 0 | 宿主绑定 scope、无任意自由文本外发、验证角色不注入、删除范围说明 |
| 7 | 可观测性与运维 | 0 | safe events、memory_uses、selection digest、REL-01 原因枚举；正文不进日志 |
| 8 | 类型安全与契约表达 | 0 | 状态、修订、行版本、幂等恢复已闭合；Pydantic 代码留 M1 |
| 9 | 可测试性与死代码 | 0 | M01–M20 含污染/删除竞态/失败保留；无新增实现，无死代码检查对象 |
| 10 | 工程规范与一致性 | 0 | 范围/文档/台账明确“未实现”；角色目录继续复用 |
| 11 | 数据与事务完整性 | 0 | 来源修订与确认同事务；删除墓碑/正文清除、备份范围、迁移恢复明确 |
| 12 | 依赖与供应链治理 | 0 | 不安装框架/向量库/新模型；第三方代码安全未评估且未作为本批依赖 |

## 5. 整改清单

| ID | 严重级 | 负责人 | 截止 | 状态 |
|---|---|---|---|---|
| D1 | Defect P1 | 主实现 Agent /root | M0 完成前 | 已修订并复审 |
| D2 | Defect P2 | 主实现 Agent /root | M0 完成前 | 已修订并复审 |
| D3 | Defect P1 | 主实现 Agent /root | M0 完成前 | 已修订并复审 |

无推迟项，无风险豁免。REL-01/REL-02 是文档明确的后续功能与试验，不是已解决的 B5 模型可靠性缺陷；REL-01 必须先于真实记忆效果评估交付。

## 6. 架构防腐与长效治理建议

1. **将失效判定共用到检索与模型预留事务**：一次查询结果不能一直有效。成本是 M2/M3 引入同一领域契约与并发反例，防止只在 UI 隐藏删除内容、模型仍重用缓存。
2. **分开运行事实和效果主张**：M5 报告全部尝试、无记忆/正确经验/错误经验三组，不能用“命中”证明收益。成本为文档冻结的有限评测预算；未建立收益则默认关闭注入。
3. **实现批次独立复审**：M1 必须实际验证迁移、备份恢复、CAS、正文删除和请求重放；M3 再验证预算和实际请求绑定。成本是分批测试和 review，防止以本次设计 Pass 替代运行验收。

## 7. 审计声明

- 结论仅对下列文件快照有效；内容发生变化需要增量复核。
- 本轮无模型/容器/记忆效果试验，不以阅读文档或历史 B5 通过计作 P1-02 运行验收。
- 设计范围全部完成审阅。后续实现与供应链基准不在此次范围，不作为已通过项目。
- 已施加豁免：none。全部发现均 confirmed 且已修复；无 suspected 项用于驱动结论。

### 审计快照

| 文件 | SHA-256 |
|---|---|
| `docs/42-p1-02-memory-research-design.md` | `231b6f8670389594c69f303883a215aa82463fb9812f663a357b47d463b43fa5` |
| `docs/02-architecture.md` | `9573e4eab66976dfe7c8388767505829988c9a17f245bce949f638f2b873133b` |
| `docs/03-roadmap.md` | `0fa24c5c9ccb03d9d821416f2b35b09fa771da4a12377128875ad49b42c4833c` |
| `docs/README.md` | `4fa3074199d4328d361267a4d1be63d733ab8458aad4fd38f60654dc6ece2f06` |
| `frontend/lib/documentation.ts` | `631515bfde8defc9268c36772ea64761bce65b417e2efff2b343ce1883129e02` |


## 附录 A：站内文档窄屏排版增量复审

- **增量范围**：`frontend/app/styles/docs.css:170` 与 `frontend/tests/document-reader.spec.ts:35`，初次为两行增量；随后加入设计 §6.2 的等价图语法变更与图像渲染断言。原五份设计/索引文件先独立确认全部 MATCH；仅设计文件的后续图语法修改再次复审，并更新上方 SHA-256。
- **问题证据**：实现者生产页面检查发现新文档在 390px 视口下，裸长标识符使页面扩展到 640px；桌面和 axe 检查不足以发现这一横向溢出。reviewer 不把该原始观察冒称为自己的浏览器测量。
- **修复核对**：`.doc-prose` 新增 `overflow-wrap: anywhere`，允许正常断行无法消化的长标识符折行；不修改原有色彩、字号、布局层次，不用整页 `overflow:hidden` 隐藏内容。`pre` 原有 `overflow-x:auto` 与保留空格规则继续存在。
- **回归核对**：新文档被加入既有 2 主题 × 2 尺寸（1440/390）的文档矩阵，直接断言 `document.documentElement.scrollWidth <= innerWidth`，并执行 WCAG2A/AA axe 检查；不是只检查新增 CSS 属性值。其余站内目录、交叉引用、404、代码块键盘和图表滚动测试保持。
- **独立静态结论**：无 P0/P1/P2/P3；该最小变更符合 frontend-design 的阅读排版/响应式要求。无新增依赖、权限、存储、API 或 Agent 行为，其他实现维度不适用。
- **第二项 UI 发现与修复**：初次 7 项通过（11.7s）未检测 Mermaid 降级源码，截图发现 `stateDiagram-v2` 不在阅读器允许语法中。设计 §6.2 改为等价 `flowchart TB`，reviewer 对照全部八条状态边：创建、确认、撤回、修改转草稿、撤回后转草稿、三条删除边均保留，deleted 无出边。未扩大 renderer 的指令/HTML/链接安全许可。此前设计 SHA-256 为 `7af11e8061f83efaba4d02e56f41cbd5181e8b8ad240dd28b14023f234892e1b`，当前值见上方最终清单。
- **回归加强**：每个 `.doc-diagram` 必须渲染可见 `img` 且 `naturalWidth > 0`，不能以源码降级替代图像成功。reviewer 读取 `document-diagram.tsx` 与文档页面组件，确认此断言能够抓到 unsupported diagram 分支。
- **最终浏览器验收证据**：实现者执行，reviewer 独立读取 `artifacts/p1-02-design/document-reader-final.log`，实际输出 `7 passed (11.8s)`；包含 2 主题 × 2 尺寸 × 4 文档的溢出/axe/图像断言，以及目录交叉引用、404、图表主题/键盘滚动。此前 11.7s 的通过仅代表较弱检查范围，未被用于替代此结果。实现者另报告 production build 通过及截图 `fixed-light-390.png`/`fixed-light-1440.png` 视口等宽；reviewer 另通过图像查看工具独立目视检查 390px 截图：标题、目录、状态段和正文在可见宽度内，未见裁切；scrollWidth 数值测量不冒称 reviewer 执行。
- **最终增量结论**：✅ Pass；无残留发现。P1-02 运行时与效果评估状态不受这次阅读器排版修复改变。

| 增量文件 | SHA-256 |
|---|---|
| `frontend/app/styles/docs.css` | `ff7f945afe0c994315f5951cb6eedc9a3fce7ce6b26efc31e997edda66984e58` |
| `frontend/tests/document-reader.spec.ts` | `e9f6435b6ac9071c5764220817248a8815d81646b189c1543fca41fb69a91327` |


## 附录 B：正式选型与交付批次澄清 — 2026-10-19

**本轮范围**：只评审设计第 1、3、7、10、11 节及架构文档末尾的选型澄清。此附录保留此前审计快照，不修改旧哈希、不重新宣称历史结果适用于新全文。REL-01/02 后续实现与交付不在此次增量范围；未改代码、运行模型或安装 Cognee。

**结论：✅ Pass，无新增 Defect P0/P1/P2/P3。** SQLite + SQLAlchemy + Alembic 与自研领域经验库可作为既定本地产品范围的正式选型；M1–M5 明确为同一架构下的实施/验收批次。M1 仍为 ready，P1-02 仍为 in_progress，记忆运行时未实现。

### 核对结果

1. **职责闭合**：选择覆盖形成、确认、检索、使用、失效、删除与审计；同库事务服务于来源决定、记忆版本、模型请求预留的原子核对，不仅是减少部署组件。自研范围限于业务领域规则，没有承诺自研数据库或通用 Agent 平台。该选型依据是合理的工程判断，不是已经验证实现正确性的声明。增量核对既有 `RepairStore.__init__` 复用 `RunStore`，`_reserve` 与审批决定均通过同一 `_base._transaction` 操作；因而把新经验表纳入同一权威库有当前代码基础，但实际跨表核对仍须在 M1/M3 实现与并发验证。
2. **范围不夸大**：第 1 节明确本地交付；第 3 节说明两类合成资源是验收范围而非完整产品用户范围；第 7 节保留当前仅注册两案例、真实接入仍属 P1-03。正式选型未被写成所有未来产品形态都必须使用 SQLite：多用户仍需独立需求与 ADR，不是预设迁移步骤。
3. **不作超额确定性承诺**：确定性针对宿主作用域、精确版本、规则过滤和排序；文档明确不承诺同义召回、跨版本/跨项目泛化、LLM 成功率。历史经验仍不能取代当前冻结验收，价值仍须对照实验验证。
4. **与既有门禁一致**：本轮没有改动 T08 两次查询/20 次工具预算、验证角色隔离、参考来源与当前证据分离、有界查询及有限降级。M1–M5 不再暗示“先临时实现、后替换引擎”，也不免除独立评审与负向测试。
5. **Cognee 排除理由有边界**：独立打开 [Cognee 官方文档](https://docs.cognee.ai/) 和 [官方 Postgres 方案](https://www.cognee.ai/graph-on-postgres)，确认前者列出 Python/MCP/HTTP 入口与嵌入式运行，后者说明图、向量及元数据可部署于同一 PostgreSQL 实例。设计没有以“必须三套数据库”作错误前提，也没有将未跑的性能测试写成比较结果。排除依据为当前结构化经验与精确适用性要求，不是否定 Cognee 的质量。
6. **架构文档同步**：末尾同样声明尚未实现、同库中逻辑分离、人工确认/严格版本/来源核验，并把 M1–M5 定义为交付批次；与设计一致，没有引入另一份权威架构。

### 增量门禁与边界

- 本次仅更新选型决策的表达与依据，已有生命周期/CAS/删除等设计结论保持；没有发现需要阻断 M1 的新矛盾。
- 本轮不运行 Python、模型或浏览器验收，不把之前的测试成绩当成本轮实现证据。设计可开工与功能可用仍分开记录。
- 十二维中的架构/契约/测试口径/依赖决策在本次增量内已核对；其他运行性能、资源安全与迁移实际行为不属于纯文档澄清范围，不能据此准予运行时上线。

### 本轮增量审计快照

以下是本轮读取版本；此前各节历史 SHA-256 保持原样。

| 文件 | SHA-256 |
|---|---|
| `docs/42-p1-02-memory-research-design.md` | `8a9a1f96fe717236b0cf2b673776d4240e8ad4857bf02bee953c795cfa2fc8df` |
| `docs/02-architecture.md` | `0d4222437dd631345eb30b3491145c15ebe46604b18f53cd51ed37d0bdf887bb` |
