# 企业代码质量审计报告：M3-R 网页绑定与修复经验草稿

- **审计对象**：`experience_memory/repair_drafts.py`、`web_memory/repair_draft_api.py`、`agent_service.py`、`agent_api.py`；`web_analyses` / `web_repairs` 的 models/store/service 绑定增量及 API 注册。
- **审计版本**：2026-10-19 工作树，规格 `49-m3-r-agent-memory.md`；最终版本包含 memory-only v2 指纹及终态投影修复。
- **技术栈**：Python 3.12、FastAPI、Pydantic、SQLAlchemy/SQLite、既有 RepairFence。
- **业务场景**：General SaaS；可信本机单用户。
- **审查严苛度**：Mission-Critical。
- **审计时间**：2026-10-19。

## 1. 代码质量评估卡

- **综合质量得分**：100 / 100（最终未关闭增量缺陷计分）。
- **上线评估结论**：Pass，限本批网页持久绑定、BH06 与安全投影范围。
- **审计范围**：约 650 行实现增量，以及直接依赖和相关测试；评审者未修改生产代码，新增 `tests/test_agent_memory_web_review.py` 五个独立用例。
- **未评估项**：本分配范围内 none。核心 M3 权威/模型请求原子授权/角色运行时由另一评审批次负责；生产浏览器及真实模型整体门禁由交付记录负责，不能以此报告代替。

| P0 阻断 | P1 严重 | P2 主要 | P3 次要 |
| :---: | :---: | :---: | :---: |
| 0 | 0（1 已关闭） | 0（1 已关闭） | 0 |

```text
STRICTNESS: Mission-Critical
SCENARIO: General SaaS
DEDUCTIONS: no open incremental findings → 0
RAW SCORE: 100
CAP APPLIED: none after fixes
FINAL SCORE: 100 / 100
VERDICT: Pass for assigned web integration scope
NOT ASSESSED: none within assigned scope
```

评分不代表第三方依赖无风险、商业效果已验证或整个 M3-R 已完成。M2 的私有目录、既有模型配置、条件性 token profile 与单用户边界继续有效。

## 2. 生产事故风险推演

> **场景 — 任务已经取消，经验状态永久等待（已修复）**
> 触发：带 memory 的任务持久接收后，在第一次 prefetch 之前被取消；或 begin_task 在创建绑定前拒绝失效来源。父任务已结束，但没有 `memory_task_bindings` 行。初稿 `agent_api.usage` 只看该行是否存在，持续返回 pending。用户刷新也只能看见等待，无法区分没执行与仍在调用。独立测试通过真实 SQLite 接收/取消/终态写入复现，模型调用为 0。修复后先读取父任务权威终态；未开始返回 unavailable / retrieval_not_started，终态遗留 running 返回 unknown / retrieval_interrupted。

爆炸半径为对应任务的经验状态展示和排障；不构成已收费请求被清零。实现者 `/root` 在 M3-R 完成前修复，复审关闭。

## 3. 详细审计明细

### P0 — 阻断性缺陷

none。

### P1 — 严重缺陷（已关闭）

#### M3W-01. 父任务结束后仍投影 pending

- **位置**：`src/agent_workbench/web_memory/agent_api.py::usage`。
- **审查维度**：4 容错、7 可观测性、11 权威状态一致性。
- **置信度**：confirmed。
- **问题分析**：没有 retrieval 行不等于尚待执行；父任务取消和前置拒绝不会再创建该行。缺少终态检查会永久显示进行中。
- **最小修复**：在同一读取事务内由 AnalysisStore/RepairStore 读取父任务阶段；结束且无绑定标 unavailable，结束但残留 running 标 unknown；没有 memory 仍返回 disabled。
- **验证方式**：`test_cancel_before_prefetch_does_not_project_forever_pending`。初轮失败，修复后通过；请求从真实任务存储完成接收/取消/运行终止，未调用模型。

### P2 — 主要缺陷（已关闭）

#### M3W-02. 新绑定指纹未使用已冻结的版本编码

