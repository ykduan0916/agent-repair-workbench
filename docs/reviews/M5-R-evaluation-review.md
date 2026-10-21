# 企业代码质量审计报告：M5-R 受控对照评估

- **审计对象**：`ops/m5r_eval.py`、`ops/m5r_embedding_contract.py`、`tests/test_m5r_eval.py`、`docs/50-m5-r-memory-evaluation.md` 与台账/文档入口/更新记录增量。
- **审计版本**：基于 `cf91986` 的工作树；执行脚本由实验 plan 的 `code_sha256` 进一步绑定。
- **技术栈**：Python 3.12、既有 RepairController、Cognee 1.6.5、SQLite、Docker。
- **业务场景**：General SaaS（本机受控评估批处理）。
- **审查严苛度**：Mission-Critical。
- **审计时间**：2026-10-21。
- **独立性**：评审者未编写被审实现，未执行真实模型调用；仅运行无外发测试和静态检查，写本报告。

## 1. 代码质量评估卡

- **综合质量得分**：100 / 100（当前已审实现范围无未关闭缺陷；分数不代表模型效果）。
- **上线评估结论**：**Pass，仅限评估采集器、失败证据与文档/UI 入口增量**。实际记忆收益验收为 **blocked**，M5-R 父任务保持 in_progress；不得把代码评审通过写成效果达标。
- **审计范围**：约 550 行执行脚本、SDK 模拟实验辅助脚本、确定性单测、三轮真实产物及评估规格；不重新审计已有生产记忆/修复系统。临时 provider 分块改动已回退，不属于最终产品交付。
- **未评估项**：已交付范围 none；跨版本/独立项目及有效记忆暴露的效果验证属于明确未完成的后续范围。

| P0 阻断 | P1 严重 | P2 主要 | P3 次要 |
| :---: | :---: | :---: | :---: |
| 0 | 0 | 0 | 0 |

```text
STRICTNESS: Mission-Critical
SCENARIO: General SaaS
DEDUCTIONS: none remaining -> 0
RAW SCORE: 100
CAP APPLIED: none
FINAL SCORE: 100 / 100
VERDICT: Pass — delivered evaluator and evidence only; benefit gate remains blocked
NOT ASSESSED: none within delivered scope; generalization and memory benefit remain unfinished
```

## 2. 生产事故风险推演

以下两项在真实调用前提出并修复；不计为现存缺陷。

1. **读取证据失败跳过回收**：原 `collect()` 先调用 `reader.detail()`，同一 try 中随后才回收；比较记录损坏可跳过已持久容器清理。现回收位于独立 finally，分别保存回收前任务与 `after_recovery_task`；读证据失败仍必须执行 apply/inspect，不能自动重放任务。
2. **索引准备期间配置漂移**：原各索引准备没有执行冻结检查，而 embedding 配置会重读；长实验中可能产生不同配置的处理组。现每份准备和 worker 在外发前调用 `frozen_configuration`，核对代码、Adapter 配置、embedding 与镜像，随后同一 provider 快照传入索引/检索运行时。漂移中止后续单元，保留计划分母。

## 3. 详细审计明细

### P0 — 阻断性缺陷

none。

### P1 — 严重缺陷

none remaining。已关闭项：

| ID | 位置（修复后） | 维度 / 置信度 | 最小修复 | 验证 |
|---|---|---|---|---|
| M5-01 | `ops/m5r_eval.py::collect` | 3 资源安全 / confirmed | 精确回收独立放 finally；不以读取结果为前提；回收后独立确认 | `test_collection_failure_still_reclaims_resources` |
| M5-02 | `ops/m5r_eval.py::frozen_configuration/execute/worker` | 11 实验完整性 / confirmed | 每组外发前冻结检查；传递已核对 provider 快照；漂移中止 | `test_frozen_configuration_denies_drift_without_external_calls`，调用路径复核 |

### P2 — 主要缺陷

