# M3-R：Agent 经验取用与修复经验草稿

2026-10-19设计，2026-10-20交付。Feature P1，状态 done（planned → ready → in_progress → in_review → done）。M4-R及本批真实模型、生产网页与独立评审均已通过。用户已批准 M4-R 网页管理与 Hook 优先，随后实施本批；M4-R 验收是本批执行前置条件。依据 [44](44-p1-02-memory-product-and-integration.md)、[47](47-m2-r-engine-and-retrieval.md)、[48](48-m4-r-web-memory-hooks.md)。

## 1. 本批业务闭环

用户在诊断或修复开始前，显式选择项目与经验来源。宿主按当前受控案例的工具、源码版本生成一次检索请求；诊断与修复角色可看到经过确认、授权、有效性复核的参考经验。每次实际模型请求均记录使用的经验版本，验证角色仍只依据本次可执行证据和冻结回归。候选通过本次冻结回归且用户接受后，用户可选择保存经验草稿；草稿仍需在经验页确认、授权和建立索引，才可能被后续任务取用。

本批继续只执行既有固定案例，不把外部 Hook 事件变成任意仓库执行入口，不自动确认经验，不把历史成功作为本次通过的证据，也不宣称记忆提高修复成功率。收益结论属于 M5-R 对照评估。

运行范围仍为可信本机单用户；不新增多租户认证、云端公开回调或任意项目执行权限。记忆默认不启用，不选择时不改变原来的模型、工具、预算与失败处理行为。

## 2. 输入与宿主绑定

诊断 `AnalysisRequest`、修复 `RepairRequest` 新增可空字段：

```json
{"memory":{"project_id":"demo","connection_id":"local-reference","allow_historical":false}}
```

`MemoryBinding` 使用严格 Contract、Key 与布尔类型。请求指纹包含完整绑定，持久接收记录恢复出同一绑定；相同 request_id 修改项目、来源或历史策略返回冲突，不能复用旧任务却更换记忆来源。未选择记忆时不检索、不收费，兼容既有请求与离线测试。

无记忆请求继续使用原 version=1 指纹编码且不序列化新增空字段；有记忆请求使用明确的 version=2 编码。旧数据库 JSON 未含 memory 应解析为 None，不能因新增字段让历史相同请求变成幂等冲突。

客户端只选择已存在项目、连接及是否参考历史版本。以下字段由宿主从已核验 registry 得出，API 禁止覆盖：

| 字段 | 权威来源 |
|---|---|
| task_id / task_kind | 已持久接收的诊断或修复任务 |
| tool_id | 固定案例 registry 的目标工具 `lookup` |
| source_version | `TaskResourceRegistry.source_sha256()`；对应修复案例 `get_profile(case_id).source_sha256` |
| 查询文本 | 宿主固定模板 + 安全错误分类，最长 1,024 字符；无任意网页输入、日志或源码 |
| event_id | 任务绑定的确定性 UUID；同一任务重放不能产生第二次检索 |
| provenance | 合成案例，仅表示本次宿主观测；不能借此将项目其他经验标成合成 |

`check_only + memory` 与 `single_role + memory` 在接收前拒绝，并提供可理解提示。单角色模式混合诊断、修复和验证，当前无法证明验证阶段不继承记忆；不能仅在 system prompt 中要求忽略。

## 3. 一次检索与资源预算

T08 是宿主受控的前置经验检索步骤，不是模型可重复调用的任意搜索工具。任务完成接收并进入运行状态后、首个 diagnosis 请求之前运行一次。重启或未知结果不得自动重放检索。

