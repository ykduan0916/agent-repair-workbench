# 企业代码质量审计报告：REL-01 / REL-02

- **审计对象**：修复模型拒绝分类、最多一次格式纠正、请求结算与前端错误契约；不包含此前 P1-02 记忆设计或文档阅读器排版改动。
- **审计版本**：基于 `67b50d056213c00c1350c81c47de779d05b20866` 的本轮工作区增量。
- **技术栈**：Python 3.12、Pydantic、httpx、LangGraph、SQLite/SQLAlchemy；Next.js、TypeScript、Zod。
- **业务场景**：General SaaS，本地可信单用户、两个固定合成案例。
- **审查严苛度**：Mission-Critical。
- **审计时间**：2026-10-19。
- **评审者**：专职 `rel_review`，独立于实现者；未修改实现、未读取私密模型配置、未运行真实模型。

## 1. 代码质量评估卡

- **综合质量得分**：**100 / 100** — 🟢 健康（仅表示已审范围未发现未解决缺陷，不表示模型准确率或全产品成熟度）。
- **上线评估结论**：**✅ Pass — 代码评审与本轮证据复审通过**。已核对实际模型、Docker 与生产页面验收，明细见下；本结论不表示真实模型格式纠正成功率已经得到统计验证。
- **审计范围**：模型网关、共享严格解码器、修复控制器、持久账本、网页 policy 指纹、前端错误枚举与文案、新增恢复测试、mypy TypeForm 检查选项及既有直接调用方；增量低于 2,000 行。
- **未评估项**：审计范围内 none；范围外的模型总体成功率、第三方服务 SLA、P1-02 记忆实现不作判断。

| P0 阻断 | P1 严重 | P2 主要 | P3 次要 |
| :---: | :---: | :---: | :---: |
| 0 | 0 | 0 | 0 |

```text
STRICTNESS: Mission-Critical
SCENARIO: General SaaS
DEDUCTIONS: none -> 0
RAW SCORE: 100
CAP APPLIED: none
FINAL SCORE: 100 / 100
VERDICT: Pass — 已发现边界问题均在本轮修复后复审
NOT ASSESSED: none within the declared code scope
```

独立执行的最终定向验证：

```text
uv run --frozen pytest tests/agents/test_repair_protocol_recovery.py tests/agents/test_repair_controller.py tests/test_repair_store.py tests/test_repair_store_review.py tests/agents/test_repair_gateway.py tests/agents/test_repair_gateway_review.py tests/agents/test_gateway.py tests/agents/test_reviewer_gateway.py -o addopts='--import-mode=importlib' -q
151 passed in 29.83s

uv run --frozen ruff check .
All checks passed!
uv run --frozen ruff format --check .
424 files already formatted

cd frontend && npm run typecheck
> tsc --noEmit
exit 0

uv run --frozen mypy src/agent_workbench
Success: no issues found in 139 source files
```

过程记录：首次定向 51 项通过；网关分类修改后旧重复键断言出现一次失败（85 passed/1 failed），随后一次运行与实现编辑重叠，旧测试已被 Python 加载（143 passed/1 failed）。不把混合快照当验收；最终重新执行上述完整定向集，151 项全部通过。

### 真实门禁增量复审

已独立核对 `artifacts/b5-live/03d4c80d-7695-4fd7-9f26-b9c1f68c4f41/report.json` 及 `browser-results.json`：两个正常案例均为 `candidate_verified`，各 5 次模型、5 次工具调用，四槽冻结比较完整，10 个容器均 absent。停止任务为 `outcome_unknown / MODEL_CANCELLED`，1 次模型、0 次工具，预留 1,056,768 tokens 保留，三个任务均无 active/pending。

首轮门禁仍记为 **failed**：浏览器旧断言期待 `MODEL_USAGE_UNKNOWN`，实际为本次新增保留的具体原因 `MODEL_CANCELLED`；不是可忽略的成功记录。实现者仅更新 `frontend/tests/b5-real-repair.spec.ts` 两处错误码断言及 `ops/b5_live_gate.py` 一处匹配，未知用量、预留不退款、调用数不增加、容器清理等断言全部保留。此变更符合新契约，不构成降低安全门禁。

