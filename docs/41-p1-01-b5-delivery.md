# P1-01 B5：真实网页修复闭环验收

日期：2026-10-19。Feature P1。状态：`planned → ready → in_progress → in_review → done`。

本批关闭网页接收、执行、停止、候选审阅、人工决定与精确补丁下载之间的集成门禁。B4 已提交并推送为 `31a5acf`。当前只支持本地可信单用户和两个内置审核案例；不新增角色、不接收任意仓库、不自动写回项目。

## 1. 验收分层

| 层次 | 证明什么 | 不证明什么 |
|---|---|---|
| 默认 Python 与模拟浏览器 | 权限、去重、预算、版本核对、错误与交互分支 | 模型效果、Docker 隔离与真实网页闭环 |
| 两案例真实 Docker | 冻结回归正确识别基线、有效候选及破坏旧行为的补丁 | 模型能够生成补丁 |
| 当前网关 + Docker + 生产网页 | 网页发起任务，模型生成候选，真实容器比较，人工决定并下载精确字节 | 任意项目修复率、市场验证、多角色必然优于单角色 |

真实验收使用独立 SQLite、快照目录、API/前端端口与 Next 构建目录，不把合成任务写入用户主库。全部尝试保留，失败不得被后续成功覆盖。密钥由权限 0600 的本机配置读取，不进入浏览器、命令参数或报告。

## 2. W01–W12 追溯

| 验收 | 既有依据与本批补充 |
|---|---|
| W01 一次接收/响应丢失 | B1a 接收账本；B2 API；B3 浏览器丢响应、同 ID 查询及明确重提 |
| W02 异参/墓碑/伪造 claim | B1a 存储负向测试与 B2 HTTP 边界 |
| W03 多进程/owner 生死 | B1b fence 与恢复；B2 资源复审；真实 Docker 孤儿容器恢复单列运行 |
| W04 缺配置/版本失配 | B2 preflight，接受前零模型零容器；前端错误入口 |
| W05 取消 | B2 首次调用前、模型预留与晚到提交；本批 T05 真实 SDK/Docker 的 HTTP 取消、T07 原生执行重复取消，另列真实网页模型期间停止；owner 崩溃恢复单独记录 |
| W06 重启/刷新 | B1b 不自动重放；B3 同任务刷新；本批真实浏览器再次核对 |
| W07 迁移/旧 CLI | B1b/B3 迁移逐行保真；本批全量 Python 回归 |
| W08 审批失效 | B4 当前证据重新核对、篡改拒绝与历史保留 |
| W09 审批并发/幂等 | B4 CAS、request ledger、独立双进程并发 |
| W10 下载一致 | B4 浏览器摘要与关联校验；本批真实下载逐字节 SHA-256 核对 |
| W11 UI | B3/B4 浅深主题、390/1440、键盘、未知状态；本批保留现有视觉并补紧凑操作路径与站内指南 |
| W12 两案例真实闭环 | 第三轮真实网关、Docker、生产 Next 全链路通过；前两轮失败全部保留，见下方实际结果 |

既有实现与评审：[B1a](36-p1-01-b1a-delivery.md)、[B1b](37-p1-01-b1b-delivery.md)、[B2](38-p1-01-b2-delivery.md)、[B3](39-p1-01-b3-web-controls.md)、[B4](40-p1-01-b4-approval-download.md)。

## 3. Ruff 与可复现工具链

此前裸 `ruff` 使用 Anaconda 全局 0.12.0，项目锁定版本为 0.17.0；检查结论不能跨版本混用。本批统一使用 `uv run --frozen ruff check .` 和 `uv run --frozen ruff format --check .`。四个 B4 文件仅修正格式，独立 AST 比较确认语义不变；未增加忽略规则，也未关闭检查。独立首轮记录见 [质量评审](reviews/P1-01-B5-quality-review.md)。

## 4. 实际结果

