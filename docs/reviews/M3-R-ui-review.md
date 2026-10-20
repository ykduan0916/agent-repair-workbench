# M3-R Agent 经验取用界面独立评审

- **审计对象**：`frontend/components/memory/task-memory-choice.tsx`、`task-memory-usage.tsx`、`save-repair-memory.tsx`；诊断与修复运行/审批接入、`memory-workspace.tsx` 来源门禁、`connection-grants.tsx` 的 `agent_context` 用途、关联 schema/CSS/rewrite 增量。
- **审计版本**：`10b1d8b` 后 M3-R 工作区；实现者 `/root`，独立评审者 `/root/m4_ui_review`。
- **技术栈**：Next.js 16.4、React、TypeScript、Zod、Playwright、axe。
- **业务场景**：General SaaS，本机可信单用户。
- **审查严苛度**：Mission-Critical。
- **审计时间**：2026-10-19。

## 1. 代码质量评估卡

**综合质量得分：100 / 100。结论：Pass，限本批前端及直接接口契约。**

初审识别 1 项 P1、2 项 P2，全部修复并复验。未解决 P0/P1/P2/P3 均为 0。约 400 行产品增量和独立测试，未超过单次 2,000 行范围。数据库调用准入、真实模型上下文隔离、容器执行和真实 Hook 安装另由对应专项评审及真实门禁负责，不把 mock 浏览器断言作为这些能力的证明。

| P0 | P1（初审） | P2（初审） | P3 |
|---|---|---|---|
| 0 | 1，已关闭 | 2，已关闭 | 0 |

```text
STRICTNESS: Mission-Critical
SCENARIO: General SaaS
INITIAL: 100 - 8 - 3 - 3 = 86; P1 cap -> 79, Conditional Pass
FINAL DEDUCTIONS: 0 (all findings fixed and reviewed)
RAW SCORE: 100
CAP APPLIED: none
FINAL SCORE: 100 / 100
VERDICT: Pass within scoped frontend
NOT ASSESSED: none within scope
```

## 2. 生产事故风险推演

> **预算说明漏记检索请求**：用户开启经验后，原预算框仍只写“模型 12 次”，而 Cognee 另有最多 6 次 LLM、40 次 embedding。实际后端有界预算未被此 UI 错误扩大，但界面把 Agent 调用上限误当整个任务调用上限，违背本批明确的费用分项披露。修复将 12 次标为 Agent，并独立列出一次检索的 LLM/embedding 数量和 100,000/1,900,000 token 分配；历史索引不是本任务费用。作用范围是启用记忆的任务开始前说明。

## 3. 详细审计明细

### P0 — 阻断性缺陷

none。

### P1 — 严重缺陷

**M3UI-01 [confirmed]：启用记忆后预算框仍宣称仅 12 次模型调用。**

- 位置：`frontend/components/repairs/run-controls.tsx` 预算说明；`components/memory/task-memory-choice.tsx` 检索说明。
- 维度：7 费用可观测、8 契约表达。
- 最小修复：区分 Agent 12 次、一次检索最多 6 次 LLM/40 次向量请求和各自 token 额度，不更改后端额度。
- 复核：新源码和 390px 明暗主题实际截图均显示分项上限；实际使用区域测试核对已知 100 + 600 = 700 tokens，并在存在 unknown 请求时保留“总用量尚未完全确认；金额未知”。closed。

### P2 — 主要缺陷

**M3UI-02 [confirmed]：已结束任务无法在不重载整页时复核当前经验状态。**

- 位置：`frontend/components/memory/task-memory-usage.tsx` 请求 effect。
- 原机制：仅依赖 taskId/revision；经验撤回不会增加已结束任务 revision，因此父级查询状态仍保留“当前已确认”。
- 修复：增加显式“刷新经验状态”，保留展开位置、请求期间禁用重复刷新、失败显示核对失败提示；新请求读取当前权威状态。
- 独立测试保持任务 revision 不变，将经验从 confirmed 改为 revoked，再点击刷新；显示“已撤回”。closed。

