# M4-R 网页经验管理独立评审

- **审计对象**：`frontend/components/memory-workspace.tsx`、`components/memory/*.tsx`、`lib/memory.ts`、`app/styles/memory.css`，以及本批导航、能力标记、接入页、Next rewrite、迭代历史和浏览器测试增量。
- **审计版本**：`10b1d8b` 后 M4-R 工作区增量；实现者 `/root`，独立评审者 `/root/m4_ui_review`。
- **技术栈**：Next.js 16.4、React、TypeScript、Zod、Playwright、axe。
- **业务场景**：General SaaS；本机可信单用户产品界面。
- **审查严苛度**：Mission-Critical。
- **审计时间**：2026-10-19。

## 1. 代码质量评估卡

**综合质量得分：100 / 100；上线评估结论：Pass（本批前端范围）。** 初审 1 项 Defect P1、2 项 Defect P2 均修复，并由独立浏览器复验关闭。最终未解决 P0/P1/P2/P3 均为 0；范围内未评估项 none。

审计范围约 650 行产品代码及测试增量，小于单次 2000 行门槛。仅评审前端与直接 HTTP 契约；数据库、引擎生命周期、真实 Codex 插件执行由另三份专项评审覆盖，不把浏览器 mock 计为 Hook 或模型验收。

| P0 阻断 | P1 严重（初审） | P2 主要（初审） | P3 次要 |
| :---: | :---: | :---: | :---: |
| 0 | 1 | 2 | 0 |

初审判定轨迹：100 − 8 − 3 − 3 = 86，存在 P1 封顶 79，Conditional Pass。最终评分按未解决发现计算：

```text
STRICTNESS: Mission-Critical
SCENARIO: General SaaS
DEDUCTIONS: 0 (three confirmed findings fixed and independently retested)
RAW SCORE: 100
CAP APPLIED: none
FINAL SCORE: 100 / 100
VERDICT: Pass for scoped M4-R frontend
NOT ASSESSED: none within scope
```

## 2. 生产事故风险推演

> **旧来源与显示的来源不一致**：项目持续收到事件，旧经验的来源事件离开最近 100 条列表；用户打开旧经验并修改标题。React 对找不到原值的 select 显示第一个新事件，但提交仍携带旧事件。独立浏览器检查得到显示 `new-source/new-event`，实际 PATCH 为 `old-source/old-event`。修改操作本身没有丢失旧归属，但用户审核看到的来源错误，破坏来源可核对契约。爆炸半径是这类旧经验的编辑页；修复后必须保留当前原值选项，并明确翻页选择。

## 3. 详细审计明细

### P0 — 阻断性缺陷

none。

### P1 — 严重缺陷

**UI-01 [confirmed]：历史来源离开事件页后，表单显示与提交来源不同。**

- 位置：`frontend/components/memory/memory-editor.tsx:23`，来源事件 select。
- 维度：8 类型与契约、11 来源完整性。
- 最小修复：原来源不在当前页时补充当前值 option；增加事件翻页，切换列表页不改变已选来源。
- 实现者已按上述方式修复，独立回归 `memory-review.spec.ts` 的第一项验证 DOM 当前值和 PATCH 的来源同时相等。

### P2 — 主要缺陷

**UI-02 [confirmed]：连接状态把分页结果当全量历史。**

- 位置：`frontend/components/memory/connection-manager.tsx` 的 `received` 和来源状态文本。
- 维度：7 可观测性与产品反馈。
- 场景：旧来源有历史事件，但不在当前事件页，错误显示“等待首条事件”；页内数量也曾被写作累计接收数。
- 修复：文字明确限定“本页”数据，不据此推断从未连接。独立回归检查不能再显示“等待首条事件”。

**UI-03 [confirmed]：关闭详情丢失键盘导航位置。**

- 位置：`frontend/components/memory-workspace.tsx:22`，`close()`。
- 维度：4 可访问性与用户操作恢复。
- 场景：键盘打开第二条及之后的经验，关闭后总是跳到第一条。
- 修复：行绑定 `data-memory-id`，优先按原选中 ID 恢复焦点，行已不存在才使用第一条。独立回归以第二条验证焦点。

### P3 — 次要缺陷

none。审计期间迭代历史新增项曾产生 TypeScript 语法错误；编译门禁已发现，实现者已修复，最终独立 `npm run typecheck` 退出 0，不计为未解决缺陷。

## 4. 维度覆盖声明

