# CI-01：GitHub Checks 失败排查与修复

2026-10-21。Defect P1。状态 in_review（planned → ready → in_progress → in_review）。本批只修改 CI 依赖准备和前端测试替身，不修改产品执行、模型、记忆或授权逻辑。

## 远端事实

- 最新失败：[39531352356](https://github.com/ykduan0916/agent-repair-workbench/actions/runs/39531352356)，提交 `cf91986`。
- deterministic：1867 passed、69 skipped、1 failed。`test_next_dist_dir_rejects_paths_and_preserves_production_default` 调用 Node 加载 TypeScript，独立 Python job 未安装前端依赖，报 `Cannot find module 'typescript'`。
- product-ui：27 项失败，其中 26 项来自三个旧测试替身匹配整个 `/api/analysis-runs**`，新增的 `GET /api/analysis-runs/{id}/memory` 落入创建分支，空请求体触发 TypeError，并污染 starts 计数。另 1 项是记忆文档插入到“快速上手”之前，违反导航顺序约定。不是 27 个独立产品故障。
- B4 提交 `31a5acf` 的历史失败是 Ruff format 的 4 个文件；后续已修复。B5 `67b50d0` 的 product-ui 成功，deterministic 在 pytest 失败；`f8a6f35` 日志确认同一个缺 TypeScript 依赖问题。历史红色记录不会因新提交成功而消失。

日志保存在忽略目录 `artifacts/ci-diagnosis/`，不将原始日志加入 Git。

## 修复

1. deterministic job 显式安装与 product-ui 相同固定版本的 Node，执行前端 `npm ci`，保持锁文件和配置契约测试，不跳过该测试。
2. 三个测试替身显式处理只读记忆端点，返回未启用记忆的合法响应；创建分支断言方法 POST 和精确路径。未知端点不能再被记作新任务。
3. 恢复快速上手置顶，保留记忆指南。索引边界测试在确认后等待新状态呈现，再展开随版本重挂载的编辑器；不以固定睡眠掩盖竞态。
4. 不增加重试、不放宽断言、不使用 continue-on-error、不关闭检查。

## 验证

- `uv run --frozen pytest tests/test_b5_live_gate.py -q`：24 项通过，含失败的 TypeScript 配置契约测试。
- 三个受影响前端文件：29 项通过，日志 `artifacts/ci-diagnosis/frontend-focused.log`。
- `npm run typecheck`、全仓 Ruff lint/510 文件格式检查、`git diff --check`：通过。
- 首次默认全量前端测试 235 passed / 3 failed / 15 skipped：文档顺序、索引测试时序，以及编辑后文档未构建导致链接检查不一致。保留首次结果，修复后重新构建验收。
- 独立评审见 [CI-01](reviews/CI-01-checks-review.md)，新增修改复审通过，未遗留 P0/P1。
- 远端 Ubuntu 的最终绿色门禁仍需本补丁获准提交推送后验证；本机通过不等于远端通过。

### 最终本地结果

- 重建后默认完整 UI：238 passed / 15 skipped / 0 failed（3.5 分钟），日志 `artifacts/ci-diagnosis/frontend-final.log`。跳过项仍要求专用真实运行环境，未将它们计为通过。
- 生产构建、TypeScript、Ruff lint/512 文件格式、diff check 均通过。
- 前后端常驻服务已重启，API health 与网页 docs 均 HTTP 200；实际网页确认快速上手置顶。
- 本地补丁评审 Pass；CI-01 保持 in_review，待获准推送后的同 SHA GitHub Ubuntu 门禁，不宣称远端已恢复。此次未改产品 Agent 与记忆处理逻辑，没有重新调用模型。

## 交付检查约束

后续获准推送时，必须核对该提交 SHA 对应的两个 Checks 作业及实际失败日志。不能以本机测试、push 成功、或更早提交的绿灯宣称当前远端通过。此次修复不替代 M5 真实记忆效果验收。


## 2026-10-21 推送后的远端验证与后续修正

`ac6b870`已推送。远端[39562903720](https://github.com/ykduan0916/agent-repair-workbench/actions/runs/39562903720)的deterministic成功（包括完整Python测试）；product-ui于总任务15分钟时限到达后cancelled。准备/构建/浏览器安装已占约6分钟，E2E仅记录“260 tests”后被取消，两个live步骤未运行。现有日志不能确定具体慢用例或排除卡住，不能称全绿。

有界修正：product-ui任务45分钟，E2E步骤20分钟并显式line reporter，两个会重新构建的live步骤各8分钟；保留现有测试级时限（多数30秒，已有appearance用例120秒），不跳过测试、不增加自动重试。先改善可观测性与时间预算，远端后续结果另验；若再次超时必须根据具体进度定位，不继续盲目加时。日志保存在本机`artifacts/ci-ui-ac6b870.log`。