**M3UI-03 [confirmed]：嵌入运行区的经验选择器未继承主题表单样式。**

- 位置：`frontend/components/memory/task-memory-choice.tsx` BindingPicker；`frontend/app/styles/memory.css`。
- 原机制：输入样式只匹配 `.memory-page`，嵌入修复卡片时项目/来源 select 退化为无边框行内文字，与相邻字段不一致。
- 修复：选择器使用受控范围的主题表单样式，并用 disabled fieldset 覆盖其中项目操作，任务提交期间整个选择区域保持冻结。
- 复核：明暗主题 390px 截图已显示一致字段边界、上下标签及正常换行；axe 无违规。closed。

### P3 — 次要缺陷

none。

## 4. 维度覆盖声明

| # | 维度 | 结论 |
|---|---|---|
| 1 | 架构与边界 | 选择、实际调用审计、保存草稿三个组件独立；确认、外发、索引不合并。 |
| 2 | SOLID 与设计 | 共用有界传输、项目选择与严格 DTO；没有增加产品 Agent 角色。 |
| 3 | 并发与资源 | 只读请求可取消；明确开始才写入；pending 绑定持久保存；模式切换清空不支持的选择。 |
| 4 | 容错与防御 | 保存响应未知不自动重试；恢复以原请求/GET 查询；来源失效或读取失败不允许确认。 |
| 5 | 性能与伸缩 | 参考最多 8 条、调用最多 12 条、费用请求最多 46 条；项目/来源分页沿用 M4。 |
| 6 | 安全与合规 | `agent_context` 独立授权且更换用途重新勾选；任务 UI 不返回经验正文副本；BH06 排除外部报告来源。 |
| 7 | 可观测性 | 命中、获准发送、模型采纳明确区分；未知调用费用不按零。M3UI-01/02 已关闭。 |
| 8 | 类型与契约 | 新 pending schema 包含完整项目/来源/历史绑定；旧不带 memory 请求仍可用；写入带当前证据摘要。 |
| 9 | 测试性 | 新增 12 项独立浏览器回归，涵盖实际页面路由与持久刷新；受控 mock 不算真实模型。 |
| 10 | 工程规范 | 默认折叠、技术数据局部展开、来源跳转本地经验页；M3UI-03 已关闭。 |
| 11 | 数据完整性 | 前端不生成经验正文或 verified 标志；BH06 使用证据摘要和决定版本；实际数据库事务由权威评审负责。 |
| 12 | 依赖治理 | 无新增前端包；沿用锁定依赖。 |

## 5. 整改清单与验证

| ID | 级别 | 负责人 | 截止 | 状态 |
|---|---|---|---|---|
| M3UI-01 | P1 | `/root` | M3-R 验收前 | closed |
| M3UI-02 | P2 | `/root` | M3-R 验收前 | closed |
| M3UI-03 | P2 | `/root` | M3-R 验收前 | closed |

独立测试文件：`frontend/tests/agent-memory-review.spec.ts`。

1. 在新生产隔离构建执行：

   ```bash
   AWB_NEXT_DIST_DIR=.next-b5-4d14698a-6fbc-4d78-a27f-2ac617ea730c AWB_MEMORY_LIVE=1 npx --no-install playwright test --config playwright.memory.config.ts
   ```

   **33 passed (26.4s)**：本批 11 项、M4 与导航 21 项、真实隔离 API/SQLite 的 M4 生命周期 1 项。之后新增的第 12 项显式 Agent 外发授权测试单独执行：**1 passed (2.4s)**。共 34 个不同测试通过；不是将两次完整套件相加。