| # | 维度 | 结论 |
|---|---|---|
| 1 | 架构与边界 | 管理、来源授权和引擎启动分开；不将确认草稿等同于外发许可。 |
| 2 | SOLID 与现代设计 | 项目上下文、编辑、授权、连接和引擎控制有独立组件与共同传输层。 |
| 3 | 并发与资源安全 | GET 可取消、页面更新有 ticket、轮询有上限；未知引擎请求跨刷新保留身份。 |
| 4 | 容错与防御 | 写入错误不自动重放；策略/授权详情读取失败时按钮保持禁用；UI-03 已修复并复验。 |
| 5 | 性能与伸缩 | 项目和每组列表独立分页；传输读取 2 MiB 上界；筛选范围明确为当前页。 |
| 6 | 安全与合规 | 凭据只驻 React 内存、密码框显示、关闭清除；下载需主动操作并说明私有权限；无 raw HTML。 |
| 7 | 可观测性 | 接收/处理/来源自报分开；已知 tokens 与未知费用分开；UI-02 已修复并复验。 |
| 8 | 类型与契约 | Zod 验证服务器返回；不展示服务器异常正文；UI-01 已修复并复验。 |
| 9 | 测试性 | mock 负向测试、独立评审回归、实际 API/SQLite 流程分列。 |
| 10 | 工程规范 | 沿用主题、模块/导航、文档站、原生 details 与对话框；迭代历史完整。 |
| 11 | 数据与事务 | 前端提交 CAS 版本，策略/授权独立详情读取；数据库事务本批 UI 范围外，由 authority 评审覆盖。 |
| 12 | 依赖治理 | 未新增前端依赖；原锁定 Next/React/Zod/Playwright 足够。 |

## 5. 整改清单与验证

| ID | 严重级 | 负责人 | 截止 | 状态 |
|---|---|---|---|---|
| UI-01 | P1 | `/root` | M4-R 验收前 | closed — 修复后独立浏览器通过 |
| UI-02 | P2 | `/root` | M4-R 验收前 | closed — 修复后独立浏览器通过 |
| UI-03 | P2 | `/root` | M4-R 验收前 | closed — 修复后独立浏览器通过 |

独立执行记录：

- `npm run typecheck`：初次因迭代记录语法失败；修复后退出 0。
- `frontend/tests/memory-review.spec.ts`：修复前 `3 failed, 2 passed (20.0s)`；3 个失败与上列发现一一对应，策略和授权读取 503 时不发送写请求的两项通过。首次测试脚本的 exact label/Next route announcer locator 问题已修正，不计为产品缺陷。
- 修复后完整独立执行 `AWB_NEXT_DIST_DIR=.next-b5-4d14698a-6fbc-4d78-a27f-2ac617ea730c AWB_MEMORY_LIVE=1 npx --no-install playwright test --config playwright.memory.config.ts`：**22 passed (20.2s)**。其中 5 项独立评审测试全部通过，包含来源显示/提交一致、焦点恢复、分页状态、失败策略/授权读取；1 项为真实隔离 Next/API/SQLite 管理生命周期，其余为受控浏览器契约/导航测试。未调用模型，未改常驻主库。
- 独立浏览器 390px、light/dark、reduced-motion：`overflow: false, violations: []`；axe scope 为经验与事件区域。第一次脚本使用 browser.newPage 而非 newContext，被 axe 拒绝；修正调用后上述结果才成立。
- 视觉核对：`artifacts/m4r/memory-dark.png` 与独立 `review-connections-light.png` / `review-connections-dark.png`。列表与详情分栏，技术信息折叠，授权文案与操作相邻；390px 单列不溢出。已有接入页其余环境内容保留，不将旧模块布局当本次新设计。

## 6. 架构防腐与长效治理建议

1. 保留这批跨页来源和分页状态回归：成本为五项浏览器测试，防止小数据测试掩盖实际历史数据错误。
2. 任何策略、来源权限或引擎状态新控件继续先读取权威版本再允许操作；读取失败不把默认值当“尚未配置”。
3. 前端验收报告持续区分 mock、实际 API、真实模型与真实客户端 Hook；页面状态只能取已验证的那一层。

## 7. 审计声明

本报告只对当前 M4-R 前端范围负责；M3-R 角色取用不在本次审计对象中。没有读取其他项目或记忆。没有以模拟 Hook 或合成事件证明 Codex 客户端实际触发。无风险豁免，三个发现已由新生产构建的浏览器测试复验关闭。后续 M3-R 改动及任何改变这些已审契约的增量须另行复核。
