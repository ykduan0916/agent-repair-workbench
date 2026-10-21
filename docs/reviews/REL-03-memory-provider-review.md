# REL-03 记忆初始化与有限恢复：独立代码评审

- 审计对象：Cognee worker/runtime/gateway、固定分词资源安装器、记忆调用账本及 0015 迁移、Agent 记忆完成判定、网页安全失败提示及对应测试。
- 审计版本：2026-10-21 当前工作区 REL-03 增量；不覆盖工作区尚未提交的其他里程碑。
- 技术栈：Python 3.12、锁定 Cognee 1.6.5 SDK、SQLite/Alembic、Next.js。
- 审计者：专职评审 Agent `embedding_failure_review`，未实现生产代码；只写本报告、独立离线证据。
- 场景：Data Pipeline；严苛度：Mission-Critical。

## 1. 代码质量评估卡

**本地代码增量：Pass。一次真实索引及检索恢复链路：通过。真实接口稳定性与 M5 收益门禁：未通过。**

本结论允许交付已验证的初始化修复、固定诊断和有限恢复机制。补充真实证据已证明一次 Cognee 索引及检索可在指定失败后恢复；先前三连 HTTP 500 的失败仍保留。不能将这一次成功等同于接口稳定，也不能将确定性测试成功替代端到端结果。

| 未修复 P0 | 未修复 P1 | 未修复 P2 | 未修复 P3 |
|---|---|---|---|
| 0 | 0 | 0 | 0 |

```text
STRICTNESS: Mission-Critical
SCENARIO: Data Pipeline
DEDUCTIONS: none in the reviewed final local patch
RAW SCORE: 100
CAP APPLIED: none
FINAL SCORE: 100 / 100 within the stated code scope
VERDICT: Pass for local implementation; live reliability gate remains blocked
NOT ASSESSED: upstream service internals and memory effectiveness, outside code-review scope
```

该分数仅表示本次有限代码范围内未留存已确认缺陷，不能用于宣称产品整体质量或模型效果。

## 2. 故障风险推演与复核

### 冷启动分词资源缺失

原 runtime 每次使用新 TMPDIR；Cognee 的 bge-m3 分词器在 HuggingFace 离线失败后回退到 TikToken，TikToken 缓存缺失时仍尝试公网下载。`HF_HUB_OFFLINE` 不控制该下载。故障发生在 provider 请求之前，旧 worker 抹去阶段，仅剩 `engine_failed`。

独立离线复现使用锁定 SDK，拒绝网络和 tiktoken 资源下载：新缓存失败且出现一次下载尝试；放入本项目现有资源后真实 embedding adapter 初始化成功。资源 SHA256 与锁定 tiktoken 代码内的预期值一致。历史两次零 wire 的根目录无该缓存，已到 provider 的目录有缓存；这是强关联，但历史原异常已丢失，不能证明两次历史异常的唯一根因。

修复预检资源、预热分词器、关闭下载 fallback。另发现 Cognee 导入时 LiteLLM 远程价格表加载；已补本地价格表配置。最终离线验证初始化成功且 **零 socket 尝试**。

### 指定网关 500 后恢复

仅 embedding 的 HTTP 500 且 `new_api_error / do_request_failed` 进入最多三次总尝试。此响应不能证明上游未执行或未计费。每次独立预留，失败仍是 unknown/NULL 用量；后续有效向量只能恢复结果可用性，不能恢复账单确定性。

旧 unknown、超时、坏向量、LLM 失败、不同摘要/恢复组、撤权、取消、预算或截止时间到达均不借此放行。重试次数与每 job 的调用、tokens、时间上限同时成立。

## 3. 详细审计与复审结果

### 已闭环项

1. 分词缓存隐式下载依赖：固定大小与 SHA256、私有文件、符号链接拒绝、安装器不覆盖；worker 显式缓存并禁止下载 fallback。
2. 第二个初始化网络来源：`LITELLM_LOCAL_MODEL_COST_MAP=true`；独立真实 SDK 初始化零网络尝试。
3. 幂等键绑定：相同 call ID 改 request digest 或恢复组必须冲突；最终补丁和测试已覆盖。
4. 错误丢失：只输出固定阶段码，宿主白名单接收，原 provider 原因通过 COALESCE 优先保留；测试确认私密异常正文不会被输出。

### 最终增量检查

- 恢复组绑定同 job、lease、请求摘要；未恢复 pending 项必须全部是相同恢复组的指定 embedding 失败，最多三条。
- `recovered_by` 仅在有效向量及当前授权检查后写入；失败的 actual_tokens 仍为 NULL，预留额度不退款。
- M3 允许使用已恢复的读结果，继续保留固定记忆预算；验证角色不获取经验。效果和费用完整性分开。
- 0015 仅增可空列；历史 unknown 不自动获得恢复资格，已使用恢复账本拒绝降级。
- 网页只映射固定安全码，未知码不直接展示；仍展示未知用量，不自动重新提交整项索引。
- M5 的 usage_complete 与成本可比要求未被放宽。

无留存 P0/P1/P2/P3 代码发现。上游 500 的内部根因不在客户端证据可证明范围内；有界功能恢复已取得真实成功证据，稳定性和成本可比门禁继续阻塞。

## 4. 十二维度覆盖

