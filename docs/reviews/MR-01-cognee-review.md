# 企业代码质量审计报告

- **审计对象**：MR-01 独立选型实验及 [45 契约验证](../45-mr-01-cognee-contract-validation.md)，依据 [44](../44-p1-02-memory-product-and-integration.md) 与仓库 AGENTS.md。
- **审计版本**：工作区实验快照；Cognee 1.6.5，公开提交 `37f788bd287750e10d7610173e5332b3843f6ebd`。实验文件摘要见第 7 节。
- **技术栈**：Python 3.12、httpx、Cognee、SQLite、LanceDB、Ladybug；既有 DeepSeek 与授权 bge-m3。
- **业务场景**：General SaaS。
- **审查严苛度**：Mission-Critical；仅针对下述限定范围评分。
- **审计时间**：2026-10-19，Asia/Shanghai。
- **审计身份**：独立 reviewer，与实现者分离。除本报告外未修改实现；未读取私有模型配置、全局记忆或其他私有项目，未执行真实模型请求。

## 1. 代码质量评估卡

- **综合质量得分**：**97 / 100**，仅为已审实验代码及交付声明的复审得分，不是 Cognee 全项目、生产集成或修复效果评分。
- **上线评估结论**：**Conditional Pass / 有条件通过选型实验**。可以完成 MR-01 的选型决策并进入 M1-R；不得据此开放自动加工、外部 Hook 或向修复 Agent 注入记忆。生产上线尚未验收。
- **审计范围**：`experiments/mr01/` 的五个 Python 文件和 pyproject 共 696 行；README、evidence-summary、doc45 的证据与承诺核对。对 lock/manifest 做版本与漏洞结果检查；对上游只追踪与本契约直接相关的重试、记账、引用、删除和缓存路径，不作全仓审计。
- **未评估项**：产品持久 token/金额预留、权限授权账本、并发 CAS/outbox/恢复、修订与多来源有效性、完整物理删除、长期负载、OS 网络隔离、全部传递依赖安全与法律审计、前端交付、真实收益评测。这些未评估项阻断生产 Pass。

**缺陷汇总（复审后未关闭项）**

| Defect P0 | Defect P1 | Defect P2 | Defect P3 |
| :---: | :---: | :---: | :---: |
| 0 | 0 | 1 | 0 |

初审 3 项 Defect P1 已修复并复审关闭；不继续对关闭项扣分。

```text
STRICTNESS: Mission-Critical
SCENARIO: General SaaS
DEDUCTIONS: P2 x 1 x 3 x 1.0 x 1.0 = 3
RAW SCORE: 100 - 3 = 97
CAP APPLIED: none
FINAL SCORE: 97 / 100（仅本实验和已审声明）
VERDICT: Conditional Pass — 1 个供应链残余 P2，且生产关键路径未评估
NOT ASSESSED: 产品预算、授权、并发修订/删除、恢复、完整清理、收益等
```

**独立验证证据**

1. 运行 `MR01_INSTALLED_CONTRACTS=1 .venv/bin/python -m pytest experiments/mr01/test_probe.py experiments/mr01/test_installed_contracts.py -o addopts='--import-mode=importlib' -q`，实际输出：

   ```text
   ..............                                                           [100%]
   14 passed in 0.30s
   ```

2. Ruff 检查输出 `All checks passed!`。首次格式检查发现安装包测试需格式化；实现者修正后独立复查输出 `6 files already formatted`。这是工具门禁修正，不另列风格缺陷。
3. 独立读取安装发行包元数据：173 包；与实验 lock 的版本差异 `[]`，与 manifest 的版本差异 `[]`。127 份下载的 Python 源码与安装包逐文件比对，missing/different 均为 `[]`。这些结果不等于全包或全部依赖安全验真。
4. 核对全部四次 SDK 尝试的安全摘要与实际 calls/result：调用数、reported tokens、unknown 数和 phase 全部一致。两次空来源、一次 unknown 失败均保留，未算通过。
5. 成功产物 `artifacts/mr01/6056889825e443ad9874442999214867/`：1 个 segment 的 dataset/data ID 与投影一致；只读检查实验 SQLite 得到 `synthetic-memory`、revision 1、64 字符 digest、state `deleted`；结果保存映射 `1 → 0`、dataset_removed=true。2 LLM + 9 embedding 的 provider total_tokens 合计 5,774；金额未知。该目录 `.log` 文件数为 0。
6. 真实模型运行由实现者执行；reviewer 检查了产物和实现，没有独立重复消耗模型。14 项测试均为本地确定性/安装包配置检查，不能替代真实模型或远端取消证据。

