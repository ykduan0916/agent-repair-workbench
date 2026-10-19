# 企业代码质量审计报告：M2-R 权威层

- **审计对象**：`experience_memory/engine_models.py`、`engine_store.py`、`jobs.py` 本轮增量、`run_store/store.py` 本轮增量、`v0012_memory_engine.py`、`tests/test_memory_engine.py` 与规格 [47](../47-m2-r-engine-and-retrieval.md)。
- **审计版本**：`f8a6f35` 后 M2-R 工作区，2026-10-19 复审快照。
- **技术栈**：Python 3.12、Pydantic、SQLite、SQLAlchemy、Alembic。
- **业务场景**：General SaaS；可信单用户宿主，不评估多租户边界。
- **审查严苛度**：Mission-Critical。
- **审计时间**：2026-10-19。
- **独立评审者**：`/root/m2r_authority_review`；未修改实现，仅补充独立回归测试和本报告。

## 1. 代码质量评估卡

- **综合质量得分**：**100 / 100**，已审范围没有未关闭缺陷。
- **上线评估结论**：**Pass（权威层）**。这不是整个 M2-R 的上线批准；运行器、供应链、真实 Cognee 与主库操作另有门禁。
- **审计范围**：单个权威层里程碑，新增实现约 510 行，连同规格、测试与既有存储增量约 1,100 行。
- **未评估项**：本范围内 none；范围外的进程/网络适配、前端、真实模型效果和主库迁移由其他验收记录覆盖。

| P0 阻断 | P1 严重 | P2 主要 | P3 次要 |
| :---: | :---: | :---: | :---: |
| 0 | 0（2 项修复关闭） | 0 | 0 |

```text
STRICTNESS:   Mission-Critical
SCENARIO:     General SaaS
DEDUCTIONS:   未关闭缺陷 0 → Σ = 0
RAW SCORE:    100
CAP APPLIED:  none
FINAL SCORE:  100 / 100
VERDICT:      Pass — 仅权威层，发布窗口与 profile 重索引问题已修复复审
NOT ASSESSED: 本审计范围内 none
```

初审一项 confirmed P1 对应原始分数 92、封顶 79；不以“其他代码通过”稀释该阻断项。实现者修复后，复审清单中无开放缺陷，按现状重新计算。

## 2. 生产事故风险推演

> **场景 1 — 索引发布与结束之间进程退出（已关闭）**
> **触发条件**：`publish_index` 提交 `ready`，紧接着在 `finish_job` 前进程退出，或结束失败后取消该任务。
> **失效机制**：初审 `_scope` 只按根的 `ready` 状态筛选，缺少来源任务 `done` 条件。新查询可以选择尚未完成或已取消的索引根。
> **用户可见症状**：索引任务未完成，查询却把其派生物当作可用来源；账单未知时尤其不能据此继续运行。
> **爆炸半径**：该项目的一条经验及使用它的检索。
> **可检测性**：原路径可从任务状态与根状态不一致发现；修复后直接返回 `index_required`，维护清理可回收该根。

> **场景 2 — 配置切换后的重索引无法恢复检索（已关闭）**
> **触发条件**：已有 profile A 的完成索引；切换 embedding 配置至 B 并完成重索引，B 的随机 UUID 字典序小于 A。
> **失效机制**：增量实现先选 UUID 最大的根，再比较 profile。即使 B 已完成，仍选 A 并拒绝查询；重复重索引的结果取决于随机 ID。
> **用户可见症状**：按配置漂移错误要求完成重索引后，仍持续显示 `embedding_profile_changed`。
> **爆炸半径**：更换 embedding 配置的项目检索。
> **可检测性**：安全错误码可见，但不能让用户靠反复重索引碰巧得到更大 UUID；修复后先筛选当前 profile。

## 3. 详细审计明细

### P0 — 阻断性缺陷

none。

### P1 — 严重缺陷

#### P1-1. 未终结的索引根进入检索范围（confirmed，closed）

- **位置**：`src/agent_workbench/experience_memory/engine_store.py:161–173`，`_scope`；调用窗口见 `providers/cognee/runtime.py` 的 `publish_index` → `finish_job`。
- **审查维度**：11 数据与事务完整性、4 容错。
- **置信度**：confirmed；已追踪分事务窗口和新查询路径，并运行修复后的边界复现。
- **修复补丁**：实现者将来源任务完成状态纳入权威查询。无需假定 SDK 进程的退出等于事务完成。

