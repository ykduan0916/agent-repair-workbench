# M1-R：记忆权威领域与事件存储

2026-10-19，Feature P1，状态：done（planned → ready → in_progress → in_review → done）。依据[44业务设计](44-p1-02-memory-product-and-integration.md)、[45引擎契约](45-mr-01-cognee-contract-validation.md)。本批是可信本地宿主调用的领域层，不开放外部HTTP/Hook入口，不向模型外发内容。Cognee生产接入属于M2-R；客户端签名/凭据与网页管理属于M4-R。

## 1. 冻结范围与数据

沿用现有SQLite权威库，Alembic新增0011，旧表不改。新表使用独立 `memory_` 前缀；领域代码放 `experience_memory/`，来源适配器后续放 `integrations/`。产品Agent目录不增加记忆伪角色。

| 表 | 权威内容 |
|---|---|
| projects | 宿主注册的项目ID |
| connections | 连接项目归属、来源类型、启用状态与CAS版本；没有远端凭据 |
| events | connection+event_id去重、规范化摘要、受限结构化事件、接收时间 |
| heads / revisions | 当前经验状态与CAS版本、不可变修订摘要/来源/适用范围；删除清空所有修订正文 |
| receipts | project+request_id持久幂等结果；相同请求重试返回原决定，不携带正文 |
| jobs | 同事务outbox、租约与fencing token、尝试次数、终态与预算累计 |
| projections | 不可复用的dataset/data到memory/revision/digest映射 |
| calls | 实际外发前的调用预留、usage/unknown；本批只实现账本，不发送HTTP |

事件只允许 `test_completed / contract_changed / user_correction`，包含安全标识、outcome、有限摘要引用及来源时间。外部报告不能自报 `internally_verified`；BH-06内部冻结回归绑定留M3-R。正文只通过显式宿主创建经验操作写入，不将事件原始日志或终端文本默认为可外发。

经验类型 episode / repair_experience / project_constraint；状态 draft / confirmed / revoked / deleted。所有类型创建为draft，模型可用性后续独立判断。修改正文增加revision且回draft；撤回可重新明确确认；deleted不可复活。修订、确认、撤回、删除均用expected_version和project+request_id。确认不等于当前回归通过。版本适用策略由M2-R复核；本批不会给出语义匹配结论。

## 2. 事务、幂等和删除

使用既有RunStore显式BEGIN IMMEDIATE写事务、外键、FULL同步与错误码。确认/撤回/删除及outbox同事务提交；进程在提交前退出不产生半条经验，提交后重传返回持久receipt。同键异内容冲突，跨项目资源返回not_found。连接项目归属不可修改，启停采用CAS；暂停阻止新接收、事件任务和新模型预留，不自动删除已有数据。

创建/修订/确认排入待同步任务，撤回/删除排入清理任务。draft任务不得预留请求或发布投影，确认产生新代际；过期draft任务会终结。修订/状态变更同时作废本条经验旧任务（在途调用标unknown），释放其队列位置；如果仍达到队列上限，整个操作显式失败回滚，不能报告已撤回/已删除。worker必须在执行和预留前重查当前修订/状态；旧代际任务失效，不得发布投影。清理任务本批只提供账本，实际Cognee删除及共享来源处理在M2-R验收。删除保留无正文墓碑、摘要和审计；SQLite旧页/WAL/备份与源事件不在逻辑删除保证内，不宣称物理擦除。

同键请求重放先查幂等再看CAS，但不会返回旧正文或复活状态。重复事件只保留一份；事件缺少步骤或时钟倒序仍作为不完整来源保存，不推断完整任务。

## 3. 有界任务和请求

固定上限：事件规范JSON16KiB、引用最多16个；经验正文UTF-8最多16KiB、每条最多64修订；每项目最多10000条经验；每连接最多10000事件；最多1000未完成任务。列表最多100条。超限返回明确错误，事务回滚，无静默丢弃。

worker租约30秒，可续租；过期可重新领取，最多3次尝试。只有未到期且token匹配的持有者可写结果；过期worker不能完成新租约的作业。租约不是外部副作用exactly-once，实际副作用必须可查询/幂等，未知调用禁止自动重放。

每作业独立预算：最多6次LLM、40次embedding，累计100000个预留token单位；每次预留1..65536。调用前预留、响应后结算；累计预算按预留值单调增加（不返还），实际usage独立记录。没有可信token上界的适配器不得发送，M2-R负责算上界并验证provider profile；本批参数为可信宿主输入。预算不是美元承诺，金额unknown。settle超过预留/缺usage/租约中断标unknown并冻结后续调用，不把未知记作免费。

