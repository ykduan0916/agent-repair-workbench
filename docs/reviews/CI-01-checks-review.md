# 企业代码质量审计报告：CI-01 Checks 修复

- **审计对象**：`.github/workflows/checks.yml`、`frontend/tests/console-evidence.spec.ts`、`frontend/tests/console-review.spec.ts`、`frontend/tests/evidence-independent.spec.ts`、`frontend/tests/memory-boundaries.spec.ts`、`frontend/lib/documentation.ts` 的指南顺序修复、`docs/51-ci-checks-repair.md` 与台账的 CI-01 条目。
- **审计版本**：`cf919862912e87488bee9adc60469d0b44a5c672` 上的 CI-01 工作区增量；独立于实现者的专职评审。
- **技术栈**：GitHub Actions、uv/Python 3.12、Node、TypeScript、Playwright。
- **业务场景**：General SaaS。
- **审查严苛度**：Mission-Critical。
- **审计时间**：2026-10-21。

## 1. 代码质量评估卡

- **综合质量得分**：100 / 100，仅针对此次有限增量的已发现缺陷计分，不代表整个仓库无缺陷。
- **代码审计结论**：Pass。补丁没有通过跳过测试、增加重试或吞掉未知请求来制造绿灯。
- **审计范围**：6 个执行/测试/导航文件，约 33 行增删；附带检查 CI-01 文档及台账。已有 M5 工作区变更（包括文档索引新增 M5 项）不属于本次审计。
- **范围内未评估项**：none。
- **交付边界**：远端 Ubuntu 对本补丁的实际 Checks 尚未运行，不属于此次已完成的本地代码审计证据。最终远端验收仍须在授权推送后核对同一提交 SHA；不得由本报告推断 GitHub 已变绿。

| P0 阻断 | P1 严重 | P2 主要 | P3 次要 |
| :---: | :---: | :---: | :---: |
| 0 | 0 | 0 | 0 |

```text
STRICTNESS:   Mission-Critical
SCENARIO:     General SaaS
DEDUCTIONS:   0 → Σ = 0
RAW SCORE:    100
CAP APPLIED:  none
FINAL SCORE:  100 / 100
VERDICT:      Pass — 本次增量无未关闭的确认缺陷
NOT ASSESSED: none within code-review scope
```

## 2. 生产事故风险推演

本次补丁没有新增 P0/P1 发现。原故障路径及后续全量测试暴露的问题已核对：

1. 全新 CI Python job 有 Node、没有 `frontend/node_modules/typescript`，配置契约测试执行 `require('typescript')` 时退出。新增固定版本 Node 与 `npm ci` 补足测试的真实依赖；保留原测试。
2. 任务详情加载发起 `GET /api/analysis-runs/{id}/memory`，旧测试替身把无请求体的 GET 当任务创建，随后访问空对象的 `request_id`。新增精确 GET 分支不会改变创建次数；落入创建分支的请求仍必须满足 POST 与精确路径。
3. 最新远端 27 项失败实际为 26 项旧替身错误和 1 项文档导航顺序回归。恢复快速上手第一项，保留记忆指南，未修改原排序断言。
4. 首次本地全量中，确认经验后异步刷新会按新 version 重挂载编辑器；在旧编辑器展开后，新编辑器恢复折叠，测试后续找不到可见的索引按钮。三处相同操作序列均等待“确认经验”按钮消失后再展开新编辑器；等待的是实际状态，不增加固定睡眠或测试重试。复审曾指出第三处同类时序遗漏，现已补齐并核对。
5. 新增 CI 说明文档未进入当时已启动的生产构建，交叉链接检查发现 `/docs/51-ci-checks-repair` 返回 404。重建交付版本后重新验证，而非删除链接检查。

爆炸半径为 CI 门禁、测试替身与文档导航；没有修改生产运行、模型、授权或记忆存储行为。

## 3. 详细审计明细

### P0 — 阻断性缺陷

none。

### P1 — 严重缺陷

none。

### P2 — 主要缺陷

none。

### P3 — 次要缺陷

none。

### 证据与复核