- **位置**：`web_analyses/models.py::AnalysisRequest.fingerprint`、`web_repairs/models.py::RepairRequest.fingerprint`。
- **审查维度**：8 契约版本、10 规格一致性。
- **置信度**：confirmed。
- **问题分析**：doc49 明确无记忆沿用 v1，有记忆使用 v2；初稿全部写 v1。虽然新增字段已进入摘要且没有直接跨源碰撞，编码与冻结契约不一致会影响后续迁移、解释和兼容判断。
- **最小修复**：仅非空 memory 使用 v2；None 不序列化，保留旧 v1 字节摘要。
- **验证方式**：两个 DTO 的独立精确 canonical/hash 测试；旧请求指纹不变，新绑定为 v2，客户端 tool/query/字符串布尔覆盖被拒绝。

### P3 — 次要缺陷

none。

## 4. 维度覆盖声明

| # | 维度 | 发现数 | 结论 |
|---|---|---|---|
| 1 | 架构与边界 | none | 复用 M2 factory 与宿主配置，HTTP 不接受正文凭据或已授权快照 |
| 2 | SOLID 与现代设计 | none | BH06 单独模块，路由不重复决策规则 |
| 3 | 并发、异步与资源安全 | none | BH06 使用 RepairFence；API 通过有界 invoke 工作线程运行；M3 prefetch 取消归运行时批次 |
| 4 | 容错与防御性编程 | M3W-01 已关闭 | 终态/未开始/未知投影分开；重复保存读原草稿与当前证明 |
| 5 | 性能、伸缩性与计算效率 | none | 单候选事务、现有 case/snapshot 受限；不在 BH06 追加模型总结 |
| 6 | 安全与数据合规 | none | 本机 Origin/header；外部来源凭据不授予 BH06；模板不存补丁/隐藏测试/模型原文 |
| 7 | 可观测性与运维友好度 | 关联 M3W-01 | 原始使用引用与当前 head 状态分开；删除后旧 API 不返回正文 |
| 8 | 类型安全与契约表达 | M3W-02 已关闭 | 严格绑定 DTO，single_role/check_only 拒绝记忆，客户端无 tool/version/query override |
| 9 | 可测试性与死代码 | none | 新增五个独立用例；BH06 正反、事务回滚、镜像/候选篡改、来源拒绝已执行 |
| 10 | 工程规范与一致性 | 关联 M3W-02 | 锁定 uv，测试 lint/format，文档契约与指纹一致 |
| 11 | 数据与事务完整性 | none 新增 | 请求 binding 持久/摘要绑定；BH06 单事务事件、草稿、proof、receipt 与 candidate 唯一键 |
| 12 | 依赖与供应链治理 | none 新增 | 未引入依赖；延续 M2 SDK 与私有配置边界 |

## 5. 整改清单与测试证据

| ID | 严重级 | 负责人 | 截止 | 状态 |
|---|---|---|---|---|
| M3W-01 | P1 | `/root` | M3-R 完成前 | fixed / independently rechecked |
| M3W-02 | P2 | `/root` | M3-R 完成前 | fixed / independently rechecked |

初次独立运行：`3 failed, 17 passed in 10.74s`，失败分别为两个版本编码和终态 pending；保留该发现记录，没有跳过失败。

修复后独立命令：

```bash
uv run --frozen pytest tests/test_agent_memory_web_review.py tests/test_memory_repair_drafts.py tests/test_web_repair_store.py tests/test_web_analysis_store.py -o addopts='--import-mode=importlib' -q
```

```text
90 passed in 17.12s
```

独立测试文件 Ruff：`All checks passed!`；格式：`1 file already formatted`；`git diff --check` 通过。没有在评审中调用外部模型或启动用户服务。

实际核对的 BH06 关键边界：

- 当前候选 committed、比较 candidate_verified、人工 accepted、决定全局 revision、候选树/比较摘要全部一致才首次创建。
- `verify_versions` 复核当前冻结镜像与 snapshot；仅数据库状态文字不够。
- 非本地来源、暂停、跨项目、未接受/拒绝、旧 revision、哈希/文件/镜像篡改均拒绝且没有草稿/事件半写。
- `RepairFence` 排除当前任务并发决定；单事务保存事件、草稿、来源证明与幂等回执。
- 同候选换 request_id 不再建第二条经验；改变项目/来源/决定版本产生冲突。
- 决定后来撤回时 proof 显示当前 rejected / invalid；不删除已有历史。用户删除经验后只保留无正文的证明投影。
- 草稿不自动 confirmed、授权或 index；确定性模板只证明该次结果，未声称自动找到了根因或通用修复方法。

