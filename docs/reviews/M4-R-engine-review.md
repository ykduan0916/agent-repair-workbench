# 企业代码质量审计报告：M4-R 网页引擎

- **审计对象**：`web_memory/engine.py`、`engine_api.py`、`providers/cognee/runtime.py` 的 `on_job` 增量、`api.py` 生命周期接线、`ops/m4r_live_gate.py`、相关引擎测试。
- **审计版本**：2026-10-19 工作树，基线 `10b1d8b`；以最终复审的取消保护、四槽 admission 为准。
- **技术栈**：Python 3.12 / FastAPI / SQLite / asyncio / Cognee 隔离运行时。
- **业务场景**：General SaaS，可信本机单用户。
- **审查严苛度**：Mission-Critical。
- **审计时间**：2026-10-19。

## 1. 代码质量评估卡

- **综合质量得分**：100 / 100，针对本批增量未关闭缺陷计分。
- **上线评估结论**：Pass；只覆盖本批引擎接线，不能替代权威存储、Hook、前端及整批发布门禁。
- **审计范围**：约 600 行新增与增量及直接调用关系；没有改写生产代码，独立增加 4 条负向测试。
- **未评估项**：本分配范围内 none。完整网页交互、Hook 安装和领域权限由其他独立范围覆盖。

| P0 阻断 | P1 严重 | P2 主要 | P3 次要 |
| :---: | :---: | :---: | :---: |
| 0 | 0（两类已修复） | 0（无新增） | 0 |

```text
STRICTNESS: Mission-Critical
SCENARIO: General SaaS
DEDUCTIONS: no open incremental findings → 0
RAW SCORE: 100
CAP APPLIED: none after fixes and re-review
FINAL SCORE: 100 / 100
VERDICT: Pass within assigned engine scope
NOT ASSESSED: none within assigned scope
```

历史 M2 供应链风险与条件性 token 上限继续适用。本报告的 100 分不代表上游没有漏洞，也不代表模型效果、账单价格或多租户隔离已获验证。

## 2. 生产事故风险推演

> **场景 1 — SQLite 争用冻结 API 事件循环（已修复）**
> 触发：后台写事务持有 `BEGIN IMMEDIATE`，同时网页提交引擎任务。初稿 `submit` 在 asyncio 线程同步执行 SQLite。独立探针观测请求等待 1.064 秒，心跳停顿 1.076 秒；同一进程其他接口、轮询及取消处理都延迟。改为四槽有界 admission 工作线程后，独立 SQLite 锁测试要求心跳最大间隔小于 300ms，已通过。

> **场景 2 — 重复取消先释放所有权（已修复）**
> 触发：worker 仍在实际线程中执行，生命周期收到重复取消。初稿第一次取消后直接 `await worker`，第二次取消会结束线程包装 Task、进入 `finally` 关闭 fence，线程本身却没有退出。独立探针在老线程未释放 barrier 时取得了第二把 fence。修复为持续 shield 到物理 worker 结束；admission 线程交接及 close 同样保护，防止获得 fd 后取消丢失交接。停止信号仍由 threading.Event 传入运行时。

两项均由实现者 `/root` 修复，截止为 M4-R 完成前；独立复审已关闭。没有通过加锁后的未知重放来规避问题。

## 3. 详细审计明细

### P0 — 阻断性缺陷

none。

### P1 — 严重缺陷（已关闭）

#### M4E-01. 同步 admission 阻塞整个 API 事件循环

- **位置**：初稿 `EngineService.submit`；最终 `engine.py:210` 的 `_admit` 与 `:248` 的异步 submit。
- **审查维度**：3 并发与资源、5 背压。
- **置信度**：confirmed。
- **问题分析**：SQLite busy timeout 可真实阻塞 1 秒；逐请求串行阻塞扩大到整个 API 进程。
- **最小修复**：将持久 admission 放入 owned `asyncio.to_thread`，四槽 fast reject 限制队列；fence 由实际线程移交执行任务，HTTP 断开不能取消后台 admission。
- **验证方式**：`test_admission_sqlite_wait_does_not_block_event_loop`、`test_admission_queue_is_bounded`；在持有真实 SQLite 写锁时验证事件循环仍推进，第五个并发 admission 立即拒绝且没有启动额外线程工作。

#### M4E-02. 重复取消与 admission 交接丢失所有权