1. 同事务保存 task-memory 绑定和检索占位，再执行已有 `CogneeRuntime.search()`；`on_job` 回调绑定唯一 memory job_id。
2. 仅查询既有确认经验和已完成索引；运行中不自动 index/cognify，不创建第二套引擎目录。复用 M4 的同一 authority、私有 engine-root、SDK 与 provider 配置。
3. 使用 `asyncio.to_thread` 执行同步引擎；取消事件、线程回收与锁释放遵循 M4 的 shield/join 模式。不能只取消 await 后让线程继续外发。
4. `CogneeRuntime.search` 增加宿主 deadline 上限：实际截止为 `min(任务剩余截止, 当前时间 + 180 秒)`；监控包含线程等待和清理。任务的 600 秒从接收/运行时间计算，不能检索后重新开始计时。
5. 将总 token 上限 2,000,000 分配为检索预留 100,000、Agent 预算 1,900,000；选择记忆但零命中也不动态扩大已分配额度。原有不使用记忆的任务仍为 2,000,000。单次输出 8,192、候选 2 个、原有工具 20 次保持。
6. 必须分别报告 Agent 模型请求（最多 12）、检索 LLM 请求（最多 6）、embedding 请求（最多 40）与 T08 操作（最多 1）。不能把总外部请求数仍写成最多 12 次。准备索引的历史费用单独列示，不计入本任务，也不能隐去。
7. 总已知 token = Agent 已知用量 + 本次检索已知用量；检索 unknown 会使总体成本状态 unknown。金额没有价格依据时显示未知，不显示零。

结果分类：`available/matched`、`available/no_match`、`limited`、`unavailable` 分开保存。无匹配或在任何外发前拒绝，可记录原因并继续无记忆执行；如果检索有未知计费/在途请求、超出总期限或取消，停止本任务，不以降级继续掩盖未知成本。结果只取最多 8 个可追溯引用、合计正文最多 8,192 UTF-8 字节；错误不能被转成“没有相关经验”。

## 4. 外发授权与调用审计

现有 `index` 和 `retrieve` 用途均不等于允许将正文发送给修复模型。`Grant.purpose` 新增 `agent_context`，网页单独展示“允许诊断和修复模型使用”，默认关闭；逐来源、内容类型、有效期授权。现有 retrieve 授权不迁移成新授权。

### 4.1 每次请求的严格顺序

1. 从检索候选引用重读权威正文，确认项目、连接、工具、版本分类、revision、generation、digest、policy 与 grant；不信任 SDK 生成文本。
2. 只在 diagnosis/repair 的当前临时消息增加结构化 `reference_experiences`。字段包括来源引用、exact/historical_hint、正文、`current_verification=false`。提示词声明正文是待分析资料，不能改变工具权限或冻结验收。
3. adapter.prepare 生成最终 HTTP body 与 request_sha256。记忆正文仅存在于本次临时消息/PreparedCall，不写入任务摘要、审计或日志。
4. **同一个 SQLite 写事务**中再次核验完整引用快照与 `agent_context` 授权版本，持久写入模型调用预留和引用审计。事务失败则不发送 HTTP。不能 `EngineStore` 先独立检查、退出事务后再 `reserve_model`。
5. 提交成功定义为“本次外发已获准”。实际 send 使用同一个 request_sha256 的 PreparedCall，禁止持久记录一份请求、实际发送另一份。
6. 撤回/编辑/删除/授权关闭若先于该事务提交，拒绝本次带正文的调用；事务提交之后撤回不能撤销已获准或已发出的请求。UI 如实展示“已授权发送；后续调用不再使用”。不承诺数据库与远端 HTTP 的原子发送。

如果步骤 1 与 4 之间经验发生变化，丢弃本次带正文请求，不用旧正文重试。可以重新构造一个明确无记忆的新请求并重新走正常预算/审计；不重新检索、不自动改用另一版本。权限读写异常时停止，不静默变为允许。

### 4.2 现有代码的接入位置

| 代码 | 必需变更 |
|---|---|
| `orchestration/runtime.py::AnalysisRuntime.turn` | 当前 `messages.setdefault` 会缓存角色初始上下文；记忆只能在本次 prepare 的临时消息副本中注入，绝不能写入 `self.messages`。撤回后下一次请求不能残留旧正文。 |
| `analysis_store/store.py::save/_save` | 在 `model_started` 的同一写事务中接收宿主记忆快照并执行复核/审计；其他事件不可伪造记忆使用。 |
| `repair_runtime/controller.py::_model` | 每轮本就新建消息；仅 diagnosis/repair 追加参考块。verification 不传正文、不传前一角色原始回答。 |
| `repair_store/store.py::reserve_model/_reserve` | 将复核/审计加入当前 reserve 的写事务，而非新增并行事务。request_id 重放只读取原审计，不追加引用或外发。 |
| `providers/cognee/runtime.py::search` | 接受宿主截止时间并继续保持已有子进程、网关、lease、writer 锁约束。 |
| `web_analyses/service.py`、`web_repairs/service.py` | 持久绑定、按分配后预算创建 registry/task，构造同一 authority 的记忆会话；接收后才运行前置检索。 |

