# 企业代码质量审计报告

- **审计对象**：M4-R `web_memory/api.py、store.py、models.py、lifecycle.py`，`api.py` 集成，`v0013_web_memory.py`，RunStore schema 检查及 `test_web_memory.py`。
- **审计版本**：`10b1d8b` 上的工作区增量，2026-10-19。
- **技术栈**：Python 3.12、FastAPI、SQLAlchemy、SQLite、Alembic。
- **业务场景**：General SaaS；实际部署边界为可信本地单用户。
- **审查严苛度**：Mission-Critical。
- **评审者**：独立 Agent `m4_authority_review`，不参与生产实现。

## 1. 代码质量评估卡

- **综合质量得分**：100 / 100（修复后复审）。
- **上线评估结论**：Pass（本批权威 API 范围）。初审发现的 P1、P2 均已修复并独立复验；UI 的分页操作仍需对应前端门禁。
- **审计范围**：约 800 行生产代码及直接调用边界；引擎执行、Hook relay、UI 由另外批次评审，本报告不能替代那些门禁。
- **范围内未评估项**：none。

| P0 阻断 | P1 严重 | P2 主要 | P3 次要 |
| :---: | :---: | :---: | :---: |
| 0 | 0 | 0 | 0 |

```text
STRICTNESS: Mission-Critical
SCENARIO: General SaaS
DEDUCTIONS: unresolved defects = 0 (initial P1 and P2 fixed)
RAW SCORE: 100
CAP APPLIED: none
FINAL SCORE: 100 / 100
VERDICT: Pass within authority scope
NOT ASSESSED: none within this bounded review
```

初审独立运行证据（包含修复前失败，予以保留）：

```text
uv run --frozen pytest tests/test_web_memory.py tests/test_memory_authority.py tests/test_memory_jobs.py tests/test_memory_engine.py -o addopts='--import-mode=importlib' -q
94 passed in 5.06s

uv run --frozen pytest tests/test_m4_authority_review.py -o addopts='--import-mode=importlib' -q
1 failed in 0.38s
AssertionError: assert 'pending' == 'done'

uv run --frozen ruff check <four web authority files and two test files>
All checks passed!
uv run --frozen ruff format --check <same six files>
6 files already formatted
```

修复后独立复验：

```text
uv run --frozen pytest tests/test_web_memory.py tests/test_m4_authority_review.py tests/test_memory_authority.py tests/test_memory_jobs.py tests/test_memory_engine.py -o addopts='--import-mode=importlib' -q
102 passed in 6.62s

uv run --frozen pytest tests/test_web_memory.py tests/test_m4_authority_review.py -o addopts='--import-mode=importlib' -q
22 passed in 2.60s

uv run --frozen ruff check <same six files>
All checks passed!
uv run --frozen ruff format --check <same six files>
6 files already formatted
uv run --frozen mypy <four web authority source files>
Success: no issues found in 4 source files
```

第二组 22 项包含新增的独立第 101 条记忆可修改反例，以及满额来源重投仍明确显示 capacity_limited 的断言。测试文件新增断言后仅作 Ruff 格式调整。

全部测试使用临时 SQLite；未读取生产凭据、未调用外部模型。

## 2. 生产事故风险推演

> **场景 1 — 满额项目堵塞所有后续事件**
> **触发条件**：项目 A 有 10,000 个历史 memory_heads，且至少 20 条事件早于项目 B 的待处理事件。删除保留 head，因此这是正常使用可达到的容量状态。
> **失效机制**：全局扫描先 LIMIT 20，再对满额项目 continue；下一次永远选择同一批 20 条。
> **用户可见症状**：B 的事件已经接收，但始终 pending；后台 sweep 返回 ready，无法从状态判断队首阻塞。
> **爆炸半径**：全部项目的全局自动处理。
> **可检测性**：现有回执可见 pending，无超龄告警；独立回归已复现。

## 3. 详细审计明细（保留初审发现及关闭依据）

### P0 — 阻断性缺陷

none。

### P1 — 严重缺陷

#### M4-AUTH-01：满额项目导致全局队首永久阻塞

- **位置**：`src/agent_workbench/web_memory/store.py:162–179`，`process_pending_events`。
- **审查维度**：3 并发与资源安全、4 容错、11 事务完整性。
- **置信度**：[confirmed]。
- **问题分析**：原子事务保障不会重复创建草稿，但 LIMIT 前未排除不可处理记录，满额记录也没有终态。两次独立 sweep 均返回 0，健康项目仍 pending。
- **最小修复方案**：在当前事务内将达到永久容量限制的事件及 job 原子转为明确失败，保留固定原因 `memory_limit`；或在 LIMIT 前过滤不可处理项目并提供可见的阻塞原因。不可仅扩大批次或吞掉失败。