none。

### P3 — 次要缺陷

none。

## 4. 维度覆盖声明

| # | 维度 | 发现数（未关闭） | 备注 |
|---|---|---|---|
| 1 | 架构与边界 | 0 | 直接复用生产控制器；隔离实验库，不改变产品 API 预算和权限 |
| 2 | SOLID 与现代设计 | 0 | 计划、准备、执行、证据收集和汇总分别可测；没有新业务 Agent |
| 3 | 并发、异步与资源安全 | 0 | 单 worker 顺序运行、780 秒外层界限、精确容器回收；继承 SDK parent watchdog，额外确认 writer 锁及 leased 作业 |
| 4 | 容错与防御性编程 | 0 | 所有失败保留、未知不重试；固定安全错误码；无法确认资源即停止后续任务 |
| 5 | 性能与伸缩性 | 0 | 固定 12 单元、4 次索引、串行；不评估生产吞吐 |
| 6 | 安全与数据合规 | 0 | 只使用审核合成资料；私有配置不打印；产物 0700/0600；报告不含原始模型正文 |
| 7 | 可观测性与运维 | 0 | plan/索引/单元/summary 单独持久化；原始失败和保留费用可复查 |
| 8 | 类型安全与契约 | 0 | 生产 Budget/MemoryBinding/领域契约复用；脚本内部结构化 JSON 不作为外部 API |
| 9 | 可测试性 | 0 | 均衡顺序、重放拒绝、失败分母、未知账单、配置漂移、回收失败路径等 10 项无外发测试 |
| 10 | 工程规范 | 0 | 锁定 uv，独立 Ruff check/format 通过 |
| 11 | 数据与事务完整性 | 0 | 计划先冻结、单元独占创建、原子更新；索引费用只计一次；生产账本仍是权威 |
| 12 | 依赖与供应链 | 0 | 无新增依赖/模型；继承锁定 SDK 已有供应链边界，不宣称重新审计其全部依赖 |

独立验证命令：

```bash
uv run --frozen pytest tests/test_m5r_eval.py -o addopts='--import-mode=importlib' -q
uv run --frozen ruff check ops/m5r_eval.py tests/test_m5r_eval.py
uv run --frozen ruff format --check ops/m5r_eval.py tests/test_m5r_eval.py
```

实际输出：`10 passed in 1.01s`；`All checks passed!`；`2 files already formatted`。

## 5. 整改清单

| ID | 严重级 | 负责人 | 截止 | 状态 |
|---|---|---|---|---|
| M5-01 | Defect P1 | root 实现者 | 实际模型调用前 | closed，独立复审 |
| M5-02 | Defect P1 | root 实现者 | 实际模型调用前 | closed，独立复审 |

## 6. 架构防腐与长效治理建议

1. 保留实验冻结机制；任何运行中实现调整都另建版本，不替换旧失败。成本是存储少量摘要和完整账本，防止挑选结果。
2. 计划分母与实际暴露率独立报告；失败索引/零匹配/未知账单不能从统计中消失。金额无价格依据保持 unknown。
3. 人工同版本提示消融只支持局部效果描述。文档 44 要求的相关/不相关跨版本和独立工具项目仍未完成，M5-R 父任务保持 in_progress，不能用本批替代泛化验证。

## 7. 审计声明

- 本报告最终审计基于 `cf91986` 后工作树，真实运行分别固定各自 plan 的实现摘要；不同轮次未拼接成单一有效实验。
- 评估采集器/证据/文档增量准予交付。实际记忆收益未完成，不适用这个 Pass；父任务不得标 done。
- 已施加豁免：none。
- 不对模型成功率、记忆收益或商业可靠性作推断。真实运行由 root 执行，评审者后续只读复核。

### 7.1 第一轮产物与单条 embedding 兼容修复复审

