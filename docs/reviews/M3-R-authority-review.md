# 企业代码质量审计报告

- **审计对象**：M3-R A 批：`experience_memory/agent_models.py、agent_store.py、agent_runtime.py`，`engine_models.py` 授权增量，0014 迁移与 RunStore schema，AnalysisStore/RepairStore 同事务回调，AnalysisRuntime/RepairController 临时消息注入及 Cognee 搜索 deadline/on_job 增量。
- **审计版本**：`10b1d8b` 之上的 2026-10-19 工作区增量。
- **技术栈**：Python 3.12、SQLite/SQLAlchemy/Alembic、asyncio、现有 Cognee 适配器。
- **业务场景**：General SaaS，实际边界为可信本地单用户。
- **审查严苛度**：Mission-Critical。
- **评审者**：独立 Agent `m4_authority_review`，未修改生产代码。

## 1. 代码质量评估卡

- **综合质量得分**：100 / 100（修复后复审）。
- **上线评估结论**：Pass，仅适用于本批核心权威、事务和运行接入范围。
- **审计范围**：新增核心约 500 行，加已有文件局部增量与直接调用方，少于 2,000 行评审预算。
- **范围内未评估项**：none。BH-06 业务、网页 API/UI、真实 provider 验收属于其他批次；0014 中 BH-06 表只审阅迁移安全，不替代其业务评审。本报告不是整个 M3-R 交付完成结论。

| P0 阻断 | P1 严重 | P2 主要 | P3 次要 |
| :---: | :---: | :---: | :---: |
| 0 | 0 | 0 | 0 |

```text
STRICTNESS: Mission-Critical
SCENARIO: General SaaS
DEDUCTIONS: 0 unresolved (initial P1 fixed)
RAW SCORE: 100
CAP APPLIED: none
FINAL SCORE: 100 / 100
VERDICT: Pass within this bounded core review
NOT ASSESSED: none within scope
```

独立验证记录（失败保留）：

```text
# 修复前独立反例
uv run --frozen pytest tests/test_m3_authority_review.py -o addopts='--import-mode=importlib' -q
1 failed in 0.44s
Failed: DID NOT RAISE StoreError

# 完整范围复核修复后
uv run --frozen pytest tests/test_agent_memory.py tests/test_m3_authority_review.py tests/test_memory_engine.py tests/test_repair_store.py -o addopts='--import-mode=importlib' -q
84 passed in 6.27s

# 新增独立迁移旧授权保持与拒绝有损降级反例后
uv run --frozen pytest tests/test_m3_authority_review.py tests/test_agent_memory.py -o addopts='--import-mode=importlib' -q
21 passed in 3.98s

# 核心文件与测试静态检查
uv run --frozen ruff check <four core/migration files and two test files>
All checks passed!
uv run --frozen ruff format --check <same six files>
6 files already formatted
uv run --frozen mypy <three agent-memory files, both stores, both runtimes, Cognee runtime>
Success: no issues found in 8 source files
```

兼容性回归首轮：既有 analysis/repair runtime 与 web store 四组 `112 passed, 3 failed in 22.69s`。三个失败均为 0014 新增列/表后旧 SQL 测试构造仍假定十列、迁移对比未排除三张新增空表；实现者修正列清单/占位及新增表清单，保留唯一槽和旧数据不变断言。复跑结果追加在本报告末尾。

上述测试使用临时真实 SQLite、真实角色编排与受控测试适配器，未调用外部模型。角色请求字节校验不等于真实 provider 效果验证；完整交付还需真实链路证据。

## 2. 生产事故风险推演

> **初审场景 — 检索完成后新增矛盾规则仍被忽略**
> **触发条件**：已完成一次检索并准备参考正文；用户在预留前新增两条同 scope 的约束，同一 conflict_key 对应不同 conflict_value。
> **失效机制**：原 `admit_context` 只复核 selected refs，自身未变的旧经验照常通过；新的矛盾规则不在结果中，因此被忽略。
> **用户可见症状**：系统明知存在可确定性识别的冲突，仍把旧经验送给诊断/修复模型，违背 44/BM-08 的完整范围拒用契约。
> **爆炸半径**：受影响任务的后续带正文模型调用。
> **可检测性**：初始测试仅改变已选中引用，不能发现；独立反例可稳定发现。现已修复。

## 3. 详细审计明细

### P0 — 阻断性缺陷

none。

### P1 — 严重缺陷（已关闭）

#### M3-AUTH-01：调用预留必须复核整个检索范围