```diff
- SELECT root_id,dataset_id,data_id FROM memory_engine_roots
- WHERE project_id=? AND memory_id=? AND generation=? AND state='ready'
+ SELECT r.root_id,r.dataset_id,r.data_id FROM memory_engine_roots r
+ JOIN memory_jobs j ON j.job_id=r.root_id AND j.state='done'
+ WHERE r.project_id=? AND r.memory_id=? AND r.generation=? AND r.state='ready'
```

- **验证方式**：`test_published_but_unfinished_index_not_retrievable` 覆盖发布后未完成与取消清理；独立新增 `test_review_unknown_index_bill_never_becomes_retrievable` 覆盖 reserved → ready → finish 拒绝 → unknown → 检索拒绝与待清理。独立临时库脚本发布后取消，再 prepare_search 返回 `index_required`。

#### P1-2. 重索引选根依赖随机 UUID 顺序（confirmed，closed）

- **位置**：`src/agent_workbench/experience_memory/engine_store.py`，`_scope` 的 ready/done 根筛选与 profile 校验。
- **审查维度**：4 容错、11 数据与事务完整性。
- **置信度**：confirmed；同一代际允许多次索引，旧根与新根同时存在，`roots[-1]` 不代表最新或配置匹配。
- **修复补丁**：先选取与查询 profile 一致的根，再确定性选择；没有当前 profile 时才返回配置漂移错误。

```diff
- if roots[-1]["profile_digest"] != query["profile_digest"]:
+ matching = [r for r in roots if r["profile_digest"] == query["profile_digest"]]
+ if not matching:
      raise StoreError("embedding_profile_changed")
- ref.update(dict(roots[-1]), ...)
+ ref.update(dict(matching[-1]), ...)
```

- **验证方式**：配置漂移/密钥轮换测试，以及固定旧 UUID 较大、新 UUID 较小的重索引回归；见本报告增量验证记录。

### P2 — 主要缺陷

none。初审提醒保留 0011 旧表完整性检查，实现者已恢复 `run_store/store.py:294–298`；不把提醒重复计为已确认缺陷。

### P3 — 次要缺陷

none。

## 4. 维度覆盖声明

| # | 维度 | 未关闭发现 | 结论 |
|---|---|---|---|
| 1 | 架构与边界 | 0 | Cognee 引用只定位来源；正文从当前权威记录返回，保持 current_verification=false |
| 2 | SOLID 与现代设计 | 0 | 引擎授权在预算事务扩展点实现；不把具体网络客户端放入存储 |
| 3 | 并发、异步与资源安全 | 0 | BEGIN IMMEDIATE 将授权复核与 reservation 串行化；重复调用只首次 admitted |
| 4 | 容错与防御性编程 | 0 | 无来源、失效、范围变化和未知账单均拒用；P1-1 已关闭 |
| 5 | 性能与伸缩性 | 0 | 完整扫描上限 1000、可检索范围 8、上下文 8192 UTF-8 字节；超限拒绝而非截断装完整 |
| 6 | 安全与数据合规 | 0 | 项目/连接/内容类型/用途授权；重启或暂停恢复改变来源版本，旧快照不能继续用 |
| 7 | 可观测性与运维 | 0 | 安全 reason、账单状态与单调 reservation 保留；金额无定价时 unknown |
| 8 | 类型安全与契约 | 0 | 严格 Pydantic 契约、有限枚举、范围校验与完整快照比对 |
| 9 | 可测试性 | 0 | 真实 SQLite 下测试状态变更、重复调用、作用域、UTF-8 上限和迁移；无模型依赖 |
| 10 | 工程规范 | 0 | 锁定 Ruff 独立通过；测试追加至已被 pytest 收集的目录 |
| 11 | 数据与事务完整性 | 0 | 授权/CAS、版本绑定、来源代际、任务完成态、不可随意降级与旧表保护 |
| 12 | 依赖与供应链 | 0 | 权威层没有新依赖；Cognee/diskcache 补偿控制属于独立运行层审计，不在此认定已消除 |