- 两案例真实 Docker 冻结回归：`AWB_HELDOUT_DOCKER=1 uv run --frozen pytest tests/test_repair_case_docker.py`，10 passed，29.83 秒。
- T05 真实 SDK/Docker 的正常、HTTP 取消和 lifespan 关闭：`AWB_WEB_ANALYSIS_DOCKER=1 AWB_WEB_ANALYSIS_ARTIFACT_DIR=artifacts/b5-t05-cancel uv run --frozen pytest tests/test_web_analysis_docker.py`，3 passed，4.42 秒。模型适配器是确定性的；屏障在 SDK 实际返回后、提交前，不冒称真实网关取消。
- T07 原生重复取消并回收真实容器：`AWB_CANDIDATE_DOCKER_TEST=1 uv run --frozen pytest tests/test_candidate_docker.py::test_real_native_repeated_cancel_reclaims_actual_container`，1 passed，1.28 秒。不调用模型。
- owner 存活/死亡的真实 Docker 恢复：首次命令漏设 `AWB_REPAIR_RECOVERY_IMAGE_ROOT`，2 项在前置配置校验失败，未启动 Docker；补上当前项目绝对路径后，`AWB_REPAIR_RECOVERY_DOCKER=1 AWB_REPAIR_RECOVERY_IMAGE_ROOT="$PWD" uv run --frozen pytest tests/test_repair_recovery_docker.py`，2 passed，3.55 秒。活 owner 拒绝清理；死 owner 精确回收，保留未知用量且不重放。
- 全量 Python：`uv run --frozen pytest -o addopts='--import-mode=importlib' -q`，1589 passed / 67 skipped / 1 warning，216.19 秒。跳过项为显式集成门禁；warning 为既有多线程进程 fork 测试的 Python 弃用警告。
- 首轮真实门禁 `35dd5a2a-ff0c-4801-95b8-0150c7570b7a`：三次启动均被镜像预检以 HTTP 503 拒绝，未创建任务、未调用模型；当次资源 inventory 不可用，门禁 failed。随后精确相同 API 环境的 Docker 连接/镜像预检及独立空账本核对通过，瞬时失败根因尚未确认；保留全部产物，并进行一次有界环境恢复重跑。
- 锁定工具：Ruff lint/format 全仓通过（420 文件），mypy 138 文件、TypeScript、生产 build 和 diff check 通过。
- 前端：首次误用 3017 页面代理到只接受 3010 Origin 的常驻 API，5 项实际检查被安全边界拒绝、37 passed / 1 skipped；产物保留 `artifacts/b5-ui-port3017-failure`。改用默认同源 3010 配置后，`PLAYWRIGHT_CHANNEL=chrome npx playwright test tests/product.spec.ts tests/progress.spec.ts tests/repairs.spec.ts --output=../artifacts/b5-ui-final`，42 passed，37.6 秒，包含两个新 429 回归以及浅深色/窄屏/无障碍。
- 第三轮真实门禁 `8170c202-32b4-4083-b761-69d380cada85`：3 passed，52.8 秒；总门禁 66.4 秒，`report.json` 为 passed。两次人工接受后下载字节分别与 HTTP diff 和本地不可变快照一致，撤回后禁止下载，刷新无重放；10 个精确执行容器均不存在、三任务不活动且无 pending、所有自有进程组结束、两个 Next tracked 文件恢复。

| 最终门禁案例 | 模型 / 工具调用 | 已知 tokens | 结果 |
|---|---|---|---|
| 返回类型错误 | 7 / 7 | 42,572 | candidate_verified；人工接受、478 字节补丁下载、撤回通过 |
| 计数值偏移 | 5 / 5 | 29,137 | candidate_verified；人工接受、389 字节补丁下载、撤回通过 |
| 模型期间停止 | 1 / 0 | 未知，保留 1,056,768 预留 | outcome_unknown / MODEL_USAGE_UNKNOWN；停止、不重放与资源释放通过 |

