# 企业代码质量审计报告：M2-R 引擎运行时

- **审计对象**：`providers/cognee/{runtime,gateway,worker,watchdog}.py`、`ops/{memory_engine,m2r_live_gate}.py`、相关确定性测试；权威存储与迁移由另一份独立评审覆盖。
- **审计版本**：`f8a6f35` 后 M2-R 工作树，2026-10-19；已完成运行时复审；后续新增改动须复核。
- **技术栈**：Python 3.12、HTTPX、进程组、文件锁、独立 Cognee 1.6.5 环境。
- **业务场景**：General SaaS。
- **审查严苛度**：Mission-Critical。
- **依据**：AGENTS、约束01、MR-01文档45、M2-R文档47；auditing-enterprise-code-quality 的评分与十二维矩阵。

## 1. 代码质量评估卡

**复审：Pass（限定本地可信宿主运行时，MR01-SC-01有书面补偿接受）。** 初审4项 Defect P1已修复复测关闭。初审68/100；最终97/100：100−既有残余P2×3，无P0/P1封顶。Mission-Critical的P2零预算通过本报告明确限定的风险接受覆盖，不把漏洞计为已修复。

| P0 | P1 初审 | P2 残余 | P3 |
|---|---|---|---|
| 0 | 4 | 1（MR01-SC-01，上游既有风险） | 0 |

未评估：none（所分配运行时范围）。Agent上下文接入、Hook和网页管理不在此批范围，由后续里程碑独立验收。

独立执行 `uv run --frozen pytest tests/test_memory_engine.py -o addopts='--import-mode=importlib' -q`，初审版本输出：`34 passed in 1.75s`。没有自行进行真实模型调用。修复后独立执行同命令加`tests/test_memory_runtime.py`：`46 passed in 4.92s`。安装包显式SQLite配置：`1 passed in 0.21s`。锁定Ruff：`All checks passed!`、`8 files already formatted`；`git diff --check`通过。

## 2. 生产事故风险推演

1. 上游 embedding 返回200和正常usage，但向量条数/维数/数值错误，原网关会转发给SDK；索引质量受损或故障在更深层出现，不能履行明确的适配器契约。
2. 父宿主被SIGKILL，原独立SDK子进程继承writer锁且没有自身时限；SDK本地处理/长重试持续占锁，重启后的所有引擎操作受阻。
3. 同一权威库改用另一`--engine-root`，原清理在新目录未发现root后仍标removed，旧目录实际派生数据遗留，并无法由正常cleanup重新发现。
4. 同名embedding服务变更endpoint或宿主版本，原索引无profile绑定；旧向量与新query向量可混用，输出表面有引用但语义匹配不可信。

## 3. 详细审计明细

### P0

none。

### P1（初审，confirmed）

- **RT-01 向量响应未校验**：`gateway.py::forward`，原usage结算后直接返回content。维度8。最小修法：结算保留实际usage后检查输入/输出数量一致、每向量1024维、数值finite且非bool；失败拒交SDK并停发。已见修复代码，负例已通过。
- **RT-02 父死亡缺少子进程独立期限**：`runtime.py::_worker`/`worker.py::main`，原deadline只在父循环。维度3。最小修法：独立watchdog监视只读管道EOF和绝对期限，杀死同一SDK进程组；正常退出亦清理watchdog。已见管道与watchdog修复，实际父死亡、超时与取消进程测试通过。
- **RT-03 权威库与派生目录未绑定**：`runtime.py::__init__/cleanup`。维度11。最小修法：持久绑定唯一规范目录与权威库身份、双向校验，在同一锁内禁止混用；不能在错误根上将未删除目录标removed。已见双向绑定，负例已通过。
- **RT-04 向量profile未绑定索引**：`gateway.py::Providers`及`runtime.py::index/search`。维度8。最小修法：保存endpoint/model/dimension/宿主revision的无密钥摘要，搜索与根一致；凭据轮换不改变profile。已见embedding_fingerprint与权威层profile_digest，配置漂移拒绝和凭据轮换不失效测试通过。

另建议已传递：结果返回间隙再次检查取消；验收脚本在accounting或cleanup失败时仍保存安全acceptance.json，CLI finally关闭store。此类修复合入上述回归，不另重复计分。

### P2