2. 新增测试具体覆盖：修复绑定随请求持久化、页面刷新不自动 POST；单角色清空绑定；分析 check_only 清空绑定、模型模式保留；已知/未知费用与验证角色无引用；撤回后主动核对；手机浅/深主题无横向溢出及 axe WCAG2A/2AA；BH06 响应丢失→刷新→仅 GET 恢复既有草稿；外部来源不可作为 BH06 目标；原来源失效/读取失败时禁止确认；独立 `agent_context` 授权。

3. 初次测试草稿中的 receipt 含服务契约不支持的额外 `memory` 字段、比较 fixture 漏写基线三个失败点，导致 mock 被严格 schema 拒绝。修正 fixture 后才执行上述有效门禁；这些失败不是产品问题，不计为真实模型失败。

4. `npm run typecheck`、`git diff --check` 独立运行通过。未新增依赖，未调用真实模型，未修改主库。测试临时前端由 runner 退出时回收。

5. 视觉证据：`artifacts/m3r/choice-light-390.png`、`choice-dark-390.png`。与现有中性主题一致，记忆默认折叠，打开后项目/来源/历史许可和额度说明可读；没有新增装饰图案或占据整页的空卡片。

## 6. 架构防腐与长效治理建议

1. 保留“相同任务 revision、来源状态变化”的浏览器测试，防止把运行版本误当跨资源失效版本。
2. 新增费用或模型通道时同时更新开始前上限和运行后账本，未知成本坚持显示未知。
3. 保持 BH06 的恢复测试：写入响应丢失后先查询，显式重试仍携带原请求；不得在接受候选时静默生成经验。

## 7. 审计声明

本报告证明前端交互、DTO 与负向状态边界；不证明多 Agent 运行收益，也不代替真实模型、取消竞态、调用前事务准入、BH06 数据库权威检查或 Codex 客户端信任验收。后者属于其他评审批次和实际门禁。本批范围内无未评估项、无风险豁免、无未解决缺陷。后续修改须复核受影响范围。

### 后续安全错误码兼容增量

运行评审发现后端新增 16 个候选安全错误码，而前端严格枚举尚未同步。经主 Agent 授权，本评审者仅补 `frontend/lib/repairs.ts` 的枚举与中文标签，并新增 `PATCH_SCOPE_REJECTED` 失败候选详情及终端浏览器回归；不接受任意错误字符串、不回显原始异常。隔离生产构建及 TypeScript 通过，新测试 **1 passed (1.4s)**，因此当前共有 35 个不同浏览器测试通过。初次测试仅因候选标题包含序号而 exact text locator 不匹配，改为 heading locator 后通过。

**职责分离**：此增量由本评审者实现，因此原报告不把它作为“独立自评通过”；由 `/root/m4_engine_review` 复核并在其专项报告记录结论。真实 M3 模型 gate 的网页验收另使用 `agent-memory-live.spec.ts`，尚未执行的阶段不计为通过。

### 真实代理链路复核发现（待复验）

新增 no-mock `agent-memory-live.spec.ts` 首次运行 **1 failed (280ms)**：真实隔离 API 的 `/api/repair-runs/a2c1d2f4-59bb-4382-b357-67d8d6787e11/memory` 为 HTTP 200，但经 Next 3018 访问为 404。`next.config.ts` 尚未列入 repair/analysis 任务的 memory 读取和候选 memory-draft 路由。此前 mock 测试直接拦截浏览器请求，未覆盖该代理配置缺失。

**M3UI-04 [confirmed]，Defect P1，负责人 `/root`，M3-R 完成前修复**：补上三个固定形状 rewrite，保持 ID 约束，不扩大成任意代理。修复复验前本报告整体结论暂为 **Conditional Pass（79/100）**，不得引用此前 scoped Pass 为真实网页链路通过。该失败未调用模型、未写主库，不应通过重新模型运行掩盖。


### M3UI-04 修复后的独立真实网页复验（2026-10-20）

评审者 `/root/m3_final_review`，仅追加门禁测试与评审证据，未实现产品修复。复核 `next.config.ts` 的三个固定 UUID 路径 rewrite；没有开放任意代理、改预算或重新调用模型。