只读核对 `artifacts/m5r/f41a22b3ca004f7e8a622ef338f58c93/`：plan 为 12 单元，目录 0700、plan 0600；4 个无记忆单元全部当前 candidate_verified、四格比较齐全、资源与不变量确认。四份记忆索引失败、账单 unknown，因此 8 个记忆单元均 setup_blocked，无模型修复/经验暴露。原计划分母未删除；本轮**不能估计记忆的实际效果**。

独立探针报告（实现者运行、评审者只读）`provider-probe-20261021.json`：单条 HTTP 200、16 已知 tokens；两条输入批次 HTTP 500、用量未知。该探针属于兼容性诊断，费用不能从本次工作成本中隐去，也不属于修复样本。

最小兼容修复为 `CogneeRuntime._environment()` 将 SDK `EMBEDDING_BATCH_SIZE` 从 16 改为 1，并设置 `EMBEDDING_MAX_CONCURRENT_DATA_POINTS=1`。这在外发前拆分新请求，没有对已失败/未知批次进行自动重试；每条实际 HTTP 调用仍走原有持久预算，40 次 embedding/100k tokens 上限保持，未扩大限额。

独立执行结果：

- `tests/test_memory_runtime.py tests/test_memory_jobs.py tests/test_memory_engine.py tests/test_m5r_eval.py`：`84 passed, 2 skipped in 7.33s`；跳过为既有 opt-in 实验，不算通过。
- 锁定 SDK 执行 `ops/m5r_embedding_contract.py`，真实 `index_data_points` 和 `OpenAICompatibleEmbeddingEngine` 配合 MockTransport：`{"sdk":"1.6.5","requests":[1,1,1],"network":"mock_only"}`。证实三份文本产生三个单输入 HTTP 请求；没有实际 provider 调用/模型下载。

第二轮须另冻结 plan 与代码摘要，保留首轮所有失败、已知及未知账单，不以第二轮结果替换首轮。当前修复无未关闭 P0/P1，第二轮执行门禁通过；最终交付仍待第二轮实际结果及页面同步复核。

### 7.2 第二轮保留与网关分块复审

第二轮 `5e9866f20d35459f946912f413e09cd2` 同样保留全部 12 单元：4 个无记忆修复通过并确认资源/不变量，8 个记忆单元 setup_blocked，实际暴露仍为 0。第二轮已知 tokens 下界 130,464，其中无记忆 Agent 117,378、correct 索引 13,086、wrong 索引已知 0；四份失败索引存在未知用量，0 是已知下界，**不是免费**。本轮亦不能估计记忆效果。SDK 配置分块测试只覆盖 `index_data_points` 路径，不能证明其他直接 `embed_text(list)` 路径也分块；真实失败否定了“仅该环境变量即可完整兼容”的假设。

新增 `Gateway.forward()` 前置分块修复（44 行）复审：

- 批次 2–16 条且全体合法才分块；异类型、超长、过多条目的完整请求在任何外发前被原验证拒绝。
- 每片通过原 `_forward_one()`，保持逐条锁、授权、原截止时间、预算预留与结算。未新增对已完成前缀的重试。
- 第二片 HTTP 500、额度不足、授权撤回或向量错误均中止整个批次并设置 stopped；SDK 重试不会产生新外发。
- 只合并原逻辑已验证的 1024 维有限向量，重排响应 index，usage 等于实际片调用之和；不重新向账本记一次合并费用。

独立执行 `tests/test_memory_embedding_batch.py tests/test_memory_engine.py tests/test_memory_jobs.py tests/test_memory_runtime.py`：`82 passed, 2 skipped in 7.15s`；Ruff 输出 `All checks passed!`。无未关闭 P0/P1，可另冻结第三轮；第三轮仍不能覆盖前两轮失败或取消未知费用。

### 7.3 第三轮反证、回退与最终效果边界