审计投影的 `current_state` 表示当前经验 head 是否 confirmed/draft/revoked/deleted/changed/unavailable；它不是当前 agent_context 授权或过期策略的通行证，实际外发仍以每请求权威事务为准。总费用和浏览器交互验收属于交付/UI批次。

## 6. 架构防腐与长效治理建议

1. 保留旧请求精确编码测试与绑定重开数据库测试，防止以后添加可空字段破坏历史幂等；成本为普通 CI 的几个小 SQLite 用例。
2. 每个后台投影增加“父任务结束、子记录未建立”的故障测试，避免把不存在误当排队；派生状态不能覆盖父任务权威终态。

## 7. 审计声明

- 已复审最终指纹版本、终态投影、当前 head 状态和 API 注册增量；其后行为变化应补充复审。
- 本分配范围内未评估 none；另批核心授权/运行时、完整 UI、真实模型效果、常驻部署和推送不由本报告代签。
- 无新增风险豁免；沿用既有单用户运行与供应链补偿限制。
- 两项发现均有独立失败证据，修复后 90 项通过；未将合成测试称为真实模型或业务效果证明。

### 补充：真实门禁 runner 执行前审查

新增纳入 `ops/m3r_live_gate.py` 与 `tests/test_m3_live_gate.py`，本批总量仍在约 2,000 行以内；执行前结论 Pass，无新增阻断项。

- 通过显式 `--execute` 启动，一次索引、一次 three_role 修复 POST，没有自动写入重试或随机挑成功样本；只轮询读接口。
- 独立私有目录、独立 8039 端口，创建前探测占用；只读既有宿主模型配置，人工审核合成经验/案例，不读取外部项目数据。
- 实际费用来自索引与任务的分开账本；检索校验 100k、6 LLM/40 embedding，Agent 1.9M、12 请求，核对任务总已知 token 2M 与费用完整性。索引的前置费用保留于报告，不混称任务检索免费。
- `validate_memory` 要求 diagnosis/repair 均有实际预留审计引用、verification 引用为空、审计数与模型请求数一致；它验证账本，不能独立证明原始 HTTP body 的角色隔离，后者仍需核心确定性测试。
- 仅当前 frozen evidence 被 Reader 验证，才调用现有审批和 BH06 路由；检查保存结果仍为 draft。没有绕过候选回归或自动确认记忆。
- 所有失败保留到独立 acceptance.json；finally 停止自己启动的 API，按真实 Docker 标签/镜像/snapshot 精确回收与核验，清理不确认会将成功降为 failed。
- 此 runner 是真实 HTTP/SDK/模型/Docker 门禁，**不启动 production Next 或浏览器**，不得把它写成实际浏览器验收。

独立运行：

```bash
uv run --frozen pytest tests/test_m3_live_gate.py tests/test_agent_memory_web_review.py -o addopts='--import-mode=importlib' -q
```

```text
12 passed in 1.32s
```

评审未执行真实模型门禁；最终实际尝试、消耗、修复结论及清理证据应由 49 交付记录补齐。允许实现者按一次性计划执行，不需要为这份评审重跑额外模型请求。

### 补充：首轮失败后的超时与拒绝分类增量

2026-10-19 复审新增单次 HTTP 时限调整和最终拒绝分类，结论 Pass，无新增阻断项。

评审者读取首轮实际报告 `artifacts/m3r/27463015-3cb4-4312-89bf-33f94ed33805/acceptance.json`：status=failed、stage=index、failure=index_failed；未建立修复 task；一次 LLM 预留 12014 tokens，actual=null、state=unknown、failure_code=provider_timeout；API 已 terminated。不能把该未知用量改成零，不能仅凭模型目录 GET 正常推断生成服务正常或超时根因。

变更将索引 LLM 单次 HTTP 上限 30→60 秒，embedding 仍 30 秒；实际 `asyncio.timeout` 和 HTTPX timeout 均取该上限与既有 job/parent 剩余 deadline 较小值，响应仍最多 1 MiB、不跟随跳转、无 HTTP 自动重试。作业 180 秒、任务 600 秒、tokens、请求数量不增加。使用新的独立验收目录验证，不覆盖或重放未知的第一笔请求。