租约到期若有reserved/unknown调用，则任务进入unknown终态，不自动领回。取消将reserved转unknown，已知usage保留。project/request和job/call键都受限且持久去重；retry不能多次扣账。连接停用不拦截清理作业，但停止加工和预留；cleanup作业无论连接是否启用，都禁止LLM和embedding请求，只允许受控索引清理。

## 4. 迁移与部署

0010→0011只建表和索引。显式停服务、SQLite backup、逐旧表逻辑摘要对比、迁移后启动；启动不自动迁移。旧schema不能被新领域接口静默当空库。回退只允许新表全空；含新数据拒绝downgrade，不丢用户状态。快照/实验只使用合成材料。

## 5. 验收清单

- MT01：升级保留旧运行与审批，未知schema拒绝，空库回退/非空回退拒绝。
- MT02：事件重传/异内容/不同连接同ID、跨项目和连接暂停。
- MT03：经验确认/修订/撤回/删除与历史receipt，修改暂停旧版，删除清空正文。
- MT04：多进程同CAS仅一个成功；写事务故障注入回滚经验与outbox。
- MT05：租约领取/续租/过期/旧token拒绝、最多3次、重启恢复；unknown不自动重放。
- MT06：实际请求账本并发预算、幂等、usage保留、取消/未知冻结。
- MT07：投影必须绑定当前确认修订/摘要与任务lease；旧映射删除后拒用，跨项目拒用。
- MT08：超限/非法类型/空引用/伪造来源/错误时间不落库；错误无正文/凭据回显。
- MT09：真实临时SQLite全流程、进程重启、事务中断；不以mock替代持久行为。
- MT10：前端文档/连接页状态和更新记录，深浅色/390px，实际生产构建与常驻服务。

独立评审覆盖本批代码、迁移、测试及声明；未修复P0/P1阻断done。单测不代表Hook接入或记忆改善修复率。

## 6. 领域接口与实现位置

实现入口 `src/agent_workbench/experience_memory/`：models定义命令/返回值；store处理事件与经验事务；jobs处理租约与费用账本；common提供安全错误与事务内辅助方法。没有产品Agent直接导入Cognee，没有新依赖，也没有外部HTTP接收端点。

| 方法 | 行为与边界 |
|---|---|
| register_project / register_connection | 宿主注册归属，连接来源不可改为内部验证；当前不生成外部凭据 |
| set_connection / accept_event | CAS启停、按connection+event_id去重；相同ID异内容拒绝 |
| create_memory / change_memory | 明确来源与正文；expected_version和持久request_id；改变正文回draft |
| memory / list_memories | 当前正文显式读取，列表只含无正文receipt；删除后正文为空 |
| claim_job / renew_job / finish_job / cancel_job | 有界领取、过期fencing、unknown不重放，真实副作用留给下一批执行器 |
| event_for_job | 只有当前有效租约可读取对应不可变事件，核对摘要；不是任意事件查询 |
| reserve_call / settle_call | 只首个请求返回admitted=true，重放不得据reserved再次外发；迟到usage保留但不恢复unknown作业 |
| register_projection / resolve_projection | 当前确认代际与摘要绑定；旧dataset/data不可改绑；不代表跨版本语义适用性已经判定 |

`Receipt`是历史请求的结果，不是当前head；重试旧请求之后应调用memory读取当前状态，不能以receipt旧confirmed状态注入正文。调用预留由可信宿主执行，本批没有对外API授权层；M2-R不得直接把模型提交的tokens当可信上界。

## 7. 验收记录

### 7.1 后端与进程

```bash
uv run --frozen pytest tests/test_memory_authority.py tests/test_memory_jobs.py -o addopts='--import-mode=importlib' -q
uv run --frozen pytest -o addopts='--import-mode=importlib' -q
uv run --frozen ruff check .
uv run --frozen ruff format --check .
uv run --frozen mypy
```