第三轮 `53b705400c1f43a98df08ea85dfb9ab2` 仍为 4 个无记忆修复通过、8 个记忆单元 setup_blocked、实际记忆暴露 0。独立只读重算三轮原始结果和索引账单，未依赖 summary 作为唯一来源：

| 轮次 | 计划 | 已完成无记忆 | 记忆准备阻塞 | 已知 tokens 下界 | 未知 embedding 调用 |
|---|---:|---:|---:|---:|---:|
| 原配置 f41a22… | 12 | 4 | 8 | 138,518 | 4 |
| SDK 环境分块 5e9866… | 12 | 4 | 8 | 130,464 | 2 |
| 网关分块 53b705… | 12 | 4 | 8 | 152,722 | 4 |
| 合计 | 36 | 12 | 24 | 421,704 | 10 |

六个独立 provider 诊断探针还有 192 已知 tokens、3 条未提供 usage 的响应（两个 500、一个 400）；不能由 400 推定实际收费，也不能将缺少用量写为免费。全工作已知下界 **421,896 tokens**，金额未知。三个实验版本不同，且全部复用相同两个 fixture；不得混作 36 个独立真实项目样本，也不得据记忆计划完成 0/4 写成“记忆修复率为 0%”。实际含记忆的 Agent 修复没有启动，收益无从估计。

`provider-shape-probe-20261021.json` 中，合法两条输入批次可以返回 200，而另一条单输入返回 500。这反驳了“接口一概不支持批量”的归因，也不能仅凭不同输入的返回差异断言时序性不稳定。上游请求失败的具体原因仍未确定。

实现者已撤回本轮临时 `gateway.py`、`runtime.py`、`test_memory_runtime.py` 修改；独立 `git diff` 确认三者与 HEAD 无差异。实验补丁/批次测试留在私有 `artifacts/m5r/`，没有以 SDK mock 测试通过作为生产兼容性已修复的证据。停止新增真实轮次，原生产预算、工具、模型和 SDK 批次行为保持原状。

独立资源复核（真实只读检查，无新模型/无修复重放）：

- 12 个已完成任务分别通过 `RecoveryService.recover(apply=False)`，全部 exit 0；每任务 5 个精确绑定 Docker 资源，共 60 个全部 absent。
- 18 份隔离 SQLite 检查无 leased 记忆作业；12 份记忆引擎 writer 锁均可获得。
- 全部 36 个计划单元结果文件仍在，setup_blocked 未被丢弃。

**效果门禁结论：blocked**（需要先恢复对合法输入可用的 embedding 请求，再另冻结对照；跨版本/独立工具仍未覆盖）。**M5-R 父任务保持 in_progress**。此结论是正确报告实际未完成的验证，不是通过修改成功判定来让评估达标。

### 7.4 最终回退快照与文档复核

- 独立再次运行最终 `tests/test_m5r_eval.py`：`10 passed in 0.79s`；执行脚本、SDK 辅助脚本及测试 Ruff `All checks passed!`。
- 读取实现者保留的完整最终回退测试日志：`1878 passed, 69 skipped, 1 warning in 270.38s`。warning 为既有多线程 fork 测试，跳过不算通过。该全量由实现者运行，评审者检查日志；不得与评审者亲自执行的专项混称。
- 最终文档 50 区分采集器 exit 0 与效果门禁；三轮结果、未启动分母、索引/探针成本下界、错误归因回退、解除条件与跨版本缺口均已写明。
- 文档入口和更新记录仅新增可访问报告与“当前受向量服务请求失败阻塞”说明；没有更改产品能力为已提升，也没有将合成评估混入用户项目运行列表。
- 实现者另完成 TypeScript/生产构建、6 项产品导航测试与实际生产页面 1440/390px 文档阅读检查；这些为实现者提供的页面验收证据，本评审独立覆盖代码/内容与实际实验资源，未重新截图全部页面。
- 报告更新后常驻服务最终构建/重启由 root 执行，并现场检查健康；不改变本报告的效果阻塞结论。