新增独立测试 `tests/test_memory_gateway_timeout_review.py` 五项，使用 HTTPX MockTransport 与真实 asyncio 定时器（不请求真实网关）：

- LLM 60 / embedding 30 两类 cap；各发送一次，不跟随跳转、不使用环境代理。
- 90ms 父 deadline 下持续滴流仍按整体期限终止，不被每段数据刷新时限。
- 停止标志在滴流下一段到达时终止并关闭 stream；直接 asyncio cancel 同样关闭 stream。

必须保留边界：停止标志在响应分片间检查；无分片的停滞 socket 可等待剩余 HTTP deadline，最长 60 秒。它不是即时取消远端请求或保证远端未计费；运行时继续 join 物理线程并保留未知账本。

同时复审：factory 只有明确 `provider_configuration_required` 可以无外发降级，`engine_authority_changed` 等权限/数据/运行时错误不能被吞作配置缺失；诊断/修复的逐请求事务明确拒绝集合 `CONTEXT_DENIALS` 在尚未发送时终止为 inconclusive / PERMISSION_DENIED。数据存储异常及未知远端请求仍走原来的保守未知处理，不靠该分类掩盖费用。既有反例断言 0 send、0 model_calls、usage_complete=true、无使用审计。

```bash
uv run --frozen pytest tests/test_memory_gateway_timeout_review.py tests/test_agent_memory.py -o addopts='--import-mode=importlib' -q
```

```text
26 passed in 4.00s
```

独立新增测试 Ruff/format 通过。该结果允许实现者在记录上述决策后执行另一次独立真实门禁，不是模型可靠性改善的结论。

### 补充：旧 CLI 任务的记忆状态兼容

2026-10-19，`agent_api.usage` 对没有网页 receipt、但权威父任务实际存在的旧 CLI analysis/repair 返回 disabled / 空引用；不存在的 task 仍 not_found。该改动不创建绑定、不发起检索、不把 CLI 记录变成已使用记忆。

独立新增两个真实临时 SQLite 用例：分别使用 AnalysisStore.create / RepairStore.create_task 创建无网页接收记录的任务，验证 disabled；随机不存在的任务仍拒绝。`tests/test_agent_memory_web_review.py` 最终 `7 passed in 1.42s`，锁定 Ruff/format 通过。增量结论 Pass。

### 补充：真实运行发现的释放指纹故障与候选失败诊断

2026-10-19。实现者在第二次真实运行发现终态仍 active，独立复核确认新增 `memory_binding` 为 SQLite BLOB；原 `_release_state` 将 `dict(row)` 直接交给 JSON canonical，遇到 bytes 会异常，恢复器保守返回 RECOVERY_UNCONFIRMED，因此槽位一直保留。

**M3W-03 / Defect P1 / confirmed / 已修复**：`web_repairs/store.py::_release_state` 在完整 receipt 指纹中将非空 memory_binding 编码为其 SHA-256，而非删除该字段或强制清空 active。字段内容继续由 receipt 的严格 MemoryBinding/请求指纹核验。后续仍执行资源核验和原 stamp CAS，没有跳过恢复门禁。

评审者新增 `test_full_recovery_releases_memory_bound_terminal_task`，从真实 SQLite 创建带记忆任务、消费执行权、写失败终态，然后调用完整 `WebRepairRecovery.release_checked`：绑定不丢失、active=false、active_id=None。此用例没有执行容器目标，因此不需 Docker；真实有目标的清理仍由运行证据另验。实现者的 release_state/CAS 单测同时通过。

候选失败诊断增量复核：`candidates/diagnostics.py` 只允许16个固定错误码写入既有事件；未知字符串仍映射 TOOL_FAILED，SNAPSHOT_COMMIT_UNKNOWN 保持 RESULT_UNKNOWN。修复工具对模型仍返回受控 TOOL_FAILURE/RESULT_UNKNOWN，不回显补丁或异常文字。五项桥接失败注入验证持久 candidate/request/event 码和正文不泄漏；初次聚合5失败来自测试误用要求 committed 的成功辅助函数，改用实际 invoke 后通过，不是改变产品验收。

