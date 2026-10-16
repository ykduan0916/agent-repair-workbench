# P1-01 B4 前端独立代码评审

- **评审对象**：B4 人工审批与精确补丁下载的前端改动：`frontend/components/repairs/approval-panel.tsx`、`frontend/lib/decisions.ts`、`frontend/app/styles/repairs.css`、`frontend/components/repair-workspace.tsx`、`frontend/tests/repairs.spec.ts`
- **评审基线**：B3 已提交版本之后的 B4 交付冻结工作树；后端 receipt、CAS、事件账本和下载响应头为本轮联调契约
- **技术栈**：Next.js 16、React 19、TypeScript、Zod、Playwright、axe-core
- **评审范围**：前端状态契约、人工决定幂等恢复、stale acceptance、下载完整性、键盘/窄屏/暗色模式、回归测试
- **评审方式**：只读检查实现与调用边界，运行类型检查、生产构建、浏览器回归和 B4 后端契约测试；本报告之外不修改生产代码
- **评审时间**：2026-10-16 Asia/Shanghai

## 结论

**Pass**。当前 B4 前端没有未关闭的 Defect P0/P1，满足进入主任务整合和提交前复审的条件。人工决定只记录审批，不写回用户项目；补丁只有在服务端确认当前候选、当前回归证据和接受决定均匹配时才能下载。

## 验收证据

| 检查 | 结果 |
|---|---:|
| `npm run typecheck` | 通过 |
| `npm run build` | 通过；Next.js 生成 148 个静态页面 |
| `PLAYWRIGHT_CHANNEL=chrome npx playwright test tests/repairs.spec.ts --config playwright.repairs.config.ts` | **27/27 通过** |
| B4 浏览器场景 | **3/3 通过** |
| `uv run pytest -q tests/test_b4_api.py tests/test_b4_concurrency.py tests/test_b4_decisions.py` | **11/11 通过** |
| `git diff --check` | 通过 |
| `uv run ruff check`（B4 后端联调范围） | 通过 |

Playwright 输出中的 `8030 ECONNREFUSED` 是测试配置中的本机 API 代理日志；本轮页面 API 均由 route mock 提供，未把该日志当作浏览器测试失败。

## 重点核对

1. **状态权限来自后端 receipt**：按钮和下载入口使用 `can_accept`、`can_reject`、`can_download`，不自行从历史状态推断权限。
2. **stale 决定可见且不可误用**：当前候选或回归证据摘要变化后显示“历史已接受/已拒绝”和重新核对提示，并隐藏下载入口。
3. **请求可恢复且幂等**：POST 响应丢失时把 request ID 和版本摘要保存在 `sessionStorage`；刷新后读取 receipt，重试复用原 request ID，不创建第二个决定。
4. **冲突可操作**：服务端返回 409 时保留当前页面和证据，显示刷新入口；不会把冲突当作成功。
5. **下载边界完整**：前端核对内容类型、任务 ID、候选 ID、补丁 SHA-256、1 MiB 上限和白名单文件名；后端同时重复校验当前 accepted/candidate_verified 状态。
6. **界面可用性**：B4 用例覆盖 390px 暗色模式、键盘焦点和 axe-core；审批说明保持为“只记录当前候选，不会写回项目”。

## 初次评审发现与修复

初次只读评审发现以下问题，已在复审前修复并由回归测试覆盖：

- 使用了不存在的 `--success` 设计 token，改为现有 `--pass`。
- 仅由旧字段推导审批状态，不能表达 stale acceptance；改为消费后端 `current_valid` 与 `can_*` 字段。
- 响应丢失后会新建 request ID；增加 pending request 的 sessionStorage 持久化和同 ID 重试。
- 缺少 B4 浏览器验收；新增响应丢失、stale/窄屏无障碍、409/下载摘要校验三组场景。
- 409 断言最初命中 Next 路由 announcer 的同名 `role=alert`；测试已收窄为 `.callout[role="alert"]`，27 个 repairs 用例全部通过。

## 未评估与发布前条件

- 本报告不替代后端 B4 独立评审；后端迁移、CAS 和常驻服务状态以对应报告为准。
- 真实模型、Docker 执行链路和生产部署不属于 B4 前端范围。
- 主任务提交前需确认前端与最终后端 receipt 字段、下载响应头和 Next 代理转发保持一致，并在提交后重跑本报告中的前端命令。

## 评审签核

- **前端评审结论**：Pass
- **阻断项**：无
- **后续责任**：主任务整合后提交前重跑验证，并确认 8030/3010 常驻服务状态