**M3UI-04 已关闭；本批最终结论 Pass（100/100）**。General SaaS / Mission-Critical，当前范围未解决 P0/P1/P2/P3 均为 0；原始 no-mock 404 失败保留在上节，不用后来的通过覆盖它。

以已完成真实 gate `artifacts/m3r/7af0dbc2-f36b-4407-ab0b-3793ce882085/state` 启动独立 API 8038，无模型环境配置；常驻 8030/3010 未被本评审改动。使用隔离 production Next 构建，验证命令：

```bash
AWB_API_PORT=8038 AWB_NEXT_DIST_DIR=.next-b5-4d14698a-6fbc-4d78-a27f-2ac617ea730c npm run build
AWB_API_PORT=8038 AWB_NEXT_DIST_DIR=.next-b5-4d14698a-6fbc-4d78-a27f-2ac617ea730c AWB_M3_UI_LIVE=1 AWB_M3_TASK_ID=a2c1d2f4-59bb-4382-b357-67d8d6787e11 npx --no-install playwright test --config playwright.memory.config.ts agent-memory-live.spec.ts
```

结果 **1 passed (3.7s)**，不使用 route mock：

- repair memory 经 Next 返回 200；实际角色审计 diagnosis 1 条引用、repair 4 次各 1 条引用、verification 0 条引用。
- analysis memory 使用同一个真实 UUID（没有 analysis 记录），经代理返回后端 JSON `not_found` / 404，证明命中后端而非 Next HTML 404。
- memory-draft GET 恢复既有草稿；带合法 Origin/X-AWB-Local 的空 JSON POST 返回后端 `invalid_request` / 400，覆盖 POST 代理且不创建业务记录。
- 真实修复页面读取费用/当前引用，查询已保存草稿并跳转站内经验详情，当前接受与证据有效，确认按钮可用；没有点击确认、再次保存或运行任务。页面发起的 API 写请求为 0；测试 API context 的唯一 POST 是上述被拒绝的负向探针。
- 测试没有调用模型，没有修改主库，没有重新生成候选或草稿。读到的“verified”来自当前冻结证据核验，不只来自历史状态字符串。

证据：`artifacts/m3r/browser-a2c1d2f4-59bb-4382-b357-67d8d6787e11.json` 与同名 PNG。已人工查看真实详情截图：沿用中性主题、列表与详情并排、来源证明和确认入口可见，长文本正常换行。加上之前 35 个不同测试，本批共 36 个不同浏览器测试通过；本次只重新执行上述实际链路 1 项。

构建成功，`npm run typecheck` 在 frontend 目录通过。首次误在仓库根目录执行 typecheck 因无 package.json 退出，纠正工作目录后通过，属于命令位置错误。Next 对 tsconfig/next-env 的隔离构建生成改动已恢复到构建前 HEAD 内容；不将临时 distDir 纳入仓库。

本增量沿用上文十二维度矩阵，重点覆盖代理架构、写入边界、严格错误契约、实际持久记录与测试可观测性；未发现新增依赖或资源泄露。只证明该已完成合成真实 gate 的网页读取和草稿恢复链路，不证明任意项目修复或记忆收益。

### 最终低影响展示增量复核

root 根据实际截图将列表和详情元数据中的超过 16 字符版本摘要显示为前 12 字符加省略号；独立核对 `components/memory-workspace.tsx` 与 `components/memory/memory-editor.tsx`，完整值保留在 title，短版本不截断，编辑输入仍绑定完整 source_version，正文与证据/接口不变。该改动只缩短重复元数据，不改变授权、检索、恢复或确认行为；源码复核通过，不重复模型或浏览器门禁。最终 production 构建由 root 执行。

本次评审启动的独立 API 8038（PID 25425）已正常 shutdown，测试 Next 3018 由 Playwright 回收；没有改动常驻服务。