| 维度 | 结论 |
|---|---|
| 架构与边界 | SDK、host gateway、权威账本职责保持分离 |
| SOLID 与设计 | 资源校验与部署安装独立，未新增通用平台抽象 |
| 并发与资源 | 同一 gateway 串行；取消/截止时间/lease/作用域逐次检查 |
| 容错 | 指定读请求有限恢复；其他不确定失败继续停止 |
| 性能 | 固定资源大小、最多三次、原 job 总预算不变 |
| 安全与合规 | 无原文/密钥/完整异常进入报告，私有缓存与摘要校验 |
| 可观测性 | 阶段码、逐次失败记录与 recovered_by 可区分结果和费用 |
| 类型与契约 | Ruff、mypy 通过；固定错误码与数据库约束 |
| 可测试性 | 阶段注入、恢复成功、预算/取消/撤权/超时/坏向量等负例 |
| 工程规范 | 锁定环境运行；文档和前端提示同步 |
| 数据与事务 | 每次独立预留、失败不退款、旧数据迁移逐行核对 |
| 依赖治理 | 无模型/依赖新增；本地资源哈希匹配锁定 SDK |

## 5. 独立验证证据

评审者执行：

```bash
uv run --frozen pytest tests/test_memory_tokenizer.py tests/test_memory_engine.py \
  tests/test_memory_jobs.py tests/test_agent_memory.py tests/test_memory_runtime.py \
  tests/test_web_memory.py -o addopts='--import-mode=importlib' -q
```

结果：**141 passed / 2 skipped**，16.95 秒；跳过项不是成功，评审者没有运行真实 provider 请求。

- 本次目标 Ruff 通过；mypy 18 个源文件通过。
- 独立只读核对主库与迁移前备份：40 张旧表、238 条旧行、全部旧列一致，当前 revision=0015_embedding_retries。
- 本机离线证据（不入 Git）：
  - `artifacts/embedding-review/tiktoken-jam8k9sz/acceptance.json`：fresh 失败/cached 成功。
  - `artifacts/embedding-review/sdk-tokenizer-nikjsn3h/acceptance.json`：真实 Cognee adapter 的同一结果。
  - `artifacts/embedding-review/patched-stack-6yced86r/acceptance.json`：定位 LiteLLM 价格表请求，记录仅 SDK 路径/函数。
  - `artifacts/embedding-review/patched-offline-zj18ik96/acceptance.json`：补丁及本地价格表配置，初始化 ready，network_attempts=0。
- 前端四类固定提示测试及完整回归由实现方运行，最终结果应填写交付文档；本报告没有将尚未结束的测试记为通过。

### 真实恢复补充复核

评审者只读核对 `artifacts/embedding-fix/exact-bbe48865a02b440c82504a7e52a5789e/` 的 setup、transport、retrieval 和实际 SQLite，未新增 provider 请求。独立结果另存 `independent-ledger-review.json`：

| 范围 | 实际结果 |
|---|---|
| 索引 | ready/done；11 次调用，其中 4 次成功 embedding、5 次失败 embedding、2 次成功 LLM；预留 32,001 tokens，已知 3,533 tokens |
| 检索 | available/matched/done；4 次 embedding，其中 2 次失败后恢复、2 次成功；预留 4,596 tokens，已知 32 tokens |
| 未知账单 | 共 7 条 unknown 保留 actual_tokens=NULL；每条 recovered_by 均指向同 job、同恢复组、同请求摘要的 received embedding；没有把失败费率算零 |
| 来源 | 16 项校验全部一致：confirmed head、revision/generation、digest、事件来源、ready root、dataset/data 映射和检索快照；返回 1 条经验，current_verification=false |
| wire | 相同实际 SDK 请求摘要分别出现 500→200、500→500→200；相同 payload 直接调用也出现 200/500/200；未证明上游内部根因 |

该证据证明的是实际恢复机制和正确来源映射，不是长期稳定性、M5 收益或成本完整性。此前三次 M4 失败没有删除。另一次 `exact-ad1…` 诊断的相对路径错误导致零 wire，属于本轮诊断调用错误，不能用来解释历史零 wire。

## 6. 整改清单与后续门禁

| 项目 | 状态 | 责任 |
|---|---|---|
| 分词器离线缓存、LiteLLM 本地价格表 | 已修复、独立验证 | REL-03 实现者 |
| 同 call ID 的摘要/恢复组冲突 | 已修复、回归覆盖 | REL-03 实现者 |
| 网关实际请求间歇失败 | 一次索引/检索恢复验证通过；连续失败历史保留，稳定性门禁仍阻塞 | 当前任务负责人 |
| M5 有无记忆收益 | 未由本次代码评审验收 | M5 评估负责人 |

后续需要保留失败和费用；对真实 payload 的参数结构、摘要和 HTTP 上下文进行有界比对，不能只用短探针推断实际索引输入一定正常。不得通过无限重跑、放宽向量校验或删除 unknown 获得成功结论。

## 7. 审计声明

仅适用于本次冻结 REL-03 增量。真实 HTTP 500 的网关内部原因未证实，历史零 wire 异常原文不可恢复，M5 效果尚未验证。若后续变更重试判定、费用处理、迁移或 provider 协议，必须复审对应增量。
