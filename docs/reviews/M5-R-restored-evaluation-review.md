# M5-R 服务恢复后同版本配对评估：独立评审

- 审计对象：`ops/m5r_eval.py` schema 2 增量、docs 44/50/53 的协议与结果、完整真实 study 及权威账本。
- 审计时间：2026-10-21。
- 审计者：专职 Agent `embedding_failure_review`；未修改生产代码、模型配置或评估脚本，未新增 provider 调用。
- 场景：Data Pipeline；严苛度：Mission-Critical。
- study：`2da5b4b79d8645268ef71a33393696b8`。
- plan SHA256：`f36cedb8c9440f6090a4f05991db50216ced1766bd565431498d4336293fd017`。

## 1. 代码质量评估卡与结论

**Pass：本轮同版本、人工提示消融的执行与结果记录通过复核。M5-R 与 P1-02 父任务继续 in_progress。**

无留存 P0/P1/P2/P3 发现。通过的是本轮评估协议、真实执行、证据及统计口径，不是“记忆普遍提高修复能力”的假设。

```text
STRICTNESS: Mission-Critical
SCENARIO: Data Pipeline
DEDUCTIONS: none in this evaluation scope
RAW SCORE: 100
CAP APPLIED: none
FINAL SCORE: 100 / 100 within the stated scope
VERDICT: Pass for this same-version substudy
NOT ASSESSED: cross-version and independent-tool effectiveness, outside this substudy
```

## 2. 协议与故障风险复核

- 开始前有六次单条/双条交替 preflight，均 HTTP 200、向量契约与 usage 有效；144 tokens 单列。它只满足启动门槛，不是长期稳定性保证。
- 12 个唯一任务 ID，两个案例 × none/correct/wrong × 两次；三组各四个计划单元，第二块逆序，预算、模型配置、oracle、镜像均冻结。
- schema 2 明确区分整任务/索引零重放与 REL-03 指定 embedding 请求最多三次。`bill()` 仍将已恢复但未知的费用算作 unknown；本轮实际账本没有 unknown。
- 四份索引分别建立，按计划复用两次；经验为人工编写的同版本契约建议，不是从历史修复中独立学习所得。正确建议不含完整补丁或隐藏断言，但任务简单，须承认成功率天花板。
- 全部旧失败与费用保留，本轮没有混入前三轮的成功任务，也没有删除失败后补跑单元。

可能误导结论的三类风险均已在文档中约束：只算 Agent tokens 而漏掉索引/检索；将全部通过解释为记忆提升；将一次可执行链路解释为跨项目泛化。

## 3. 独立结果核对

评审者重新读取 12 个任务权威记录，使用当前 `RepairReader.detail` 核验冻结比较，并重新执行只读恢复检查。不是仅相信 summary.json 的布尔值。

| 项目 | 无记忆 | 正确建议 | 错误建议 |
|---|---:|---:|---:|
| 当前冻结比较通过 / 计划 | 4 / 4 | 4 / 4 | 4 / 4 |
| 实际经验引用 | 0 / 4 | 4 / 4 | 4 / 4 |
| 回归 | 0 | 0 | 0 |
| Agent tokens | 141,064 | 125,833 | 148,443 |
| 索引及检索 tokens | 0 | 13,653 | 15,632 |
| 全部 tokens | 141,064 | 139,486 | 164,075 |
| Agent 模型调用 | 24 | 21 | 24 |
| 工具调用 | 24 | 20 | 23 |
| 单任务中位耗时，秒 | 21.24 | 26.54 | 27.01 |

耗时包含任务内检索，不含一次性索引准备。总计 **444,625 tokens**，preflight 144 tokens 另列。金额缺少计价依据，仍为 unknown。

### 权威数据与资源

