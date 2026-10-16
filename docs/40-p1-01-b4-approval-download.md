# P1-01 B4：人工决定与精确补丁下载

状态：`done`。B4 在 B3 的网页执行、持久事件和常驻服务之上增加人工审阅边界。它只保存用户对候选版本的接受或拒绝，并导出服务端重新核对后的精确 diff；任何决定都不会写回用户项目。

## 状态和不变量

批次仍按 `planned → ready → in_progress → in_review → done` 流转。B4 的决定状态独立于候选的 `candidate_verified`：

- `candidate_verified` 是宿主冻结回归产生的证据结论。
- `accepted` / `rejected` 是用户对当前候选的人工决定。
- 证据、候选快照或比较摘要变化后，历史决定保留，但 `current_valid=false`，不能继续下载。
- 接受只允许当前比较为 `finished/candidate_verified`；拒绝也必须绑定同一份当前证据。
- 同一任务同一时刻最多一个仍然有效的接受候选；已经失效的历史接受不阻塞新的有效候选。
- 下载只从服务端快照生成，文件名由服务端生成，接口不接受路径，也不写回项目。

## 存储模型

`0009_candidate_decisions` 保存每个候选的当前 head；`0010_decision_events` 增加追加式 request ledger。账本按 `(task_id, request_id)` 幂等、按 `(task_id, revision)` 排序，任务级 revision 是客户端 CAS 值。候选 head 的 `CandidateDecision.revision` 仍用于该候选最近决定的显示；receipt 的 `revision` 才是下一次 POST 的 `expected_revision`。

receipt 同时返回 `current_valid`、`can_accept`、`can_reject`、`can_download`。决定 payload 的 history 保留最近八条，完整 request 历史由追加式账本保存。历史 request 在后续 revision 后重试仍返回原决定，不重复写入。
任务级决定 revision 上限为 255；达到上限时服务以稳定的 `decision_limit` 冲突返回，不把数据库约束异常暴露为 503。

审批 API：

```text
GET  /api/repairs/{task_id}/candidates/{candidate_id}/decision
POST /api/repairs/{task_id}/candidates/{candidate_id}/decision
GET  /api/repairs/{task_id}/candidates/{candidate_id}/download
```

POST 必须带 `request_id`、`decision`、`expected_revision`、候选摘要和比较摘要。非法 UUID、来源、Content-Type、查询参数和未知字段在 HTTP 边界拒绝。决定与下载工作在线程池信号量内执行，超过四个并发审批请求返回 `approval_busy`，不耗尽服务线程。

## 前端行为

审批区只在当前证据允许时启用。POST 响应丢失会把原 request 暂存于当前 session；刷新先 GET 恢复，重试复用同一 request ID。409 显示刷新入口，不生成新的盲重试决定。历史失效显示“历史已接受/已拒绝”，隐藏下载。下载前校验任务 ID、候选 ID、内容类型、1 MiB 上限和服务端 SHA-256。

## 验收矩阵

| 范围 | 验收 |
|---|---|
| 存储 | 接受、拒绝、CAS 冲突、旧 request 幂等、尾部历史、篡改版本、下载撤回 |
| 并发 | 两个 worker 同任务同 revision 只有一个成功；线程池超额返回 `approval_busy`；有效候选唯一 |
| HTTP | GET/POST/download、Origin、Content-Type、非法 ID、查询参数、未知 body 字段、错误映射 |
| 前端 | 接受与下载、响应丢失恢复、409 刷新、历史失效、摘要/关联/大小校验、390px 浅深色与键盘焦点 |
| 迁移 | 0009 → 0010；历史 payload 补齐 `candidate_id`；旧 schema 未迁移时 fail closed |

实际命令与结果写入本批交付记录：

- `pytest tests/test_b4_decisions.py tests/test_b4_api.py tests/test_b4_concurrency.py tests/test_web_repair_migration.py`：15 passed。
- `npm run typecheck`、`npm run build`：通过。
- `playwright test tests/repairs.spec.ts`：27 passed（含 B4 用例）。
- Ruff、mypy、`git diff --check`：通过。

独立报告：[B4 后端评审](reviews/P1-01-B4-backend-review.md)、[B4 前端评审](reviews/P1-01-B4-frontend-review.md)。B5 仍负责真实模型/Docker/网页整体验收，B4 不宣称任意项目接入或自动写回。