## 2. 生产事故风险推演

以下为初审已修复问题的具体失效场景，保留修复历史；当前没有未关闭 P0/P1。

> **场景 1 — 空引用被当作来源验收**
> **触发条件**：CHUNKS 返回空 evidence，实验仍走到 completed。
> **失效机制**：原脚本只计数，没有非空断言；0 条可映射经过墓碑后仍为 0。
> **用户可见症状**：选型报告可能声称来源和删除拒用已验收，实际未测试任何引用。
> **爆炸半径**：本次选型结论。
> **可检测性**：旧产物直接显示 `evidence=[]`；现有非空断言会拒绝。

> **场景 2 — 重定向输出仍保存原始日志**
> **触发条件**：SDK 初始化默认文件 logger；上游记录查询或模型验证错误。
> **失效机制**：stdout/stderr DEVNULL 没有关闭 Cognee FileHandler。
> **用户可见症状**：实验目录产生原始日志，违反本次日志边界。
> **爆炸半径**：单次私有实验目录；本次材料是合成数据。
> **可检测性**：初始两次目录实际存在 `.cognee/logs/*.log`，reviewer 仅枚举路径，没有读取正文。

> **场景 3 — 响应和总耗时没有硬界**
> **触发条件**：provider 返回巨大响应或持续缓慢流数据。
> **失效机制**：原同步 post 整体读取响应，HTTP read timeout 仅限制无数据等待；独立 mock 曾返回 8,388,649 字节且被标为 received。
> **用户可见症状**：实验占用过量内存，provider 探针可能持续等待。
> **爆炸半径**：实验父进程。
> **可检测性**：现有 MockTransport 测试覆盖响应上限、绝对 deadline、停止后 unknown。

## 3. 详细审计明细

### Defect P0 — 阻断性缺陷

none（限定范围内）。

### Defect P1 — 严重缺陷

以下 3 项均 `[confirmed]`，状态 **closed**。

**MR01-R1：空来源与 0→0 不构成验收。**

- **位置**：初审 `worker.py:65–109`；修正位于当前 `worker.py:66–115`。
- **维度**：4 容错、9 测试强度。
- **机制**：CHUNKS 未覆盖 BaseRetriever 的 evidence hook；打开 verbose/include_references 仍可能得到空 evidence。源码与两次实际产物一致。
- **最小修正**：使用提供来源的 RAG_COMPLETION + only_context，并在清理前后加失败断言。

  ```diff
  - query_type=SearchType.CHUNKS,
  + query_type=SearchType.RAG_COMPLETION,
  + if not result["evidence"] or not result["mapped_evidence_before_delete"]:
  +     raise ValueError("missing_authority_mapping")
  + if result["cached_evidence_after_tombstone"] != 0:
  +     raise ValueError("stale_evidence_eligible")
  ```

- **复验**：成功产物为真实非空引用且 1→0；这里只证明一个合成来源，不能扩展到多来源、修订冲突或竞态。

**MR01-R2：默认文件日志未关闭。**

- **位置**：初审 `probe.py:189–221` 的 worker env；修正为当前 `probe.py:212`。上游 `logging_utils.py:564–594` 为默认开启的文件日志路径。
- **维度**：6 安全与数据合规。
- **最小修正**：

  ```diff
  + "COGNEE_LOG_FILE": "false",
  ```

- **复验**：最新成功目录 `.log` 数为 0；初始两份生成日志已登记哈希/大小后清理，安全失败证据保留于原产物与 `log-cleanup.json`。这不表示所有引擎数据库从不保存合成正文。