**MR01-SC-01**：diskcache 5.6.3 仍在锁定环境中，不能声称漏洞已修复。显式SQLite配置、实际适配器类型验证、私有UID/权限/无符号链接目录、禁止不可信缓存导入是本地可信宿主的补偿。该残余须由最终文档明确接受；不适用于恶意本地用户或多租户。

### P3

none。

## 4. 维度覆盖声明

| # | 维度 | 结论 |
|---|---|---|
| 1 | 架构与边界 | Cognee只给候选来源；权威正文由store复核，未接入Agent/Hook |
| 2 | SOLID | gateway/worker/runtime与领域账本分层，无新增问题 |
| 3 | 并发与资源 | RT-02；flock覆盖执行/清理，真实父死亡/超时/取消测试通过 |
| 4 | 容错 | 限时/首次unknown停发；失败产物必须在清理失败后保存 |
| 5 | 性能 | 同时一个worker、8来源/180秒；私有目录扫描改为惰性上限 |
| 6 | 安全 | 上游密钥仅宿主、随机loopback令牌、SDK原始日志关闭；不宣称OS网络隔离 |
| 7 | 可观测 | 安全failure_code、usage与unknown保留；三次真实运行证据已复核 |
| 8 | 类型与契约 | RT-01/04；请求与响应有字节上限 |
| 9 | 可测试性 | 修复后46项独立通过 |
| 10 | 工程规范 | 锁定环境与命令显式execute；未扩大到自动Hook |
| 11 | 数据完整性 | RT-03；逐HTTP durable预留和权威检查由另一评审覆盖 |
| 12 | 依赖治理 | MR01-SC-01条件性补偿，不把残余审计成零漏洞 |

## 5. 整改清单

| ID | 严重级 | 负责人 | 截止 | 状态 |
|---|---|---|---|---|
| RT-01 | P1 | 本轮M2-R实现者 | M2-R验收前 | closed |
| RT-02 | P1 | 本轮M2-R实现者 | M2-R验收前 | closed |
| RT-03 | P1 | 本轮M2-R实现者 | M2-R验收前 | closed |
| RT-04 | P1 | 本轮M2-R实现者 | M2-R验收前 | closed |
| MR01-SC-01 | P2 | 记忆引擎维护者 | 每次SDK升级/扩展信任边界前 | 保留补偿复核 |

## 6. 架构防腐与长效治理建议

1. 将provider profile、authority/root身份作为持久契约，配置变化显式拒绝或重建，防止静默混用。
2. 在CI保留实际子进程死亡、取消、错误向量与失败账单测试；这些不需要远端模型费用。
3. 新SDK版本必须重做缓存路径、引用和实际请求账单契约验证，不能只更新版本字符串。

## 7. 审计声明

本报告为独立只读实现评审，未调用真实模型，未访问其他项目或全局记忆。对分配的运行时范围给出Pass；最终里程碑还须汇总权威层评审、主库迁移与前端验收。一般用户收益与多租户安全没有据此被证明。


### 最终真实证据复核

仅读取实现者保存的安全JSON，不重跑模型。三个attempt全部保留：

| attempt | 结论 | 实际请求 | 已报告tokens | unknown |
|---|---|---|---|---|
| f1d29afbdcf242c0ba52e92dea79d80a | failed，清理1根 | 1 | 0（不代表免费） | 1 |
| f701b65229594518afb78adccea1a0cf | passed | 11 | 7646 | 0 |
| 8f8ce2065ebc48f9ab4b8133628875b1 | 修复后passed | 11 | 7206 | 0 |

最终产物`artifacts/m2r/8f8ce2065ebc48f9ab4b8133628875b1/acceptance.json`：检索1条historical_hint，`current_verification=false`；撤回后0引用，物理清理1根。证明已授权合成材料的索引/取用/撤回/清理契约，不是用户经验收益基准。

供应链风险接受负责人为本轮记忆引擎实现者，复核人为本报告作者。仅接受MR01-SC-01在当前锁定版本、可信单用户、私有受控目录、强制SqlCacheAdapter、失败拒启条件下的残余；每次依赖升级或信任边界扩展前重新审计。已检查安装包`get_cache_engine.py`显式SQLite配置失败分支会抛错，worker在`cognee.add/search`前验证配置和实际类型。没有删除上游风险项，也没有审计忽略规则。

