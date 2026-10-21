# UI-09 记忆导航独立代码与 UI 评审

- **审计对象**：记忆模块导航、Hook 子路由、来源及事件展示、旧入口兼容、对应浏览器测试与能力文案。
- **审计版本**：`cf919862912e87488bee9adc60469d0b44a5c672` 上的 UI-09 工作区增量，2026-10-21。
- **技术栈**：Next.js 16.4、React 19、TypeScript、Playwright、axe。
- **业务场景**：General SaaS（可信本地单用户产品）。
- **审查严苛度**：Mission-Critical。
- **评审者**：独立子 Agent `memory_navigation_review`；只读实现，仅编写本报告。

## 1. 代码质量评估卡

- **综合质量得分：100 / 100（复审后）**。
- **上线评估结论：Pass，仅限本批页面归属与导航改动**。
- **审计范围**：`memory/navigation.tsx`、`workspace/memory/hooks/page.tsx`、app-shell、connections-console、memory-workspace、connection-manager、memory.css、modules/capabilities/milestones/documentation 相关增量，以及 memory-boundaries、memory-review、memory-live、agent-memory-review、progress 测试增量。既存 M5、CI 修复不属于本批审计。
- **未评估项**：本批范围内 none。Cognee 服务可用性、记忆收益、新 Hook 安装/投递均未因页面调整重新验收，不属于本报告通过范围。

| 未关闭 Defect P0 | 未关闭 Defect P1 | 未关闭 Defect P2 | 未关闭 Defect P3 |
|---|---|---|---|
| 0 | 0 | 0 | 0 |

```text
STRICTNESS: Mission-Critical
SCENARIO: General SaaS
DEDUCTIONS: 0 after closing UI09-R1
RAW SCORE: 100
CAP APPLIED: none
FINAL SCORE: 100 / 100
VERDICT: Pass — one confirmed P2 fixed and re-reviewed
NOT ASSESSED: none within the navigation change scope
```

验证证据：

- 评审者独立执行 `npm run typecheck` 与 `git diff --check`，exit 0。
- 读取最终生产构建日志 `artifacts/ci-diagnosis/memory-navigation-build-final.log`；独立检查 `.next/prerender-manifest.json`，`/workspace/memory` 与 `/workspace/memory/hooks` 均存在。
- 改动首轮专项最终输出 `36 passed (12.2s)`，日志 `artifacts/ci-diagnosis/memory-navigation-tests-final.log`。覆盖手机深色 axe、深浅色记忆页、项目保持、旧入口与只读导航，以及既有授权/撤回边界。
- 最终默认全 UI 输出 `15 skipped`、`241 passed (3.5m)`，0 失败，日志 `artifacts/ci-diagnosis/memory-navigation-full.log`；其中第 106–108 项分别覆盖导航保持、旧入口兼容、503 保持。15 个受环境开关保护的真实执行测试未在此轮运行，不算通过。
- 实现者生产 HTTP 验证 memory/hooks/connections 均 200；评审者独立查看实际截图 `artifacts/ci-diagnosis/memory-hooks-page.png`，记忆主导航与 Hook 子导航高亮、真实 `codex-local` 来源和事件处理状态可见。截图不证明平台持续在线。

## 2. 生产事故风险推演

无未关闭 P0/P1。已追踪的项目上下文丢失为局部导航缺陷，见 UI09-R1；不涉及数据库权限或跨租户隔离承诺。

## 3. 详细审计明细

### Defect P0 — 阻断性缺陷

none。

### Defect P1 — 严重缺陷

none。

### Defect P2 — 主要缺陷

#### UI09-R1：项目列表慢或失败时导航丢失深链接项目（已关闭）

- **位置**：`frontend/components/memory/navigation.tsx:6–14`，直接调用方为 memory-workspace 与 connection-manager；项目 hook 初始 project 为空。
- **维度**：4 容错、9 边界测试。
- **置信度**：[confirmed]。
- **原失效路径**：首次渲染仅以 context.project 形成导航 query；`loadProjects` 未返回或失败时仍为空。从 `?project=demo` 点击另一子页生成无项目 URL，可能落入默认首个项目。
- **最小修复**：组件 mount 后捕获 URL project，在领域 context 尚未解析时保留它；初始化前取消客户端导航并标记不可用；项目由 context 更新后以当前选择优先。保留 `encodeURIComponent` 和既有导航守卫。
- **验证**：新增“项目列表不可用时子导航仍保留深链接项目”以 503 响应覆盖，检查实际点击后的 URL 和返回链接。复审确认不会产生写入，也不替代服务端项目校验。

### Defect P3 — 次要缺陷

none。

## 4. 维度覆盖声明

| # | 维度 | 发现数 | 结论 |
|---|---|---|---|
| 1 | 架构与边界 | 0 | 来源管理归属记忆，环境页移除表单；复用原管理组件，不复制写逻辑。 |
| 2 | SOLID 与现代设计 | 0 | 两页共用导航，路由只负责页面组合。 |
| 3 | 并发与资源安全 | 0 | hashchange 监听有对应清理；新增导航不启动任务。 |
| 4 | 容错与防御 | 1 已关闭 | UI09-R1 修复项目列表失败的导航丢失。 |
| 5 | 性能与伸缩性 | 0 | 保留现有分页；展示最多当前 100 条事件，不引入逐条远程查询。 |
| 6 | 安全与数据合规 | 0 | 旧链接重定向目标固定同源，只编码 project 参数；浏览不发凭据、不确认、不授权、不调用模型。 |
| 7 | 可观测与运维 | 0 | 区分接收、处理、来源报告；凭据有效不声称 Codex 在线或已信任。 |
| 8 | 类型安全与契约 | 0 | TS 检查通过，新增 props 有明确类型；未改 API schema。 |
| 9 | 可测试性 | 1 已关闭 | 新增导航、旧入口、只读请求及 503 测试，复用生命周期和 axe 门禁。 |
| 10 | 工程规范 | 0 | 现有主题 token、导航/标题语义复用，进度文案不引入工程计数。 |
| 11 | 数据与事务完整性 | 0 | 本次不改持久化、幂等写入、授权/确认事务或迁移。 |
| 12 | 依赖治理 | 0 | 无依赖及 lockfile 变更。 |

维度 4/9 为同一条 UI09-R1，不重复计数扣分。

## 5. 整改清单

| ID | 严重级 | 负责人 | 截止 | 状态 |
|---|---|---|---|---|
| UI09-R1 | Defect P2 | root 实现者 | 本批交付前 | fixed / re-reviewed |

## 6. 架构防腐与长效治理建议

1. 保留项目列表失败与旧链接兼容测试；后续记忆子模块新增时复用导航组件，防止项目上下文再次丢失，成本为扩充同一测试矩阵。
2. 新增平台状态必须绑定实际遥测来源；沿用当前“当前事件页”“接收已启用”等限定表述，不能以配置存在替代客户端在线或投递证明。

## 7. 审计声明

本结论适用于上述工作区快照；后续实现增量需补审。无风险豁免，未向主库写入测试数据或调用模型。本报告不证明上游向量接口稳定、Cognee 图谱能力已交付、M5 收益成立或 GitHub Checks 已恢复；这些是独立门禁。