- **位置**：`experience_memory/agent_store.py::_retrieval_current、prepare_context、admit_context`，修复后约 168–223 行。
- **审查维度**：6 授权与数据边界、11 事务完整性。
- **置信度**：[confirmed]。
- **初审问题**：只验证已命中引用，遗漏检索后新增/变化的未命中约束。
- **修复**：从持久 `memory_operations` 取得原始 scope snapshot，在准备上下文及带正文模型预留的同一 SQL 写事务中重查 query grant、来源版本、完整有界 `_scope`；所有 scope 变化拒绝，冲突返回安全原因。空正文降级不会重检索、不会换用另一版本。

```diff
+ if selection.references:
+     self._retrieval_current(c, row)
  for snapshot, ref in zip(selection.snapshots, selection.references, strict=True):
      current, body = self._selected(c, row, ref)
```

- **复审依据**：独立 `test_new_conflict_outside_selected_refs_blocks_call_admission` 从失败转通过；实际 RepairController 的 prepare 后撤回测试零发送、零使用审计；已发送后撤回测试后续调用没有旧正文。

### P2 — 主要缺陷

none unresolved。

### P3 — 次要缺陷

none。

## 4. 维度覆盖声明

| # | 维度 | 未解决发现数 | 依据 |
|---|---|---|---|
| 1 | 架构与边界 | 0 | 宿主一次检索、角色独立；正文来源权威复核，模型不能创建权限 |
| 2 | SOLID 与现代设计 | 0 | binding/selection、SQL 权威、运行会话分层，未复制角色实现 |
| 3 | 并发、异步与资源安全 | 0 | shield 等待实际线程退出、重复取消、同事务预留后才发送 |
| 4 | 容错与防御性编程 | 0 | 检索未知费用阻断，拒用原因单独保存，不把异常当未命中 |
| 5 | 性能与伸缩性 | 0 | 范围 1,000 探针、引用 8、正文 8 KiB；复核有界 |
| 6 | 安全与数据合规 | 0 | 新 agent_context 默认不授予；全文临时注入、不入审计；M3-AUTH-01 已修复 |
| 7 | 可观测性与运维 | 0 | job、调用 hash、版本化引用及 token 分项可追踪，价格仍明确未知 |
| 8 | 类型与契约 | 0 | 严格 MemoryBinding，宿主固定工具/版本，verification 空引用 |
| 9 | 可测试性 | 0 | 独立冲突/迁移反例，实际角色请求字节、事务回滚、撤回与取消测试 |
| 10 | 工程规范 | 0 | 锁定 Ruff、mypy 通过；旧测试 schema 假设修正不削弱断言 |
| 11 | 数据与事务完整性 | 0 | model_started/reserved 与引用审计共同回滚；旧 grant 保持，非空降级拒绝 |
| 12 | 依赖与供应链 | 0 | 本批不引入新依赖；延续 M2-R 已披露的 SDK/条件性计费边界 |

## 5. 整改清单

| ID | 严重级 | 负责人 | 截止 | 状态 |
|---|---|---|---|---|
| M3-AUTH-01 | P1 | 实现 Agent `m3_design` / `/root` | M3-R 验收前 | fixed / 独立复验通过 |

## 6. 架构防腐与长效治理建议

1. 将“新增未命中但冲突的规则”保留为 CI 反例；成本一项临时库测试，防止以后优化为只核对 top-k。
2. 保留“prepare 后变更”和“已预留后变更”两个独立障碍点测试；少量测试适配器成本，防止把权威撤回和已发送请求追回混为一谈。

## 7. 审计声明

- 审计对应 `10b1d8b` 上本批核心代码及修复后的工作区增量；后续修改需增量复核。
- 范围内未评估：none。完整 M3-R 的 BH-06、网页接入/前端与真实模型验收不由此报告背书。
- 已施加豁免：none。
- 初审发现已复现并关闭；未用疑似问题驱动门禁，也未把合成 adapter 结果写成真实模型收益。

### 兼容性修复后的独立复跑

```text
uv run --frozen pytest tests/agents/test_runtime.py tests/agents/test_repair_controller.py tests/test_web_analysis_store.py tests/test_web_repair_store.py -o addopts='--import-mode=importlib' -q
115 passed in 22.69s
```

三项旧测试仅更新 schema 列数/新表排除清单，原唯一槽约束、非空降级拒绝和旧行保持断言均保留。无记忆 runtime 路径未因本批接入而改变测试结果。