**M3W-04 / Defect P1 / confirmed / 源码修复完成**：新增候选错误码若未更新 `frontend/lib/repairs.ts` 的严格 z.enum，会让整条详情/运行事件解析失败。评审发现后，UI 实现者补齐16码及固定中文标签；独立静态集合核对结果为 `candidate_error_frontend_enum_and_labels: 16 of 16 matched`。补充的 PATCH_SCOPE_REJECTED 页面用例须由最终生产构建/浏览器批次执行，不能以本次源码核对代替浏览器结果。

Runner 同时增加首次 phase=finished 时保存检索/模型使用证据，防止释放迟滞覆盖真实分项结果；整体通过条件仍要求 active=false、当前冻结回归通过和 BH06 草稿成功，没有因记录提前而放宽完成判定。

修复后独立运行：

```bash
uv run --frozen pytest tests/test_agent_memory_web_review.py tests/test_candidate_failure_codes.py tests/test_web_repair_store.py tests/test_m3_live_gate.py -o addopts='--import-mode=importlib' -q
```

```text
79 passed in 3.82s
```

负责人 `/root` / 核心实现者，截止 M3-R 完成前。后端增量 Pass；UI 浏览器证明归最终 UI 门禁。旧运行只保留了 TOOL_FAILED，不能从新增诊断映射倒推出那次具体候选拒绝原因，也不能将槽位故障与候选失败混为同一根因。


### 最终真实证据独立核对与代理复验（2026-10-20）

评审者 `/root/m3_final_review` 只读核对真实 gate `artifacts/m3r/7af0dbc2-f36b-4407-ab0b-3793ce882085/acceptance.json`，并通过无模型配置的独立 API 8038 + 新 production Next 3018 重新读取当前冻结证据与 BH06 来源证明。**最终增量 Pass（100/100），无未解决缺陷或豁免**；不将修复概率或收益列为已验证。

- attempt_policy 为一次索引、一次检索、一次修复，无自动重试；此前不同尝试失败记录继续保留，不能只用本次成功作为全部运行统计。
- 预建索引账本：2 次 LLM + 4 次 embedding，全部 received，已知 7,220 tokens。它是准备费用，单独记账。
- 本次检索：2 次 embedding，共 32 tokens，预留 2,298，低于 100,000 上限；没有检索 LLM 请求。
- Agent：6 次模型调用、6 次工具调用、1 个候选；34,752 输入 + 1,678 输出 = 36,430 tokens，usage_complete=true、reserved_tokens=0；低于 1,900,000 / 12 模型 / 20 工具 / 2 候选限制。任务已知总量 36,462 tokens；金额保持 unknown。
- 6 条逐请求引用审计与 6 次 Agent 模型请求相符：diagnosis 1 条、repair 4 条均引用同一已确认经验版本；verification 1 条且 refs=[]。本项验证审计与账本，不将其称为模型采纳或改进收益。
- 当前 comparison verification=verified、current_outcome=candidate_verified；故障基线失败、正常基线及两组候选通过；4 个回归槽位 cleanup=removed。读取证据仍经过后端当前 Reader 验证。
- BH06 草稿仍为 draft，当前决定 accepted、source_current_valid=true；浏览器只查询和进入详情，没有再次写入或确认。
- 资源报告 confirmed=true：唯一任务 active=false、pending_operation=null，T05/T07 共 5 个实际执行尝试的 container_absent=true，原 gate API terminated。后续复验 API 是新起的只读验收服务，不是原运行遗留。

`agent-memory-live.spec.ts` **1 passed (3.7s)**，无 mock，覆盖 repair memory 200、analysis memory 后端 JSON 404、非法 draft POST 后端 JSON 400 和草稿 GET 恢复。新增三条 Next rewrite 均只匹配严格 UUID 路径，没有扩大成无约束代理。M3UI-04 的首次真实网页 404 作为失败留在 UI 报告，修复后本次才关闭。M3W-04 的候选错误码浏览器增量已由前一 UI 批次 1 passed 验证，最终真实详情严格 DTO 本次也可读取。

证据与具体构建命令见 `M3-R-ui-review.md` 末段；主库迁移、常驻服务发布由 root 最后执行，不能把本次隔离 API/浏览器结果提前算为主库部署成功。