- 已核对 `artifacts/ci-diagnosis/latest.log`：远端 `27 failed`、`211 passed (5.1m)`；Python `1 failed, 1867 passed, 69 skipped in 466.04s (0:07:46)`，包括 `Cannot find module 'typescript'`。
- 读过实际调用方 `TaskMemoryUsage` 和后端 `web_memory/agent_api.py`；替身返回的 `{state: "disabled", references: [], uses: []}` 与未启用记忆时的真实协议相同。它仅用于这些非记忆场景，不能证明真实记忆检索成功。
- 检查 diff：未删除证据关联、请求幂等、取消、可访问性断言；未知 GET/未知创建路径仍明确失败。未增加 `continue-on-error`、过滤测试、重试或宽泛成功兜底。
- 专职评审独立运行 `uv run --frozen pytest tests/test_b5_live_gate.py -q`：24 项通过，退出码 0；同一命令链的 `git diff --check` 通过。
- 阅读实现者产物 `artifacts/ci-diagnosis/frontend-focused.log`：`29 passed (26.0s)`，覆盖三个修改的测试文件。
- 已阅读首次完整结果 `artifacts/ci-diagnosis/frontend-full.log`：235 passed / 3 failed / 15 skipped。三项失败如上保留，并对应修复和重建；不将该轮写成成功。
- 已独立阅读 `artifacts/ci-diagnosis/frontend-final.log` 的修复后完整结果：`15 skipped`、`238 passed (3.5m)`，全量用例数 253；未添加 skip，未放宽原断言。此次为三个实际问题修复及重建后的复验，不是未改代码的盲目重试。
- 实现者同步最终生产 build、TypeScript、全仓 Ruff lint/512 文件格式检查通过，API health 与站内文档 HTTP 200；评审再次执行 `git diff --check` 通过。仍须在获准推送后核验远端结果。

## 4. 维度覆盖声明

| # | 维度 | 发现数 | 备注 |
|---|---|---|---|
| 1 | 架构与边界 | 0 | 仅补测试依赖与替身路由，不改生产边界 |
| 2 | SOLID 与现代设计 | 0 | 无新增业务抽象或共享模块耦合 |
| 3 | 并发、异步与资源安全 | 0 | 替身状态逐测试隔离；三处索引操作等待确认刷新完成后继续 |
| 4 | 容错与防御性编程 | 0 | 未知请求断言失败，未使用 catch 吞错 |
| 5 | 性能、伸缩性与计算效率 | 0 | 新增 npm 锁定安装使用既有缓存方式；作业超时仍有界 |
| 6 | 安全与数据合规 | 0 | 不引入密钥、外发或写权限；checkout 仍不持久化凭据 |
| 7 | 可观测性与运维友好度 | 0 | 文档保留原失败证据并区分本地与远端状态 |
| 8 | 类型安全与契约表达 | 0 | GET disabled 与实际 schema 一致，创建方法/路径显式约束 |
| 9 | 可测试性与死代码 | 0 | 受影响原用例保留；补依赖而非跳过契约测试 |
| 10 | 工程规范与一致性 | 0 | Node、setup action 与既有 product-ui job 一致 |
| 11 | 数据与事务完整性 | 0 | 不涉及生产事务；读取不再污染测试 starts 写入计数 |
| 12 | 依赖与供应链治理 | 0 | actions 固定 SHA，npm ci 使用既有锁文件，无依赖版本变更 |

## 5. 整改清单

无未关闭的 P0/P1/P2/P3。复审指出的第三处索引展开时序遗漏已补齐；文档 27 项失败的归因已纠正为 26+1。本次本地修复通过审查不关闭远端 Checks 验收事项。

## 6. 架构防腐与长效治理建议

1. **按当前提交验收远端**：推送后记录 SHA、两个 job 的结论与失败日志。成本一次 CI 检查，防止把 push 成功、本地通过或旧提交绿灯当成当前门禁。
2. **测试替身按 HTTP 契约分派**：新增 API 时明确方法与路径，未知请求保留失败。成本为对应测试替身的小范围维护，防止读取请求被误识别为写入。
3. **显式声明跨语言测试依赖**：Python 测试若执行 TypeScript 配置，干净 job 必须安装 Node 与锁定前端依赖。此次已落实，避免仅在开发机 node_modules 存在时通过。

## 7. 审计声明

- 已审计基线 `cf919862912e87488bee9adc60469d0b44a5c672` 及上述 CI-01 增量；执行逻辑继续变更时须复审。
- 未评估：范围外的 M5 实验、供应商可用性、真实模型效果与远端本补丁执行结果；本报告不为这些内容背书。
- 已施加豁免：none。
- 置信度：未保留 suspected 缺陷；所述失败机制由代码及原始 CI 日志交叉核对。