**MR01-R3：响应体和请求总时长缺少硬限制。**

- **位置**：初审 `probe.py:105–133`；修正为当前 `probe.py:72–85,144–156`。
- **维度**：3 资源安全、4 容错。
- **最小修正**：将同步整体读取替换为带绝对 deadline 的异步流读取，超过 1 MiB 或收到停止标记即失败；unknown 保留调用记录并关闭后续准入。

  ```python
  async with asyncio.timeout(min(30, max(0.01, self.deadline - time.monotonic()))):
      # 在 AsyncClient.stream 的读取循环中执行：
      if self.stopped or len(body) + len(chunk) > 1024 * 1024:
          raise ValueError("response_limit_or_cancel")
  # 结算记录时：
  if state == "unknown":
      self.stopped = True
  ```

- **复验**：新增 HTTPX 路径响应上限、deadline、在途停止测试通过。中间一次默认 5 秒读超时造成 6 次 unknown 的失败没有删除；修正后显式 timeout=30，并首次 unknown 后拒绝新请求。未声称远端已撤销。

### Defect P2 — 主要缺陷

**MR01-SC-01：锁定依赖仍包含已知存在不安全反序列化风险的 diskcache。**

- **位置**：`experiments/mr01/uv.lock:583–584`，diskcache 5.6.3；`docs/45-mr-01-cognee-contract-validation.md:152–160`。
- **维度**：12 依赖与供应链治理。
- **置信度**：`[confirmed]`，确认的是依赖存在、扫描非零及固定源码控制路径；未执行漏洞利用。
- **证据**：本地 vulnerability-audit.json 存在唯一公告 PYSEC-2026-2447 的两份重复项，fix_versions=[]；公告描述攻击者可写缓存目录时，pickle 读取可能执行代码。不得把重复记录算成两个漏洞，也不得把安装成功称为扫描通过。
- **范围判断**：受信合成实验使用新建私有目录，当前显式 SQLite，不读不可信缓存；没有证据证明本次实验存在可达的攻击者可写缓存路径，因此不判 P0/P1。生产接入尚需验证其补偿控制，残余 P2 保留。
- **已做修正**：`probe.py:213` 显式 CACHE_BACKEND=sqlite；上游 `get_cache_engine.py:153–166` 在显式设置情况下抛错而不回退 fs。安装包配置测试实证 `cache_backend` 出现在 model_fields_set。模块顶层仍可 import diskcache，因此不是移除依赖或修复漏洞。
- **验证方式 / 解除条件**：遵守 doc45 的 MR01-SC-01：锁定修复版并回归，或在产品接入中实测禁止 fs/回退、目录权限/所有权/符号链接和失败拒启，并由独立评审明确接受残余风险。生产开放前必须闭环。

### Defect P3 — 次要缺陷

none。

## 4. 维度覆盖声明

| # | 维度 | 未关闭发现 | 结论与边界 |
|---|---|---|---|
| 1 | 架构与边界 | none | 实验与主项目数据库、业务权威分离；没有宣称实验账本可投产 |
| 2 | SOLID 与现代设计 | none | 小型实验 Gate/worker/provider probe 职责可识别；未审整个 SDK 架构 |
| 3 | 并发、异步与资源安全 | none | R3 已关闭；准入锁/限次、请求期限与进程组清理已读；未做生产压力/崩溃恢复验收 |
| 4 | 容错与防御性编程 | none | R1/R3 已关闭；未知用量保留且阻止后续请求；exit 0 不单独作为成功 |
| 5 | 性能、伸缩性与计算效率 | none | 有实验载荷/调用/响应限制；生产容量与向量规模未评估 |
| 6 | 安全与数据合规 | none | R2 已关闭；子进程只获作业令牌；未读取真实凭据，也未证明 OS 沙箱 |
| 7 | 可观测性与运维友好度 | none | 四次尝试和 unknown 记录均保留；没有价格就不报金额为 0 |
| 8 | 类型安全与契约表达 | none | 检查了 JSON 边界、usage 数值过滤与有限实验字段；不是生产强类型契约验收 |
| 9 | 可测试性与死代码 | none | 14 项本地测试及真实运行产物相互区分；无真实收益结论 |
| 10 | 工程规范与一致性 | none | Ruff 检查/格式复核通过；独立锁未替换主项目依赖 |
| 11 | 数据与事务完整性 | none | 单来源实验映射/墓碑成立；生产 CAS、跨后端清理、恢复未评估 |
| 12 | 依赖与供应链治理 | 1 P2 | 173 包版本一致，127 份源码一致；保留 diskcache 公告与后续发布门禁 |