**独立执行证据**：

```text
uv run --frozen pytest tests/test_memory_authority.py tests/test_memory_jobs.py tests/test_memory_engine.py -o addopts='--import-mode=importlib' -q
80 passed in 3.84s

uv run --frozen ruff check src/agent_workbench/experience_memory src/agent_workbench/run_store/store.py src/agent_workbench/run_store/migrations/versions/v0012_memory_engine.py tests/test_memory_engine.py
All checks passed!
```

评审者独立新增 8 项参数化展开测试：查询 grant 撤销、来源暂停再启用、策略更新、grant 过期、9 条范围拒绝、9000 字节中文上下文拒绝、unknown 索引账单拒用、填充 M1 数据的 0011→0012 迁移。迁移测试比较 9 张旧表全部行一致；未操作常驻主库。

### 2026-10-19 增量复审：配置绑定与安全失败原因

增量范围包括 `memory_engine_config` 的唯一根摘要绑定、每个派生根的 `profile_digest`、查询配置漂移拒绝与 `memory_operations.failure_code`。独立读取直接运行器调用方确认绑定实际被调用；配置 fingerprint 包含 embedding endpoint/model/dimensions/revision，不包含密钥，轮换密钥不需要重索引。配置绑定在写事务中检查，无法用另一根目录对原存储做虚假清理。

`record_failure` 只接受有限安全码，使用项目和 lease token 校验，`COALESCE` 保留首个原因；数字费用照常保存。评审者新增 `test_review_failure_reason_whitelist_scope_and_first_reason`：原始异常字符串、跨项目、错误 token 均拒绝；embedding 校验失败原因不被通用错误覆盖，actual_tokens 仍为 20。

独立执行：

```text
uv run --frozen pytest tests/test_memory_authority.py tests/test_memory_jobs.py tests/test_memory_engine.py tests/test_memory_runtime.py -o addopts='--import-mode=importlib' -q
93 passed in 6.95s

uv run --frozen ruff check src/agent_workbench/experience_memory src/agent_workbench/run_store/store.py src/agent_workbench/run_store/migrations/versions/v0012_memory_engine.py tests/test_memory_engine.py
All checks passed!
```

另用独立临时 SQLite 脚本固定旧根 `ffffffff-ffff-4fff-8fff-ffffffffffff`、新根 `00000000-0000-4000-8000-000000000001`，分别完成旧/新 profile 索引后查询新 profile，准确选择新根，输出 `inverse_uuid_reindex_passed`。脚本无网络请求；该确定性场景验证 P1-2 修复，避免依赖随机 UUID 测试运气。

## 5. 整改清单

| ID | 严重级 | 负责人 | 截止 | 状态 |
|---|---|---|---|---|
| P1-1 | Defect P1 | M2-R 主实现 Agent `/root` | M2-R 完成前 | closed；复审测试通过 |
| P1-2 | Defect P1 | M2-R 主实现 Agent `/root` | M2-R 完成前 | closed；先按当前 profile 筛选，再选根 |

## 6. 架构防腐与长效治理建议

1. **保留发布窗口回归**：任何新的派生索引适配器都复用 ready+done 判定；成本是现有测试持续在 CI 执行，防止引擎成功冒充作业完成。
2. **按调用前/返回前分别复核**：后续 M3 Agent 注入沿用这一接口，不能直接读取 `memory_projections` 或 Cognee 正文；成本是两次受限本地事务，防止撤回和缓存竞争绕过权威状态。
3. **迁移测试保留真实旧数据**：后续迁移均保存非空 0011 基线和逐行比较，防止只测空库遗漏历史数据兼容。

## 7. 审计声明

- 已审计 `f8a6f35` 后当前工作区中上述限定文件；后续相关增量需要复审。
- 本范围无未评估项。运行层、供应链、真实网关、主库迁移及 UI 另行验证；本报告不得替代整体 M2-R 门禁。
- 已施加豁免：none。
- 未读取其他项目、全局记忆、私有凭据；未执行真实模型或 embedding 请求。
- 限定结论不包含记忆业务收益或模型正确率，不代表产品 Agent 已消费记忆或外部 Hook 已接入。