- **位置**：最终 `engine.py:258–275`、`:316–338`。
- **审查维度**：3 并发与资源、4 容错、11 持久所有权。
- **置信度**：confirmed。
- **问题分析**：取消 asyncio 包装任务不等于实际同步线程停止；fd 不得在物理工作结束前释放。改为 offload 后 admission 同样需要该保证。
- **最小修复**：admission、执行与 close 对 owned future 反复 shield/join，取消仅设置停止事件；等待实际结果后移交或关闭 fd。close 重新收集 admission 期间新出现的执行任务。
- **验证方式**：`test_repeated_cancellation_keeps_fence_until_actual_worker_exits`、`test_shutdown_cancellation_preserves_admission_handoff`；第二个服务在老 worker 活跃时仍被拒绝，shutdown 后 fence 可再次取得。

### P2 — 主要缺陷

无本批新增。上游 diskcache 既有风险继续受 M2 私有目录与 SQLite cache 补偿控制；未升级依赖、未扩大可信边界。

### P3 — 次要缺陷

none。

## 4. 维度覆盖声明

| # | 维度 | 发现数 | 备注 |
|---|---|---|---|
| 1 | 架构与边界 | none | HTTP 负责严格入参与操作接线，仍复用 M2 权威与 runtime |
| 2 | SOLID 与现代设计 | none | factory 提供故障与阻塞测试接缝，没有另建模型执行路径 |
| 3 | 并发、异步与资源安全 | 2 已关闭 | 实体线程、四槽 admission、跨进程 fence、shutdown 交接复测 |
| 4 | 容错与防御性编程 | 关联 M4E-02 | 未知结果持久保存；同 request 不重放；异常不打印原文 |
| 5 | 性能、伸缩性与计算效率 | 关联 M4E-01 | 快速背压；读取通过既有有界 invoke；操作数上限 10000 |
| 6 | 安全与数据合规 | none | Origin + 本地 header；provider 仅宿主私有配置；网页结果排除正文 |
| 7 | 可观测性与运维友好度 | none | job_id 在外发前附着，失败保留调用账本；unknown 不当零账单 |
| 8 | 类型安全与契约表达 | none | Pydantic 严格 shape，index/search/cleanup 互斥字段 |
| 9 | 可测试性与死代码 | none | 新增独立 SQLite/线程取消/背压测试；真实网关证据另列 |
| 10 | 工程规范与一致性 | none | 锁定 uv；审计负向测试 lint/format 通过 |
| 11 | 数据与事务完整性 | none | request 摘要幂等、running→unknown 恢复需取得 fence；无网络事务 |
| 12 | 依赖与供应链治理 | none 新增 | 复用已锁定 M2 引擎；既有残余风险不因网页接通消失 |

## 5. 整改清单与验证证据

| ID | 严重级 | 负责人 | 截止 | 状态 |
|---|---|---|---|---|
| M4E-01 | P1 | `/root` | M4-R 完成前 | fixed / independently rechecked |
| M4E-02 | P1 | `/root` | M4-R 完成前 | fixed / independently rechecked |

独立运行（不再次调用模型）：

```bash
uv run --frozen pytest tests/test_memory_engine_web.py tests/test_memory_engine_web_review.py tests/test_memory_runtime.py -o addopts='--import-mode=importlib' -q
```

```text
23 passed, 2 skipped in 4.93s
```

两项是需显式 opt-in 的已安装 SDK 契约测试；这次不是重跑 Cognee/模型。审计测试 Ruff：`All checks passed!`、`1 file already formatted`。

实际模型证据由实现者执行，评审者独立读取保留报告：`artifacts/m4r/44c48e54-3987-427d-b4a9-5def04b827e2/acceptance.json`。独立 API 端口与 SQLite 经 HTTP 建索引→检索→撤回→清理，报告 `passed`。索引 2 LLM + 7 embedding，检索 2 embedding，共 11 请求、6360 已知 tokens；货币价格 `unknown`。得到一条 exact 来源，`current_verification=false`，返回值无正文；清理一个派生根。合成资源不是业务收益或未知项目质量证明。该真实运行早于本批 asyncio 所有权修复；实际模型/SDK/调用契约未改变，差异由上述确定性并发测试覆盖。

## 6. 架构防腐与长效治理建议

1. 把独立 SQLite 阻塞与线程双取消测试纳入普通 CI；不需要模型成本，阻止协程结束被误当作实体工作结束。
2. 网页状态和 Agent 后续取用继续区分“操作结束”“参考可用”“本次验证通过”；任何历史返回均不得替代当前权威授权复核。

## 7. 审计声明

- 本报告针对上述工作树引擎接线；后续行为变更须复审增量。
- 本分配范围未评估项 none；领域权限、Hook 及完整页面验收属于分别分配的评审，不由本报告代签。
- 无新增豁免；既有 M2 供应链补偿与单用户边界继续有效。
- 两项缺陷均 confirmed；未把猜测或真实模型成功当作所有并发路径的证明。