模型配置摘要 `638e54f42cc3bb03fc21c0bfa164c281c103fc169c0456676021de0f01a6f926` 绑定现有网关配置、角色协议和提示词，未公开 URL/key。最终下载 SHA-256：返回类型 `28ba0e41c6a07e3c85014d8dd8b9c7e437da4dfe16c787138a19499dea334475`；计数偏移 `731fa272aeef6be1770260e51463358eb5fa85497642d40529bb57940f5560e1`。

效果统计不能只报最后一次：本批实际修复 4 次，3 次 candidate_verified、1 次模型输出不合规导致 inconclusive；模型期间取消 2 次另计；首次环境预检拒绝的 3 个提交未进入模型。样本全为两个固定合成案例，不能推导任意仓库修复率或多角色优越性。

### 4.1 真实运行发现与修正

第二轮 `3054def7-1812-4771-952b-683060d7878a` 保留三项失败：

- 取消：捕获真实 `model_pending` 后提交停止，用量未知且预留不退款，最终 `outcome_unknown / MODEL_USAGE_UNKNOWN`。旧测试误要求 `cancelled`，与既有持久化契约冲突；已改为核对未知终态、相同预留、调用不增长、显式停止和刷新不重放。
- 返回类型案例：2 次模型调用、1 次工具调用，已知 9,095 tokens；模型输出不合契约，终止为 `inconclusive / INVALID_MODEL_OUTPUT`。保留为真实效果失败，不据此修改验收标准或猜测调整 prompt。
- 计数偏移案例：5 次模型调用、5 次工具调用，已知 29,067 tokens，真实冻结回归 `candidate_verified`；进入审批前，页面快速导航读详情触发 HTTP 429。已补修复记录 GET 的有限退避（最多 4 次、250/500/1000ms），取消请求会停止等待，写请求不自动重试；新增恢复/持续繁忙两项浏览器反例。

修正后再执行一次完整验收，累计结果保留，不把后续成功覆盖上述失败。三任务硬上限合计 36 次模型请求；取消测试目标是首请求，实际消耗以账本为准。

### 4.2 验收脚本复审修正

独立评审发现执行器超时升级分支可能跳过另一服务的清理及报告写入，以及父进程已退出但子进程仍运行时可能漏清理。已加入独立进程组、逐项清理和逐文件恢复 `next-env.d.ts` / `tsconfig.json`；真实进程反例曾在本机出现 `killpg(..., 0)` 返回 EPERM，现通过严格进程元数据核对精确 PGID/UID；错误、空输出或身份不符仍拒绝通过。修复后 24 项运行器回归通过，独立复审已准许执行真实门禁。无法确认回收的情况必须失败，不能当成无资源。该测试失败保留，不计入真实模型失败或效果样本。

## 5. 前端与运维

修复页保持当前中性工程风格，不新增装饰大卡片；顶部以单行路径说明运行、核验、决定和下载，并提供站内指南入口。能力说明、更新记录和使用指南同步。测试运行产物仅保存在本机忽略目录，不填充用户的最近任务列表。

验收后重新构建并重启 LaunchAgent 常驻前后端，检查 8030/3010 健康与站内指南；服务是否存活以实际进程和 HTTP 响应为准。

## 6. 交付结论与下一阶段

B5 与父任务 P1-01 在既定边界内完成：W01–W12 有实现、定向测试、真实执行和独立复核依据。最终[独立评审](reviews/P1-01-B5-quality-review.md)复核了下载原字节、模型摘要、资源回收与全部尝试，无未闭 Defect P0/P1。B4 提交 `31a5acf` 已在远端；本批 B5 交付快照随代码提交，提交标识以 Git 历史为准。

下一项按台账为 P1-02 项目经验记忆：先冻结人工确认、来源与版本匹配、过期/删除和错误经验负向测试，不能直接把模型建议或未验证修复写成可复用经验。任意工具项目接入与外部使用验证仍分别属于 P1-03/P1-04，没有提前完成。