## 5. 整改清单

| ID | 严重级 | 负责人 | 截止 | 状态 |
|---|---|---|---|---|
| MR01-R1 | Defect P1 | 本轮实现 Codex | MR-01 复审前 | closed，非空来源与真实 1→0 复验 |
| MR01-R2 | Defect P1 | 本轮实现 Codex | MR-01 复审前 | closed，禁文件日志并清理本轮旧日志 |
| MR01-R3 | Defect P1 | 本轮实现 Codex | MR-01 复审前 | closed，响应/绝对时限与 unknown 门禁 |
| MR01-SC-01 | Defect P2 | 记忆引擎接入实现者 | M2-R 自动加工开放前 | open；跟踪定义和解除条件见 doc45 第 7 节 |

Mission-Critical 的 P2 预算为 0，所以本报告不出无条件 Pass。这个残余项允许继续受信选型与 M1-R 开发，不构成生产风险豁免。

## 6. 架构防腐与长效治理建议

1. **在实际发送边界统一账本**：模型和 embedding 每次请求先持久预留，unknown 不释放并停止作业；覆盖 SDK 内部重试、取消和崩溃恢复。成本属于 M1-R/M2-R 的网关集成与故障注入测试，防止上游 best-effort usage 被误当硬预算。
2. **以权威修订判断可用性**：保存 dataset/data 到 project/memory/revision/digest/代际的投影关系，取用与预留事务复核；墓碑先行、outbox 清理。成本为领域表和并发恢复用例，防止旧索引、缓存或合并引用绕过撤回。
3. **将依赖控制作为版本门禁**：保留安全扫描非零结果，显式缓存配置进入启动校验；每次升级重跑 SDK 来源、调用计数、日志和删除契约。成本为独立锁更新与固定小型验收，防止版本漂移改变默认行为。

## 7. 审计声明

- 本报告支持 **MR-01 选型实验有条件完成、继续 M1-R**；生产记忆功能仍待实现及独立验收。doc45 已明确区分实验能力和规划中的生产责任，未发现新的阻断选型缺陷。
- 实验 SHA-256：probe.py `e22faf3d8a75485bde5a1eca5c5ec54b15dd39b807549e9766abc68b0fa3f16f`；worker.py `36e508ff141427d85f6af3dc4f6090c8ba622c32ee27fb678772ee1440e46c94`；provider_probe.py `f717b822fe1ae26c08d9b0164cd779c44df936be1c77194c6126e7801475b353`；test_probe.py `762b320e178f81a4902c03c95b9a6db4ca18f4cffd6269872d0bca59b6d6213e`；test_installed_contracts.py `96918ad25c2fba5f9cb1179e1e05443a29f8fd41538a13c4a0f7ef0eef9b59b6`。
- 已审 doc45 为 2026-10-19 `in_review` 版本；后续补充文档索引、前端验收及状态记录由实现者提供证据，本报告不代替前端验收。改变实验控制或扩大能力承诺须复审。
- 未评估项见第 1 节。没有以静态源码、mock 或配置测试替代真实模型成功；也没有把实现者提供的真实运行称为 reviewer 独立模型重跑。
- 已施加生产风险豁免：none。MR01-SC-01 仍 open；实验补偿控制不自动延伸到产品。
- `[suspected]` 发现：none；未覆盖的区域保持“未评估”，不推定通过。