验证角色只能读取本次重现、候选、冻结比较和安全摘要。审计接口可以展示它没有引用经验；不能把历史经验正文放进共享 `tool_materials`，也不能让“诊断转述”把整段经验传播给 verification。

## 5. 0014 持久化与最小模块

迁移命名 `0014_agent_memory`。先备份主库，显式迁移，核对既有表行数与摘要；启动时不自动迁移。新增表的非空降级拒绝。外发正文不入新增表。

0012 的 `memory_grants.purpose` 有 SQLite CHECK，只改 Pydantic Literal 不够；0014 须保留全部旧行重建该表以允许 `agent_context`，保留主键、外键、索引和旧授权语义。降级遇到新用途授权同样拒绝，不能静默删除。

| 表 | 最小字段与不变量 |
|---|---|
| `memory_task_bindings` | `(task_kind,task_id)` 唯一；project_id、connection_id、binding_digest、tool_id、source_version、allow_historical、query_digest、job_id、state、reason、检索预留上限、created_at。job_id 首次绑定后不可替换；未绑定但运行中断标 unknown，不能重跑。 |
| `memory_model_uses` | `(task_kind,task_id,call_id)` 唯一；role、request_sha256、admitted_at、body-free refs JSON。refs 包含 memory_id/revision/generation/digest、source/version、grant/policy/connection 版本、classification。verification 记录空引用或禁止插入正文引用。 |
| `memory_repair_drafts` | `(task_id,candidate_id)` 唯一；project_id、memory_id、request_id/fingerprint、decision_revision、candidate_sha256、comparison_sha256、created_at。避免换 request_id 重复保存同一候选。 |

建议新建 `experience_memory/agent_models.py`（绑定/选择/审计 DTO）、`agent_store.py`（可复用的 `Connection` 级复核和审计）、`agent_runtime.py`（一次检索、取消与临时正文）、`repair_drafts.py`（BH-06 权威检查和草稿）。不会新增产品 Agent 角色，现有角色代码仍各自维护。

关键领域方法建议：`bind_task(...)`、`attach_retrieval_job(...)`、`finish_retrieval(...)`、`prepare_context(...)`、`admit_context(connection, task_kind, task_id, call_id, role, request_sha256, selection)`、`task_usage(...)`、`save_verified_repair_draft(...)`。事务级方法使用调用方 SQLAlchemy Connection，不调用内部开新事务的公开方法。绑定对象和引用快照属于宿主能力，不作为浏览器可上传的“已授权凭证”。

## 6. BH-06：明确保存经验草稿

修复候选操作区增加“保存经验草稿”，仅当前 `candidate_verified` 且人工 accepted 后可用。用户选择目标项目/本地来源，查看将保存的摘要；点击才写入。无需再调用 LLM总结，也不在接受按钮里偷偷写经验。

实际 API：`POST /api/repairs/{task_id}/candidates/{candidate_id}/memory-draft`。输入仅 request_id、project_id、connection_id、expected_decision_revision、expected_candidate_sha256、expected_comparison_sha256；不接受客户端正文、passed 标志或源版本。新路由沿用本机 Host/Origin 与写入体积/时间限制。

在 `RepairFence` 下同一 SQLite 写事务读取当前 candidate/comparison/accepted decision；复用 `DecisionStore` 的当前证据校验及 `verify_versions` 文件/镜像绑定。来源连接须属于目标项目、启用且为 local_user 或内部 synthetic 来源，外部事件凭据不可调用此管理操作。

正文由确定性模板生成：受控案例名、安全错误分类、工具/源码版本、修改路径、候选/比较摘要哈希、“曾通过该次冻结回归并被接受，不证明未来版本通过”。不保存完整补丁、隐藏测试、原始模型回复或输入值。按模板确有支持的修复动作描述，不能仅凭通过状态臆造根因。