- 最新专项 **46 passed / 2.09秒**，`artifacts/m1r/targeted-final.log`；使用临时真实SQLite，包含两个独立进程的CAS竞争、写事务中进程退出、重启、取消/迟到usage、draft投影与清理禁模型等负向测试。
- 完整回归 **1669 passed / 67 skipped / 1 warning / 224.43秒**，`artifacts/m1r/full-tests-final.log`。这是最后新增只读event_for_job及其1项测试之前的完整结果；新增后专项46项通过，不写成1670项全跑。67项为未启用的Docker/公开案例等显式环境门禁，本批不改模型/执行器，未重新执行真实模型或Docker验收；warning为既有fork与多线程提示。
- 全仓Ruff检查与445文件格式、mypy145文件、diff检查通过。新的测试位于现有tests目录，自动纳入默认pytest发现。
- 独立合成SQLite全链路见 `artifacts/m1r/acceptance.json`：接收事件、草稿/确认、预留/结算账本、来源映射、删除后拒用、暂停连接仍完成清理、重启后墓碑保留。账本中的100/20是**模拟数值**，没有模型/embedding/Cognee调用，不是实际消耗或完整记忆收益。

### 7.2 主库与常驻服务

确认三个执行槽空闲后停launchd服务，使用SQLite backup保留迁移前库，再显式执行RunStore.initialize的Alembic迁移。`artifacts/m1r/migration.json`记录：0010→0011、20旧表183行逐表全行逻辑摘要一致；新增9表均0行。没有往主库灌入测试经历。

维护者需要迁移时：先停止写入并备份记录库，再运行 `uv run --frozen agent-workbench runs init`；不能用网页请求隐式建表或迁移。备份和WAL含旧数据，当前逻辑删除不清除这些历史副本。

`artifacts/m1r/services.json`：运行/分析/修复历史接口均HTTP200；复现ready、分析槽available、修复槽available且accepting=true；前后端launchd进程PPID均1。不是只用健康端点替代数据库读取验收。

### 7.3 前端

- 沿用现有组件与主题，在环境与接入页新增紧凑“经验与事件”区域、本站46/44文档入口；无虚假运行按钮。
- capability仍为待开发，用户尚不能从网页管理经验或安装Hook；更新记录说明的是可见的接入说明变化，不放里程碑编号和测试数。
- TypeScript、生产build通过。`PLAYWRIGHT_CHANNEL=chrome npx playwright test tests/document-reader.spec.ts tests/memory-boundaries.spec.ts tests/progress.spec.ts`：**15 passed / 23.5秒**，`artifacts/m1r/frontend-browser-verified.log`。覆盖新页与历史页、1440/390px、深浅色、axe、导航与不触发写请求。`memory-light.png`/`memory-dark.png`为实际新区域截图。

### 7.4 问题与修正记录

1. 独立评审P1：cleanup继承领取资格而错误获得模型额度。已明确禁止LLM/embedding，新预留单独复核连接；4种来源启停与调用类型负向测试通过。清理仍可执行。
2. 独立评审P2：Pydantic Literal[1]接受true/1.0。已在before validator强制int，构造与model_copy重验均覆盖。
3. 最初2个进程测试因importlib模式下spawn找不到tests包而失败，复用本仓库spawn路径fixture后通过；不是将进程测试改成线程或跳过。
4. 首轮全量1失败/1660通过：旧0006迁移测试错误地把新增9张空表纳入旧表集合。补入其原有显式新表排除列表，旧表内容与非空回退断言不变；独立评审确认未弱化旧数据验证；最终完整回归通过。
5. 前端初轮13通过/2失败：exact-text断言未考虑状态标签中的sr-only说明。修正为分别断言data-capability与外部待开发标记，原有无按钮/无写请求/可访问性断言保留；一次路径错误导致旧测试又跑一遍，日志均保留，最终15项通过。

## 8. 交付边界与下一项

独立评审报告：[M1-R代码评审](reviews/M1-R-authority-review.md)。独立评审结论为M1-R限定范围Pass；初审1项P1和1项P2已修复并复审关闭，评审者独立运行最终46项测试通过。该结论不包含后续Cognee/HTTP/Hook或记忆收益验收。

下一项 **M2-R：Cognee适配、有效性复核与实际调用记账**。必须接入本账本，补齐授权用途、来源/内容/版本/有效期与冲突检查、真实模型预算预留、执行取消和索引清理恢复。MR01-SC-01的diskcache供应链残余仍是M2-R开放前门禁，不能因本批领域层通过而销项。

Hook业务方案已完成；客户端事件覆盖、签名/凭据、插件安装与真实联调在M4-R，尚未实现。本批不自动采集、不向外部发送用户材料、不提交推送。