```diff
 if count >= 10000:
+    # Atomically persist an explicit terminal capacity failure for this
+    # event and its job, so subsequent sweeps can progress.
+    mark_event_capacity_failure(c, row)
     continue
```

- **关闭依据**：已在 LIMIT 前按项目容量过滤，批内仍复核容量；满额事件保留 pending 账本，回执与 overview 明确派生 `capacity_limited`，不会伪报完成。独立反例现在通过；复投没有新草稿且保持容量受限状态。无须改变 0013 的既有状态枚举。

### P2 — 主要缺陷

#### M4-AUTH-02：管理集合被静默截断，缺少可访问的后续页

- **位置**：`src/agent_workbench/web_memory/store.py:211–290`，`overview`。
- **审查维度**：5 性能与分页、8 契约表达。
- **置信度**：[confirmed]。
- **问题分析**：连接、事件、授权、策略、作业、记忆均最多返回 100 条，接口没有总数、has_more 或分页。允许超过 100 条数据时，网页无法发现或管理后面的记录；按字典序截断的来源甚至可能让新建来源不出现。公开写入上限与可管理范围不匹配。
- **建议修复**：为可变更集合提供有界分页或按 ID 查询入口并显示明确截断状态；历史集合可先提供游标/数量及范围说明。不得把截断当空集合或全量统计。若暂缓，必须记录产品范围与显式 UI 提示，不能静默忽略。
- **关闭依据**：项目与 overview 六集合分别接受有界页码，LIMIT 101 判定 has_more/next_page；额外字段、重复/负数/超限页码拒绝。精确 memory context/source grants 端点避免把分页缺失误作无授权或策略版本零。作者 101 项全集合测试及独立第 101 条记忆发现、确认 CAS 修改通过。前端是否提供对应翻页交互由 UI 评审核验。

### P3 — 次要缺陷

none。

## 4. 维度覆盖声明

下表发现数保留初审分类，所有列出的发现均已关闭，当前未解决数为 0。

| # | 维度 | 发现数 | 备注 |
|---|---|---|---|
| 1 | 架构与边界 | 0 | 外部 Bearer 写入与本机管理写入分离，事件不授予外发权限 |
| 2 | SOLID 与现代设计 | 0 | HTTP 解析、领域事务、生命周期职责分离 |
| 3 | 并发、异步与资源安全 | 1 | M4-AUTH-01；请求取消后 shield 保留槽位；后台事务串行化 |
| 4 | 容错与防御性编程 | 1 | M4-AUTH-01；其余异常映射安全错误码，JSON 有大小/时间/深度边界 |
| 5 | 性能、伸缩性与计算效率 | 1 | M4-AUTH-02；读取和处理有界，但返回范围需明示 |
| 6 | 安全与数据合规 | 0 | 凭据随机生成、仅摘要持久化、常量时间比较；轮换和撤销 CAS；本机 Host/Origin 边界 |
| 7 | 可观测性与运维友好度 | 0 | 接收与处理分离；容量问题的可观测不足已计入 M4-AUTH-01 |
| 8 | 类型安全与契约表达 | 1 | M4-AUTH-02；输入严格 schema、拒绝额外字段与原值错误回显 |
| 9 | 可测试性与死代码 | 0 | 真 SQLite/API、并发、取消、回滚、授权、负向输入及独立容量反例 |
| 10 | 工程规范与一致性 | 0 | 锁定 Ruff 检查和格式通过 |
| 11 | 数据与事务完整性 | 1 | M4-AUTH-01；其余事件/outbox、草稿/回执同事务、唯一键与 CAS；非空降级拒绝 |
| 12 | 依赖与供应链治理 | 0 | 本批权威层无新增依赖；不重评另外批次的 Cognee 供应链 |

## 5. 整改清单

| ID | 严重级 | 负责人 | 截止 | 状态 |
|---|---|---|---|---|
| M4-AUTH-01 | P1 | 实现 Agent `/root`、`m4_backend` | M4-R 验收前 | fixed / 独立复验通过 |
| M4-AUTH-02 | P2 | 实现 Agent `/root`、`m4_backend` | M4-R 验收前 | fixed / API 独立复验通过 |

## 6. 架构防腐与长效治理建议

1. 保留跨项目容量反例作为 CI 门禁；成本一项临时库测试，防止有界批处理退化为永久队首阻塞。
2. 管理集合统一定义分页和截断字段；成本少量 API/UI 契约测试，防止以后新增集合再次静默丢失可管理记录。

## 7. 审计声明

- 本报告仅针对 `10b1d8b` 之上的上述工作区增量，已包含本次 API/store 分页与容量修复复审；后续生产代码修改需增量复审。
- 本批范围内未评估：none；完整 M4-R 还需要另外批次的引擎、Hook 和前端验收。
- 已施加豁免：none。
- 所列缺陷均 confirmed；无猜测性故障作为门禁依据。