写入事件、草稿、来源证明与幂等回执使用同一事务，成功返回草稿链接。草稿状态永远 draft，默认没有新用途授权、没有自动索引。后续人工改变原候选决定时，详情展示来源当前决定；草稿确认不应绕过已变化来源的提示。已确认经验仍按自身授权/有效性管理；不能悄悄删除既有审计。

## 7. 前端/API 展示

- 诊断与修复开始区用默认折叠的“参考项目经验”选项；展开后选项目/来源和历史开关。模式切为单角色时禁用并解释，不悄悄保留不可执行选择。
- 运行详情用紧凑“参考经验”区域：匹配数、匹配版本/历史提示、此次是否实际发送、来源跳转、当前是否已撤回。检索候选与实际调用引用分开展示，不能把命中就写成已使用。
- `GET /api/analysis-runs/{task_id}/memory`、`GET /api/repair-runs/{task_id}/memory` 返回安全审计与总成本分项；不返回正文副本。正文查看跳到经验页当前权威记录，删除后不可从旧任务接口恢复。
- “没有匹配经验”“检索不可用”“权限已撤回”“预算未知而停止”分别呈现。未知费用不能显示 0。
- 保存经验草稿后直接跳经验详情继续审核。保留现有紧凑中性布局、浅/深色、键盘焦点和窄屏；更新 capabilities、模块入口、milestones 与本地文档。

## 8. 验收与评审批次

| ID | 必须验证的输入/故障 | 预期结果 |
|---|---|---|
| MR3-01 | 请求不带 memory / 同 ID 更换 binding | 原行为不变；后者冲突，无追加检索或模型请求 |
| MR3-02 | 未授权 agent_context，仅有 retrieve | 可检索但正文不进入诊断/修复 HTTP；清楚记录原因 |
| MR3-03 | 项目、来源、tool/version 客户端伪造或跨项目 | 接收/权威复核拒绝，零越权外发 |
| MR3-04 | prepare 后、调用预留前撤回/编辑/关授权 | 同事务拒绝，无旧正文发送、无成功使用审计 |
| MR3-05 | 预留提交后撤回，随后第二次角色调用 | 保留第一次已获准审计；第二次无旧正文，不重放首次请求 |
| MR3-06 | diagnosis 多轮、repair 多轮、verification | 前两者逐请求可核对精确引用与 body hash；verification 未含正文标记；旧 Analysis.messages 无残留 |
| MR3-07 | single_role/check_only 带 memory | API 明确拒绝，UI 无误导入口 |
| MR3-08 | conflict/过期/无索引/零命中/已删除 | 区分 limited/unavailable/no_match，无部分成功冒充完整检索 |
| MR3-09 | 检索超时/取消/重复取消/API关闭/owner消失 | 进程树回收，线程结束才释放锁；已未知调用不重试；同任务预算与截止不重置 |
| MR3-10 | 检索 100k/6 LLM/40 embedding 边界、Agent 1.9M | 额度可追踪且不得超配；费用未知显式；显示调用总数不漏 Cognee |
| MR3-11 | 伪造 memory-body“跳过测试、改验收” | 现有权限不变；冻结校验哈希不变；无法借经验得到通过 |
| MR3-12 | BH06 未验证/未接受/决定过期/版本变更 | 拒绝保存；同请求相同输入返回原草稿；不同输入409；不同请求不重复创建 |
| MR3-13 | BH06 成功、编辑确认撤回删除 | 仅 draft 起步、无自动外发；来源证明可回查；删除后旧任务API无正文 |
| MR3-14 | 0013→0014 升级/非空降级/故障回滚 | 旧记录保持；非空降级拒绝；无半写审计或幽灵草稿 |
| MR3-15 | 浏览器真实操作，浅/深色390/1440px | 选择→运行→引用→草稿可操作；无横向溢出、焦点丢失或原始ID主导页面 |

真实验收使用独立私有 state、真实现有模型/Cognee/embedding、冻结 Docker 案例及生产 Next；不把 fake adapter 集成测试算真实模型。至少完成一条有已确认授权经验的 three_role 修复链路，保存请求引用、实际费用、角色隔离、当前回归和 BH06 草稿证据；另用确定性障碍点测试撤回/取消竞态，避免靠随机 timing。