独立运行门禁辅助测试：`uv run --frozen pytest tests/test_b5_live_gate.py -o addopts='--import-mode=importlib' -q` → `24 passed in 1.47s`。已复审 [43 实施说明](../43-rel-01-02-model-recovery.md) 的范围、隐私、恢复条件与测试矩阵，未发现与代码不符的承诺。

### 最终交付证据复审

- 新目录 `artifacts/b5-live/a10bf8c8-56e7-4858-8245-15236c188809/` 独立保留，未覆盖首轮。`report.json` 的 `status` 与 `artifact_cross_check` 均为 `passed`；浏览器记录 `3 passed (45.8s)`，包含双案例真实运行、刷新、四槽冻结验证、人工决定、准确补丁下载/撤回，以及真实模型挂起时停止。
- 两个正常案例分别为 5/7 次模型调用、5/7 次工具调用，均 `candidate_verified`；10 个实际容器全部 absent，所有比较槽 cleanup 为 removed。停止任务 1 次模型、0 次工具，`outcome_unknown / MODEL_CANCELLED`，未知预留 1,056,768 tokens 保持；三任务 active=false、pending_operation=null。
- 已读取 `artifacts/rel-01-02-pytest.log`：`1623 passed, 67 skipped, 1 warning in 232.00s (0:03:52)`。唯一警告来自原有 fork 测试的多线程 fork DeprecationWarning；67 个显式环境门禁 skipped 不算通过。
- 完整测试之后只新增 `test_correction_does_not_extend_original_deadline`：第一次错误回复结算后将单调时钟推进至原 600 秒之外，断言只有一次模型调用、没有工具动作、最终 `DEADLINE_EXCEEDED` 且最初 schema 错误仍在。独立重新运行整个恢复专项：`35 passed in 14.35s`。没有以新增用例为理由修改运行时逻辑，也没有把它算进前述 1623 项。
- 已读取 `artifacts/rel-01-02-browser.log`：`42 passed (29.2s)`，涵盖修复页面与站内文档，包括六种新错误的严格 DTO 解析、中文展示及不出现接受按钮。
- 真实案例证明当前网关与执行闭环无回归；格式错误恢复由确定性故障注入验证。没有将本次自然运行的成功解读为格式纠正机制已经提升真实故障恢复率。

## 2. 生产事故风险推演

当前未解决 P0/P1：none。复审确认以下原风险已消除：

1. 新静态错误码到达网页时，旧 Zod enum 会拒绝整条任务记录，用户只看到读取失败。`frontend/lib/repairs.ts:40` 起及 `frontend/lib/analyses.ts` 已补齐错误契约，六种错误都有中文说明与浏览器用例。
2. 空证据交接曾落入一般 schema 错误而可能触发纠正。`repair_contracts/common.py` 使用固定 `handoff_evidence_required` 错误，`orchestration/codec.py:68` 只识别静态错误类型并归为 `MODEL_EVIDENCE_REQUIRED`；不重试语义上的缺失证据。
3. 深度超限曾可能落入可纠正 JSON 错误。`orchestration/codec.py:54` 与 `model_gateway/gateway.py:191` 分别限制决策和封装深度/大小，归为不可纠正 `MODEL_DECISION_LIMIT`。

## 3. 详细审计明细

### 🚨 P0 — 阻断性缺陷

none。

### ⚠️ P1 — 严重缺陷

none open。初审的网页契约、缺失证据纠正边界已修复，见第 2 节与整改清单。

### ⚡ P2 — 主要缺陷

none open。真实 HTTP 决策参数此前把语法错误并入封装错误；现在 HTTP MockTransport + 实际适配器 + 控制器 + SQLite 的组合用例覆盖 JSON、wrapper、schema 与超限分类，避免只测试人为构造的 `Reply`。

### 💡 P3 — 次要缺陷

none。

### 关键不变量复核

