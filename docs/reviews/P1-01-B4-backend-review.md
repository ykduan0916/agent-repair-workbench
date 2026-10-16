# 企业代码质量审计报告：P1-01 B4 后端

- **审计对象**: B4 审批存储、追加式账本、HTTP 边界、并发控制、迁移与定向测试
- **审计版本**: B4 交付冻结工作树（已由本轮 B4 提交收录）
- **技术栈**: Python 3.12、FastAPI、SQLAlchemy Core、Alembic、SQLite、Pydantic
- **业务场景**: General SaaS（本地可信单用户审批）
- **审查严苛度**: Mission-Critical，范围限定为 B4
- **审计时间**: 2026-10-16 Asia/Shanghai

## 1. 代码质量评估卡

- **综合质量得分**: **100 / 100** — 🟢 健康
- **上线评估结论**: `✅ Pass / 准予 B4 合并`
- **审计范围**: B4 后端存储、迁移、HTTP 路由、API/并发测试及其直接调用方
- **范围外**: B5 真实模型、真实 Docker、生产常驻服务整体验收；这些不由本报告替代

| P0 阻断 | P1 严重 | P2 主要 | P3 次要 |
| :---: | :---: | :---: | :---: |
| 0 | 0 | 0 | 0 |

```text
STRICTNESS:   Mission-Critical
SCENARIO:     General SaaS
DEDUCTIONS:   none  →  Σ = 0
RAW SCORE:    100
CAP APPLIED:  none
FINAL SCORE:  100 / 100  (🟢 Healthy)
VERDICT:      ✅ Pass  — B4 backend scope has no open P0/P1/P2/P3 findings
NOT ASSESSED: B5 real model, Docker and resident-service end-to-end gates
```

## 2. 修复后的关键不变量

1. `0010_decision_events` 保存每个 request receipt；`(task_id, request_id)` 唯一保证历史 request 在后续决定后仍可幂等重放。
2. 事件账本按 `(task_id, revision)` 唯一排序，receipt revision 是任务级 CAS 值；候选 head 的 revision 只用于候选历史显示。
3. 接受操作在同一任务锁和事务内核对其他候选的当前证据，只允许一个仍有效的 accepted 候选；证据失效的历史接受不阻塞新的有效候选。
4. 审批/读取/下载都经过有界信号量；超出四个并发工作返回稳定 `approval_busy`，不会无限占用默认线程池。
5. GET receipt 返回 `current_valid`、`can_accept`、`can_reject` 与 `can_download`；证据变化会撤回下载权限，前端显示历史决定并要求重新核对。
6. HTTP 层先校验 loopback Origin、host、Content-Type、UUID、查询参数和严格 body；下载只生成服务端命名的精确 diff，不接受路径、不写回项目。
7. 迁移从 0009 到 0010 时补齐历史 payload 的 `candidate_id`、建立 head 唯一索引，并在旧 schema/非空降级场景下 fail closed。

## 3. 验证证据

定向后端门禁：

```text
uv run pytest -q tests/test_b4_api.py tests/test_b4_concurrency.py \
  tests/test_b4_decisions.py tests/test_web_repair_migration.py
15 passed
```

覆盖包括接受/拒绝、精确下载、版本篡改、旧 request 在后续 revision 后幂等、尾部历史、同 revision 并发 CAS、task 级有效候选唯一性、approval worker 上限、Origin/host/Content-Type/UUID/query/extra body 以及 0009→0010 迁移保真。

静态门禁：Ruff check、Ruff format、mypy、`git diff --check` 通过。无新增运行依赖。

## 4. 生产事故风险复核

本轮复核未发现仍可达的 P0/P1/P2/P3。此前初审提出的四项 P1 已分别通过追加式账本、task 级 CAS/有效候选核对、有界审批 worker 和 receipt 当前有效性字段关闭。B5 的真实模型/Docker 证据仍需单独按 P1-01 规格验收，不能从本报告推导。

## 5. 维度覆盖声明

| # | 维度 | 发现数 | 备注 |
|---|---|---:|---|
| 1 | 架构与边界 | 0 | 审批不写回用户项目 |
| 2 | SOLID 与现代设计 | 0 | 存储、API、前端契约分离 |
| 3 | 并发、异步与资源安全 | 0 | task fence、CAS、有界 worker 已覆盖 |
| 4 | 容错与防御性编程 | 0 | stale、冲突、错误映射有测试 |
| 5 | 性能、伸缩性与计算效率 | 0 | 审批并发上限明确 |
| 6 | 安全与数据合规 | 0 | loopback 边界、无路径输入、无密钥输出 |
| 7 | 可观测性与运维友好度 | 0 | 稳定错误码和 no-store 响应 |
| 8 | 类型安全与契约表达 | 0 | Pydantic/Zod 严格契约同步 |
| 9 | 可测试性与死代码 | 0 | 15 项定向测试含独立边界/并发用例 |
| 10 | 工程规范与一致性 | 0 | 迁移、文档、前端状态同步 |
| 11 | 数据与事务完整性 | 0 | append-only ledger、唯一约束、事务 CAS |
| 12 | 依赖与供应链治理 | 0 | 无新增依赖 |

## 6. 整改清单

none。B5 另有独立交付门禁，不属于 B4 后端整改项。

## 7. 审计声明

- 已审计版本：B4 交付冻结工作树；任何后续生产代码变更都需要增量复审。
- B4 结论仅覆盖本报告列明的后端范围，不能替代 B5 真实模型/Docker/网页审批整体验收。
- 无风险豁免，无未关闭 Defect P0/P1/P2/P3。