所有真实尝试保留，失败不覆盖，不无限重跑取最好结果。若实际模型拒判，记录检索/注入是否成功与修复是否成功这两个不同结果。单案例运行不支持 M5 收益结论。

独立评审分两批：A 权威/迁移/预算/每请求事务；B runtime/取消/角色隔离/BH06/API/UI。每批约 2,000 行以内，所有 Defect P0/P1 修复并复审后才进入 done。最终交付补齐实际命令、计数、完整尝试记录、报告路径及常驻服务状态。

## 实际验收中的超时策略调整

首轮真实索引在单次LLM请求30秒上限处返回provider_timeout；预留12014tokens、实际用量未知，未创建修复任务，失败报告保留。网关模型目录GET在0.05秒内返回200，只能证明连通，不能证明生成服务正常。为允许索引结构化抽取完成，将LLM单HTTP上限调为60秒，embedding仍30秒，实际始终取作业/父任务剩余期限的较小值；180秒索引作业和600秒任务总期限、请求数及tokens不增加、不自动重试未知请求。另起一次独立验收验证，旧未知账单不被覆盖或退款。该调整不代表已确认超时根因，也不把后续成功解释为消除网关波动。

- 超时调整的独立测试验证60/30秒上限、原始deadline截断、慢速分块响应和取消关闭连接；停止标志遇到停滞socket可能等待剩余HTTP期限，不承诺立刻中止远端计算。首轮未知费用仍保留。

## 9. 实际运行与缺陷闭环

真实验收不使用用户项目资料；每次隔离库均独立创建同一条明确标记的合成经验，再调用现有模型/embedding、Cognee 与冻结 Docker 案例。失败不删除、不覆盖；修复任务结果与记忆取用结果分别记录。

| 尝试目录（artifacts/m3r/） | 实际结果 | 后续处理 |
|---|---|---|
| `27463015-3cb4-4312-89bf-33f94ed33805` | 索引首个 LLM provider_timeout；实际用量未知，预留12,014 tokens；未创建修复任务 | 未重试该请求、未退款未知预留；资源已清理。另起尝试检验上述有界超时调整 |
| `d6590c9c-a2be-4f5d-8ceb-c330c14b432f` | 索引成功；检索 matched，diagnosis/repair/repair 每次引用1条。T06工具失败，未进入verification；任务inconclusive，完整验收失败 | 找到运行槽释放的BLOB序列化缺陷并修复；旧T06只存TOOL_FAILED，无法追溯具体原因，未臆测为模型错误。原acceptance保持不变，terminal-memory-audit与recovery-after-fix分别补充只读审计/回收结果 |

第三次独立尝试 `7af0dbc2-f36b-4407-ab0b-3793ce882085`：**记忆链路和修复链路均 passed**。索引2次LLM、4次embedding，共7,220个已知tokens；任务检索0次LLM、2次embedding，共32tokens。Agent共6次模型、6次工具请求，已知36,430tokens；任务检索加Agent合计36,462tokens，准备索引费用另列。金额缺少定价依据保持未知。

diagnosis一次、repair四次请求各引用1条，verification一次请求引用0条；每次均有对应请求摘要和授权审计。冻结回归四格为基线故障失败、基线正常通过、候选故障通过、候选正常通过；当前证据verified。人工接受API后BH06只生成draft。执行槽active=false、pending_operation=null，T05/T07的5个容器均确认不存在。单个合成案例成功不证明通用修复率或记忆收益。

### 9.1 实际验收暴露并关闭的缺陷