- `RepairController._model` 在任何本回复工具动作前进行严格解码与交接检查，再通过现有 CAS/claim 结算；只有成功结算后的 ready 状态才能考虑纠正。
- `CORRECTABLE` 仅包含决策封装、JSON、角色格式三类；超时、取消、未知用量、截断、超限、证据不足/不匹配及错误交接不进入格式纠正。
- 一次纠正是**每任务共享**，不会在切换角色后重置。控制器为一次性执行，不支持从持久状态恢复后再次给纠正额度。
- 纠正重新走 `reserve_model`，使用原调用数、tokens、输出、截止时间预算；持久化确认丢失、取消、预算不足时停止，不重复发送未知请求。
- 纠正提示仅带宿主静态代码，保留宿主 `completed_reads`、执行进度与已完成材料，不回显非法模型原文。已有 T05 后发生格式错误的用例仍只执行一次复现，后续四槽冻结回归不变。
- 失败请求保留 failed/outcome_unknown 及已知用量；未知用量保留预留。不把失败改为成功，也不由角色结论设置 candidate_verified。
- 协议修订与纠正上限进入 adapter/policy 摘要。新代码沿用既有表，不新增迁移；旧错误码仍可读取。
- 本轮保存请求摘要、角色、模型配置摘要和静态错误码；**没有新增完整回复/决策摘要字段**。不要声称可以凭这些信息还原旧 B5 失败的具体原文或字段值。

## 4. 维度覆盖声明

| # | 维度 | 未解决发现 | 备注 |
|---|---|---|---|
| 1 | 架构与边界 | none | 模型、角色、执行器和账本职责保留；无新 Agent 或权限 |
| 2 | SOLID 与现代设计 | none | 静态拒绝策略集中，诊断解码显式可选，旧分析链兼容 |
| 3 | 并发、异步与资源安全 | none | 原 fence/CAS/owned cancellation；纠正前重新检查取消、期限和预算 |
| 4 | 容错与防御性编程 | none | 单次纠正，错误白名单，超限/未知不重试 |
| 5 | 性能、伸缩性与计算效率 | none | 最多额外一次原预算内请求；JSON 大小深度有界，无新增无界查询 |
| 6 | 安全与数据合规 | none | 无原始拒绝内容回显或持久化；schema、角色与冻结 oracle 未放宽 |
| 7 | 可观测性与运维友好度 | none | 拒绝原因存现有 request/event，前端可读；拒绝不被成功覆盖 |
| 8 | 类型安全与契约表达 | none | Python ErrorCode 与两处 Zod 枚举一致；启用已锁定 mypy 的 TypeForm 支持，保持 strict，无新增 ignore；四角色原契约未改 |
| 9 | 可测试性与死代码 | none | 实际账本、HTTP decoder、单/多角色、恢复与停止负向测试 |
| 10 | 工程规范与一致性 | none | 锁定 Ruff 与 TypeScript 检查通过；用户文案不暴露里程碑编号 |
| 11 | 数据与事务完整性 | none | 继续使用原 reserve/settle 事务；确认丢失不重发；无表结构变更 |
| 12 | 依赖与供应链治理 | none | 无新增依赖/锁文件变更；Pydantic core 与 typing_extensions 已锁定 |

## 5. 整改清单

| ID | 严重级 | 负责人 | 截止 | 状态 |
|---|---|---|---|---|
| R1 网页新错误枚举不匹配 | Defect P1 | 实现者 Codex | 本轮交付前 | fixed / re-reviewed |
| R2 空证据交接误入格式纠正 | Defect P1 | 实现者 Codex | 本轮交付前 | fixed / re-reviewed |
| R3 深度上限分类不一致 | Defect P1 | 实现者 Codex | 本轮交付前 | fixed / re-reviewed |
| R4 真实 HTTP JSON 分类过粗 | Defect P2 | 实现者 Codex | 本轮交付前 | fixed / re-reviewed |

以上均不计入最终未解决缺陷分数。没有延期项或风险豁免。

## 6. 架构防腐与长效治理建议

1. 继续用真实 decoder→控制器→账本测试覆盖新错误码；每新增一种码同时补网页解析/文案，成本为一个参数化用例，防止后端更精确但前端整页无法读取。
2. 若以后加入运行恢复，不能复用当前进程内纠正计数；必须先将纠正额度与 admission 纳入持久化事务及恢复测试。当前一次性控制器已经使该风险不可达。
3. 后续分析收益时分开报告“未触发纠正”“纠正恢复”“纠正后仍失败”，保留原请求消耗；不能把合成注入成功当成真实供应商协议失败概率降低的证据。

## 7. 审计声明

- 已审计基线为 `67b50d0` 及本轮 REL 增量；其后的运行逻辑变更需增量复审。
- 范围外：供应商总体可靠性与新记忆系统实现；本报告不对其背书。
- 已施加豁免：none。
- 置信度：记录的整改项均为 `[confirmed]`，通过源码路径和定向测试复核，无未解决 `[suspected]` 项。