最终补偿增量复测：`AWB_COGNEE_CONTRACT=1 uv run --frozen pytest tests/test_memory_runtime.py -o addopts='--import-mode=importlib' -q`，`15 passed in 8.57s`。包含安装的固定Cognee在显式SQLite URL无效时抛错，且注入FSCacheAdapter哨兵未被调用；没有远端模型请求。另覆盖重新索引profile的根选择、清理无需provider凭据。

## 8. 收尾配置及前端增量评审

按追加授权审阅`connections-console.tsx`、`capabilities/modules/milestones/documentation`及两份前端测试、doc47操作步骤；应用frontend-design skill，保持已有环境设置页的紧凑行布局、语义色和主题，不另加展示卡片或装饰动画。

- CLI loader已读取可选embedding revision并传入Providers；用独立临时合成配置执行验证，输出`revision forwarding and strict dimension checks passed; no network`，包含拒绝浮点dimensions=1024.0。未读取真实密钥。
- doc47已区分：完整可信usage可保留received，取消后操作结果仍拒用；响应不完整/未知账单保留unknown。与网关处理一致。
- UI能力明确标记`CLI 可用`，网页经验管理、Agent自动取用及外部Hook均未接通；没有新增执行按钮、自动确认或外发操作。外部事件仍待开发。更新记录保留历史条目且没有里程碑ID/测试数等工程信息。
- 复用语义section/heading、可访问链接及CapabilityBadge屏幕阅读说明，既有CSS含focus-visible、移动端行重排和两主题变量。测试覆盖390px深浅色、无写请求、无虚假按钮、axe、文档横向溢出；实际浏览器结果由主执行者另行报告。
- **UI-01（Defect P2）初审发现**：`connections-console.tsx:38`使用`#本机使用`，而`docs.server.ts:45-46`保留标题数字，`## 6. 本机使用`实际锚点为`6-本机使用`。原测试只核对URL，没有核对目标heading，无法证明用户真正进入操作步骤。建议href与测试使用正确ID，并断言heading进入视口。负责人本轮实现者，验收前修复；修复确认记录如下。

UI-01复审关闭：href已改为`#6-本机使用`；测试将浏览器URL解码后核对hash，并对`[id="6-本机使用"]`使用`toBeInViewport()`。不再以URL变化代替实际导航。`git diff --check`通过。追加的UI代码评审无未修复P0/P1/P2，整体运行时评分97/100及既有依赖残余接受不变；实际浏览器验收属于主交付证据。

## 9. 字面正文摄入边界追加复审（RT-05，P1，closed）

实现者在收尾发现原`worker.py::main`直接向`cognee.add`传str，SDK可把它解释为文件路径、URL、S3或仓库定位符。正文已获授权并不意味着授权读取其指向的资源；应阻断M2-R交付，不能靠调用方人工记住避开这种正文。负责人本轮实现者，截止M2-R验收前，已修复关闭。

最小补丁已核对：索引使用`DataItem(data=payload["text"], literal_text=True)`，子进程环境额外设置`ALLOW_HTTP_REQUESTS=false`。独立追踪安装的Cognee1.6.5链路：

1. `resolve_dlt_sources`保留正文为str的DataItem，不将它作为DLT资源展开。
2. `resolve_data_directories`只对裸str做目录/仓库/URL解释，DataItem沿else分支保留；增量预处理也使用此函数。
3. `ingest_data`把完整DataItem交存储；`save_data_item_to_storage_detailed`的literal_text分支直接把字符串写为文本，不进入文件/HTTP/S3读取分支。
4. `_source_uri_from_input`识别literal_text，不凭正文猜资源来源。数据读取权限没有扩大。

独立执行固定安装包的真实`cognee.add`契约测试（无cognify、无模型请求、不接外网）：

`AWB_COGNEE_CONTRACT=1 uv run --frozen pytest tests/test_memory_runtime.py::test_installed_add_keeps_paths_and_urls_literal -o addopts='--import-mode=importlib' -q`

输出：`1 passed in 11.87s`。覆盖一个实际存在但未授权读取的合成文件路径、localhost URL、S3 URL；每个数据集恰好一个data，`raw_data_location`中的文本逐字等于输入定位符，不是它指向的内容。`git diff --check`通过。

追加RT-05关闭后累计5项P1修复，无未修复P0/P1，运行时Pass及97/100不变。此前第三次模型链路早于这项补丁；本项证据是固定SDK实际add摄入契约，不冒称又完成一次远端模型全链路。ALLOW_HTTP_REQUESTS是SDK抓取开关，仍不宣称OS网络沙箱。