- **Defect P1：终态运行槽无法释放。** M3在持久接收记录新增BLOB绑定，原release stamp直接JSON编码整条记录触发TypeError。现将通过契约校验的绑定字节摘要纳入完整stamp，保留变化检测；独立测试通过真实WebRepairRecovery.release_checked验证有记忆任务释放，未绕过资源核验。
- **Defect P2：候选失败原因过粗。** T06原将所有确定性CandidateError合并TOOL_FAILED。现通过16个固定白名单码保存既有error_code字段，未知文本继续降为通用错误，提交未知仍为RESULT_UNKNOWN；无异常原文/补丁正文入日志，没有新增自动重试。旧记录仍是原有未知具体原因。
- **Defect P1：新增安全码前端未识别。** 独立评审发现Zod枚举遗漏会让整条修复详情解析失败，已同步枚举和用户可读说明并添加候选失败浏览器回归。
- **Defect P1：生产前端代理缺少M3路由。** 真实无mock浏览器验收发现后端经验审计200但Next返回404；补齐诊断/修复经验审计和候选草稿的三个精确代理规则。mock测试不会覆盖代理，生产构建与真实API浏览器验收独立验证修复。
- 权限准备后新增冲突约束、终态经验一直等待、旧CLI任务兼容、预算与当前状态刷新等问题均在对应独立报告记录修复和复审。

### 9.2 独立评审与测试分层

- [权威/迁移/调用事务](reviews/M3-R-authority-review.md)：Pass；逐请求复核、全检索范围冲突、角色隔离与旧授权不扩权。
- [网页/API/草稿/恢复](reviews/M3-R-web-review.md)：Pass；包括真实验收暴露的release stamp修复，以及安全错误码白名单。
- [前端](reviews/M3-R-ui-review.md)：角色选项、当前权限状态、费用分项和保存草稿；独立浏览器与实际API结果分开报告。
- 全量锁定Python第一次最终运行1,841 passed / 69 skipped / 1 fork弃用warning；随后针对真实验收修复补充102项、独立79项回归。2026-10-20最终全量复跑为1,868 passed / 69 skipped / 1 warning（270.92秒），日志artifacts/m3r/final-pytest-20261020.log；警告为既有多线程fork弃用提示，跳过不计为通过。

### 9.3 主库升级

显式停止常驻服务、备份后0013→0014，37张旧表238行的原有列逐项摘要一致，外键与integrity_check通过。两个新增可空绑定列不与旧SELECT *混比。证据 `artifacts/m3r/migration-62b2709c-1398-4dcb-936a-c1bade2e67b4/acceptance.json`，备份位于同目录，未提交Git。

### 9.4 真实生产网页复验（2026-10-20）

独立评审在隔离生产Next与真实API/SQLite上执行 `agent-memory-live.spec.ts`：1 passed（3.7秒），没有mock，也没有新模型调用。读取第三次验收任务的引用和费用、恢复既有BH06草稿并打开来源详情；页面没有成功写操作。修复经验审计返回200；不存在的诊断任务得到后端JSON404；空草稿请求得到后端契约400，三个精确rewrite均已贯通。M3UI-04已复验关闭。含此前35个不同浏览器用例，共36项通过；其中真实M4管理和真实M3已有结果浏览各1项，其余是受控交互/负向测试，不能统称36次真实模型验收。

证据：`artifacts/m3r/browser-a2c1d2f4-59bb-4382-b357-67d8d6787e11.json`及同名截图；评审者未再执行修复或改写原acceptance。

## 10. 交付结论与边界

M4-R网页管理/Hook先交付，随后M3-R完成Agent参考与BH06草稿；没有扩大为任意仓库自主修复。全部独立评审发现的P0/P1已关闭。Ruff检查和505文件格式检查、mypy171源文件、生产Next构建通过。网页默认不选记忆，授权/确认/索引均保留显式操作。列表和详情的长版本摘要缩写展示，完整值保留在提示和编辑/技术详情中。

三次真实验收分别为索引超时、修复失败并暴露释放缺陷、修复后完整通过，记录全部保留；不能将一次成功解释为稳定性或记忆收益已达标。下一项M5-R需做同模型/同预算的有无记忆对照，独立报告修复率、回归率、成本和人工步骤。本批不提前宣称收益。

2026-10-20常驻发布：默认生产构建及TypeScript通过后由launchd重新加载API8030/Web3010，两进程PPID均为1；health、项目经验、修复页、本站交付文档、更新记录和项目API均HTTP200。证据artifacts/m3r/resident-20261020.json。隔离验收服务已回收，密钥/私人配置/原始运行产物未入Git。