- plan 摘要与当前代码摘要一致；重新计算 summary 与原 summary 完全相等。
- 从六个 SQLite 状态库独立汇总 memory_calls：正确组 26 条、错误组 20 条，合计 46 条；全部 received 且 actual_tokens 非空，没有失败恢复记录被误算作已知费用。
- 所有 repair_tasks 的 payload 摘要有效，任务用量与逐单元 JSON 一致；数据库独立汇总的各组 Agent tokens 和记忆 tokens 与上表相等。
- 八个记忆任务的 diagnosis、repair 均有实际引用；全部 verification 的引用为零。使用引用的 memory head 状态、修订、generation 和 digest 与权威库一致。
- 12 个任务的当前冻结比较均通过，预算/镜像/oracle/请求审计不变量成立。
- 60 个精确执行资源当前均 absent；六个状态库无 leased 记忆作业，四个实际引擎目录 writer 锁可获得。
- 独立证据：`artifacts/m5r/2da5b4b79d8645268ef71a33393696b8/independent-review.json`。本地产物不入 Git。

## 4. 结论算术与解释

- 正确组 Agent tokens 合计相对无记忆低约 **10.8%**；计入索引及检索后只低约 **1.1%**。四个相同案例/重复号的配对中，两次更少、两次更多。
- 正确组每个配对的任务耗时均比对应无记忆更长，中位数也更长。因此不能只展示 token 较少而声称整体效率提升。
- 错误组全部 tokens 高约 **16.3%**。全部冻结通过支持这次运行没有绕过验收，不足以证明错误记忆无害。
- 三组成功率都是 4/4，没有证据表明正确记忆提高成功率。样本小、只含两个合成案例且存在服务端采样差异，不支持显著性、商业收益或一般因果结论。
- docs 50 的结果表、四对 Agent tokens、百分比和耗时口径与独立核对一致。

## 5. 十二维度覆盖

| 维度 | 结论 |
|---|---|
| 架构与边界 | 仍调用生产 Controller、Cognee 和真实 Docker；未新增产品执行权限 |
| SOLID 与设计 | 仅评估计划 schema/策略声明增量，无平台扩张 |
| 并发与资源 | 顺序冻结执行、精确回收、逐库 owner/锁核对 |
| 容错 | setup、unknown、未执行保留原分母；整任务不得重放 |
| 性能 | 模型/工具/候选/time/token 预算一致，准备费用单列 |
| 安全与合规 | 合成授权输入；无密钥或原始 provider 异常导出 |
| 可观测性 | 逐单元、索引、检索、引用与费用可追溯 |
| 类型与契约 | REL-03 请求策略在 plan 明示；旧 unknown 口径未放宽 |
| 可测试性 | 计划、未知费用、失败分母、配置漂移及角色边界确定性测试 |
| 工程规范 | 独立新 study、不可覆盖任务记录、源代码冻结 |
| 数据与事务 | 数据库与 JSON 用量相符、索引一次计费、旧失败不删除 |
| 依赖治理 | 沿用已验收 SDK/模型/镜像，无新模型下载 |

## 6. 验证与整改清单

评审者执行：

```bash
uv run --frozen pytest tests/test_m5r_eval.py \
  -o addopts='--import-mode=importlib' -q
```

结果：**10 passed**。评审只读脚本的首次 summary 比较因自行按文件名排序导致索引数组顺序与协议不同，随后按原协议 CASES/ARMS 顺序复核完全一致；没有修改实验结果或被审计源码。

| 项目 | 状态 |
|---|---|
| 本轮同版本执行及结果复核 | 通过 |
| 有来源的跨版本相关/不相关经验 | 尚未执行，按 docs 44 的后续门禁 |
| 开发中未用作 fixture 的独立工具项目 | 尚未执行，不可用本轮重复任务替代 |
| M5-R / P1-02 总体 | 保持 in_progress |

## 7. 审计声明

本报告仅验收该冻结 study。允许完成本轮同版本子评估；不允许据此把整个 M5-R 或 P1-02 标为 done。评审未新增模型调用、未修改生产代码；上游内部实现及长时间稳定性不在本轮证据范围内。
