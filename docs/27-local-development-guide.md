# 本地开发、运行与验收指南

本指南承接 [README 的产品介绍与最短体验](../README.md)，集中说明安装、网页、模型配置、执行隔离、记录恢复与测试。命令对应当前实现；新功能状态以 [迭代台账](03-roadmap.md) 为准。

## 按目标选择入口

| 想做什么 | 阅读位置 | 需要什么 |
|---|---|---|
| 验证一份工具调用 JSON | [环境与离线检查](#环境与离线检查) | Python 3.12、uv |
| 打开本地网页 | [启动网页](#启动网页) | 再加 Node 24 |
| 调用审核过的 MCP 服务 | [MCP stdio 调用](#mcp-stdio-调用) | 固定 profile；公开案例需显式准备 |
| 在隔离容器中复现故障 | [受控容器执行](#受控容器执行) | 本机 Linux Docker daemon、固定镜像 |
| 发起模型分析 | [模型分析与配置](#模型分析与配置) | 自己授权的现有模型接口；复现工具另需 Docker |
| 查记录、处理进程中断 | [运行记录与恢复](#运行记录与恢复) | 同一 `AWB_STATE_DIR`，显式恢复操作 |
| 验证本次开发改动 | [工程验收与常见问题](#工程验收与常见问题) | 按门禁分别准备环境 |

## 环境与离线检查

使用 macOS / Linux、Python 3.12 和 uv。首次 `uv sync` 安装锁定依赖需要联网；安装完成后的 `check` 离线执行，不需要 API key 或 Docker。以下命令均从项目根目录执行，除非另有说明。

```sh
uv sync --frozen
uv run --frozen agent-workbench check examples/passing.json
uv run --frozen agent-workbench check examples/failing.json
```

第一条检查退出 0；第二条预期退出 1。检查无法完成退出 2。默认限时 5 秒，可用 `--timeout 10` 修改；报告打印到 stdout。`examples` 是合成证据；`check` 命令只检查已有记录，不执行工具或调用模型。

```sh
uv run --frozen ruff check .
uv run --frozen ruff format --check .
uv run --frozen mypy
uv run --frozen pytest
```

原始 trace 和 `.env` 不提交。分享报告前核对字段路径是否含敏感信息；摘要哈希不是匿名化保证。CLI 不支持 Windows 文件打开语义，Windows 移植需独立验收。

## 启动网页

首次安装先在项目根目录运行 `uv run --frozen agent-workbench runs init` 创建记录库。已有库升级时必须先停止写入并备份，见下文“运行记录与恢复”；读取页面不会自动迁移。然后启动本地 API：

```sh
uv run --frozen uvicorn agent_workbench.api:app --host 127.0.0.1 --port 8030 --no-access-log
```

另开终端启动前端（Node 24）：

```sh
cd frontend
npm ci
NEXT_TELEMETRY_DISABLED=1 npm run build
NEXT_TELEMETRY_DISABLED=1 npm run start
```

访问 <http://127.0.0.1:3010>，工作区入口为 `/workspace`（总览）。调用检查可导入自己的 JSON 调用证据；「复现」支持四种内置场景的真实复现、停止请求、运行记录与校验报告；「分析」可选择内置案例与目标，开始或停止分析，并查看真实证据、预算、结构化事件及关联运行；「修复」支持运行两个内置案例、停止、核对代码差异与回归、人工决定和下载补丁；「环境与接入」显示本机服务、Docker 与模型配置状态。侧栏底部常驻本机环境状态。每个页面标题旁用一个状态说明能力边界（网页可用 / 网页只读 / CLI 可用 / 待开发）。全站支持浅色、深色及跟随系统，主题偏好仅保存在本机；[更新记录](http://127.0.0.1:3010/updates) 列出各阶段用户可感知的变化。

### 前端测试

在 frontend 目录运行：

```sh
npm run typecheck
npx playwright install chromium
NEXT_TELEMETRY_DISABLED=1 npm run test:e2e
```

测试需要先 production build；Playwright 自动启动未运行的本地服务。已有服务时复用，开发者应先重启到最新构建。本机 Chromium 下载慢时可用已安装的 Chrome：`PLAYWRIGHT_CHANNEL=chrome npm run test:e2e`；CI 使用锁定 Playwright 对应 Chromium。额外运行 `npm run test:runs-live` 可在独立临时数据库与 8031/3011 服务验证运行记录；该命令会按测试端口重新构建，之后启动产品前须用默认环境再次 `npm run build`。CI 已配置这项门禁，本地默认使用 Chrome，CI 通过 `PLAYWRIGHT_CHANNEL=''` 使用安装的 Chromium。浏览器测试包含真实 Python API、数字/BOM保真、错误恢复、预览交互、手机和桌面 axe 检查；并非全用 mock。

[产品界面规格](07-web-workbench.md) · [P0-02 接入设计与测试计划](08-mcp-integration.md)


## MCP stdio 调用

```sh
uv run --frozen agent-workbench mcp-check --server-config examples/mcp/pass-server.json --case examples/mcp/lookup-case.json
uv run --frozen agent-workbench mcp-check --server-config examples/mcp/failing-server.json --case examples/mcp/lookup-case.json
```

上述使用项目已审核的真实 stdio fixture，预期分别退出 0/1。输入参数失败不会调用工具，调用后超时/断连记录 outcome_unknown，不自动重试。配置不能传入任意 command/args/env；本阶段尚非任意代码沙箱。

公开案例的准备、只读核验和来源见 [实验说明](../experiments/public_mcp_case/README.md)。显式准备后：

```sh
uv run --frozen agent-workbench mcp-check --server-config examples/mcp/public-server.json --case examples/mcp/public-case.json
AWB_PUBLIC_CASE=1 uv run --frozen pytest -m public_case
```

公开历史错误的预期 CLI 退出码是 1。MCP 报告只保存版本、摘要与问题路径，标明 SDK 规范化数据和原始 wire 未检查；网页接入能力仍按台账逐项解锁。

## 受控容器执行

前提：本机 Linux Docker daemon（macOS 可用现有 Colima）、固定基础镜像已明确准备。
仅运行项目审核过的合成故障服务；网页「复现」页可发起下述四种固定场景，不接受用户上传的命令或代码。

```sh
uv run --frozen python containers/p0_03/prepare.py
uv run --frozen agent-workbench sandbox-check --server-config examples/sandbox/pass-server.json --case examples/mcp/lookup-case.json
uv run --frozen agent-workbench sandbox-check --server-config examples/sandbox/lost-response-server.json --case examples/mcp/lookup-case.json
```

正常检查预期退出 0；响应丢失预期退出 2/outcome_unknown，不能自动重试。
容器默认断网、非 root、只读根、0.5 CPU、128MiB 内存、32 个进程、16MiB tmpfs；
无宿主挂载或凭据继承，Docker 日志关闭。原始帧在解码前受字节限制；
报告只保留摘要、错误码、计数和清理确认。运行配置/镜像源码变化需重新显式准备。
镜像取自本次构建 iidfile 并按完整 ID 执行，运行不会自动下载或构建。

正常取消执行精确清理；宿主被 SIGKILL 时容器独立监护按 lifetime 停止（默认45秒，依赖正常内核调度）。
这时没有最终报告；停止容器的元数据可能残留，按已知 run ID 显式检查/清理：

```sh
uv run --frozen agent-workbench sandbox-reap --run-id <UUID>
uv run --frozen agent-workbench sandbox-reap --run-id <UUID> --apply
```

只处理名称/项目标签/run ID 一致的停止容器；不会删除活跃目标。
P0-04 已增加持久运行记录；恢复对账通过下述显式命令进行，不会自动重投工具调用。

```sh
# 默认门禁：没有Docker也可跑；容器测试显示skip，不计容器验收通过
uv run --frozen pytest
# 本机显式准备两类实验后，P0-02～05 门禁（不含后续独立开关）
AWB_PUBLIC_CASE=1 AWB_DOCKER_TESTS=1 AWB_TOOL_DOCKER=1 uv run --frozen pytest
```

新增网页分析 T05 集成门禁：`AWB_WEB_ANALYSIS_DOCKER=1 uv run --frozen pytest tests/test_web_analysis_docker.py`，真实 API/SQLite/MCP/Docker，模型为确定性测试适配器。P0-06 候选容器需另行显式准备 `containers/p0_06/prepare.py`，以 `AWB_CANDIDATE_DOCKER_TEST=1` 开启对应 candidate 测试；两案例真实容器门禁另用 `AWB_HELDOUT_DOCKER=1`，具体结果见P0-06交付记录。

[执行规格与故障矩阵](11-controlled-execution.md) · [镜像准备与故障服务](../containers/p0_03/README.md)


## 运行记录与恢复

`sandbox-check` 现在先保存执行租约，再启动容器；终态、事件、报告在同一事务提交。
CLI 报告 `record_id` 是持久运行 ID，原 `run_id` 是容器 attempt ID。
本地默认目录 `artifacts/run-store`，可通过 `AWB_STATE_DIR` 为 CLI 和 API 配置同一个独立目录。
不保存原始参数/结果，报告摘要不代表匿名化。网页 `/workspace/tasks` 使用这个目录，支持发起固定场景、请求停止、查看记录与校验报告。

```sh
uv run --frozen agent-workbench runs init
uv run --frozen agent-workbench runs list
uv run --frozen agent-workbench runs show <record_id>
uv run --frozen agent-workbench runs report <record_id>
uv run --frozen agent-workbench runs reconcile
uv run --frozen agent-workbench runs prune --days 30
uv run --frozen agent-workbench runs prune --days 30 --apply
```

读取命令不创建库。`reconcile` 只检查过期执行与未确认资源，无法确认完成的调用保持
`outcome_unknown`，不执行重试；恢复事件不能补造缺失的原始报告。
`prune` 默认只预览，只有超期终态、资源确认不存在且报告有效的记录可清理。
没有可靠报告的崩溃记录保留待人工调查。资源清理使用 `sandbox-reap --run-id <attempt_id>`。

数据库迁移版本随当前分支维护，见 [RunStore 的 REVISION 常量](../src/agent_workbench/run_store/store.py)。已有库与代码版本不同时，需停止写入、备份后显式运行 `runs init` 升级，GET 不自动迁移。含数据时禁止降级；活动数据库备份必须使用 SQLite
`Connection.backup()`，不能只复制主数据库而漏掉 WAL。先停止 CLI/API 写入再切换恢复目录；
通过 `AWB_STATE_DIR` 指向恢复副本，执行 `runs list/show/report` 核对记录与摘要后再使用。
备份与恢复的可执行演练见 `tests/test_run_store.py::test_live_wal_backup_refused_downgrade_and_restore`。
完整契约、限制与验收见 [P0-04 说明](14-run-store.md)。

## 协作接口目录

工具接入与诊断页可通过本机`GET /api/agent-contracts`查看、下载三个角色及五类工具请求的实际JSON schema。目录 catalog_version=3，`cli_runtime_available=true`、`web_runtime_available=true`；CLI 与网页均可发起分析。此处表示能力已实现；实际模型就绪状态及执行槽由 `/api/analysis-runs/catalog` 返回。详见[冻结规格](18-p0-05a-contracts.md)与[验收记录](19-p0-05a-delivery.md)。

## 网页受控复现

1. 按上文显式准备 Docker 固定镜像，执行 `uv run --frozen agent-workbench runs init`。
2. 启动 API 和前端，进入 `/workspace/tasks`，选择正常调用、工具错误、调用超时或响应丢失。
3. 点击“开始复现”，查看真实状态事件与核验报告。正常调用应通过，工具错误应失败，发送后的超时/响应丢失应为结果未知。
4. “请求停止运行”只表示提交取消请求，实际终态与资源回收以报告为准。离页不会取消任务，断线后只查询同一请求。
5. 展开“在终端运行此场景”，下载配置并放到项目根目录，复制命令进行同场景新运行。它不是嵌入宿主 Shell，也不承诺重放旧记录的完整环境。

当前是本机单用户；网页复现与网页分析各有一个独立执行槽，允许各运行一项。旧请求重放不重复执行；数据库清理后的旧请求 ID 返回 410。进程异常后按上文显式 `runs reconcile`，不自动重跑不明状态的操作。
前端使用默认 3010、API 8030；更换前端端口时，API 启动前须指定 `AWB_FRONTEND_PORT`，只允许该端口的本机 Origin。`AWB_API_PORT` 配置 Next 构建代理目标。

P1-01a 验证的是受控合成故障的真实执行；不能从这些结果计算 Agent 修复成功率。CLI 模型协作见下方“模型分析与配置”；隔离补丁和冻结回归由下文P0-06修复CLI单独发起。
[实现契约与测试矩阵](22-web-reproduction.md) · [验收记录](23-p1-01a-delivery.md)

## 模型分析与配置

本批已通过工程验收、一次真实模型完整协作与独立代码复审。CLI 或网页 `/workspace/diagnosis` 发起分析；工具只允许项目审核的合成案例。`proposal_only` 是未应用、未执行验证的源码片段建议，不能当作已修复。

### 在网页里执行

1. 在 `/workspace/diagnosis` 选择案例和目标，点击“开始执行”。“仅检查”不需要模型；其他目标需要 API 进程已加载下方模型环境变量。
2. 控制台显示实际事件；“请求停止”后等待真实终态，不把请求已发送当作已停止。网页刷新只查询原任务，不自动重新执行。
3. 在结果中打开证据，查看问题路径、授权源码行和契约；ID 与摘要哈希放在技术详情中。
4. `proposal_only` 表示建议未应用。实际修复需要另行从修复页或下方修复 CLI 发起，不能据建议状态认为代码已修复。
5. 需要运行 MCP 场景时进入 `/workspace/tasks`；“工具错误”是主动注入的合成故障，报告同时保留真实调用失败与复现观察。它不是修复后的结果。

模型配置必须在启动 API **之前**加载到该进程环境；仅在另一个 CLI 终端配置不会改变正在运行的 API。修改配置后需重启 API。目录探测忙时网页有界重试 GET，不自动重试执行 POST。API 异常退出后可能显示“需要恢复对账”：先核对关联容器和运行，再对账分析；不自动续跑或释放未确认资源。

先显式初始化/升级本地记录（已有数据先备份；迁移保留已有运行与分析记录）：

```sh
uv run --frozen agent-workbench runs init
uv run --frozen agent-workbench analyze --case output_mismatch --intent check_only
uv run --frozen agent-workbench analyses list
```

`check_only` 不读取模型凭据、模型调用为0。模型分析使用现有兼容接口，环境配置：

| 环境变量 | 用途 |
|---|---|
| AWB_MODEL_BASE_URL | 自己授权的现有网关地址（可带 `/v1`） |
| AWB_MODEL_NAME | 审核profile支持 `deepseek-flash` / `deepseek-v4-1-flash` / `deepseek-v4-1-flash-260910` |
| AWB_MODEL_API_KEY | 仅进程环境提供；禁止写入源码、命令参数或截图 |
| AWB_MODEL_LIMIT_PROFILE | 显式配置 `deepseek-v4.1-flash-1m-v1`，表示接受官方上界到自己网关的条件性映射 |

不配置变量则不会请求模型；不下载或替换模型。真实分析向该网关发送审核合成证据、角色提示词和已授权四行源码片段，不发环境变量或本机其他文件。

```sh
uv run --frozen agent-workbench analyze --case output_mismatch --intent diagnose
uv run --frozen agent-workbench analyze --case output_mismatch --intent propose_repair
uv run --frozen agent-workbench analyses show <task-uuid>
# 中断后只对账并标记未知，不继续图或重发调用
uv run --frozen agent-workbench analyses reconcile <task-uuid>
```

需要T05实际复现时，须先按上面的容器步骤显式准备镜像。默认task总额度2,000,000（保守调用前预留）；单次输出诊断/验证4,096、修复8,192，最多12次模型/20次工具/2份建议/600秒。合法usage按实际结算；缺失或不合法usage保留预留并停止，费用始终未知。通过 `--total-tokens`、`--output-tokens`、`--model-calls`、`--tool-calls`、`--timeout` 收紧；预算小于provider完整上界时真实请求会拒发。

**测试顺序**：先跑 `uv run pytest tests/agents tests/test_analysis_store.py tests/test_controlled_resources.py tests/test_controlled_runtime.py`；再显式 `AWB_TOOL_DOCKER=1 uv run pytest tests/test_controlled_tools_docker.py`；最后配置自己的现有接口执行上述analyze并检查网页。fake测试只验证工程规则，真实模型结果单独记录，不等同修复成功率。

实际模型运行、工程测试、独立评审与能力边界见 [P0-05b 交付验收](25-p0-05b-delivery.md)。真实 SQLite/API/浏览器门禁：`cd frontend && npm run test:analyses-live`（独立临时数据库，不调用模型）。

## 隔离候选修复（P0-06）

CLI 支持两个固定审核案例：`output_mismatch`（返回类型错误）和 `counter_offset`（计数值偏移），使用 `--case` 明确选择。候选应用到隔离副本，由宿主冻结的行为测试比较基线与候选。不会修改用户项目，也不支持任意仓库。两案例同版本模型实验12次中11次通过、1次未完成，见[交付记录](31-p0-06-delivery.md)；没有跨项目效果结论。

在项目根目录，按上文初始化记录库与模型环境后，显式准备独立候选镜像：

```sh
uv run --frozen python containers/p0_06/prepare.py
uv run --frozen agent-workbench repair --case output_mismatch --mode single_role
# 相同案例、相同预算的三角色模式；这是另一次独立执行
uv run --frozen agent-workbench repair --case output_mismatch --mode three_role
# 另一个固定审核故障：计数值偏移
uv run --frozen agent-workbench repair --case counter_offset --mode three_role
```

结束后打开 `/workspace/repairs` 选择时间对应的修复记录，在「代码差异」标签查看具体差异，再切到「回归结果」标签。网页读取的是同一个 `AWB_STATE_DIR` 中的记录；CLI和API使用不同目录时，页面不会出现该次任务。

每次调用创建新任务，不续跑中断任务。单次输出上限8,192，任务上限12次模型、20次工具、2个候选、600秒；可用命令帮助中的选项收紧。退出0仅表示该候选通过固定验收集；退出2时查看安全结果码及待对账标记，不推断外部执行没有发生。

快照存放在状态目录的 `repair-snapshots`。使用 `AWB_STATE_DIR` 时应选择本机 Docker 已共享且没有软链接路径组件的独立目录；macOS 的 `/var` 软链接会被路径检查拒绝，系统临时目录也可能未共享给 Docker。不要为绕过错误而放宽快照或容器策略。

中断后先只读查看资源；确认需要清理时才执行第二条：

```sh
uv run --frozen agent-workbench repair-recovery --task-id <task-uuid>
uv run --frozen agent-workbench repair-recovery --task-id <task-uuid> --apply
```

`--apply` 会停止并删除该任务精确匹配的孤儿容器，包含仍在运行的容器；活owner存在时拒绝操作。此命令不续跑模型、不重新验证、不释放未知用量。退出0只表示资源及对账已确认，不表示修复成功。宿主死亡后没有自动硬截止回收承诺，需显式运行恢复。

详细边界、真实试验与分批评审见 [P0-06](26-p0-06-candidate-verification.md)。费用未配置仍显示未知；候选通过不等于已接受或已改写用户代码。

## 工程验收与常见问题

### 分开验证代码、真实工具和真实模型

- **默认 Python 门禁**：`uv run --frozen pytest`。未准备的真实容器和公开案例会跳过，跳过不算通过。
- **前端门禁**：按上方“前端测试”运行类型、生产构建与浏览器测试。
- **真实记录链路**：在 `frontend` 目录运行 `npm run test:runs-live` 与 `npm run test:analyses-live`。两个脚本使用独立临时数据库和测试服务，串行运行；测试构建使用专用代理端口，回到产品服务前重新执行默认 `npm run build`。
- **真实 MCP / Docker**：先显式准备审核过的源码与固定镜像，再打开本文对应测试开关。测试不会自动替你下载并执行未知仓库。
- **真实模型**：显式提供已授权接口后运行 `analyze`，核对实际摘要、用量与终态。fake adapter 测试不能代替模型效果验证，单次 `proposal_only` 也不是修复率。

具体可复现证据与独立评审见 [质量门禁](04-quality-gates.md)、[模型协作交付记录](25-p0-05b-delivery.md)。旧批次的测试计数是当时快照，不作为当前全量测试计数累加。

### 常见情况

| 情况 | 处理 |
|---|---|
| `failing.json` 的命令退出 1 | 预期结果：记录中的返回类型违反契约；查看 stdout 中的路径和规则代码。 |
| 网页列表为空 | 首次启动不会灌入示例任务。先在同一状态目录执行真实 CLI，或在「复现」页发起固定场景；再刷新。 |
| API 报数据库版本不兼容 | 停止写入，一致性备份后显式 `runs init`；GET 不自动升级。 |
| 改端口后网页请求被拒绝 | `AWB_FRONTEND_PORT` 配置 API 允许的精确本机 Origin；`AWB_API_PORT` 配置前端构建代理。两端一起改并重启。 |
| 模型未配置或输入上界未确认 | 核对“模型分析与配置”的四项环境配置；不要把密钥写入 CLI 参数。缺前置条件时系统不发请求。 |
| 用量或执行结果未知 | 保留未知状态与预留，按相应 `reconcile` 命令对账；不把未知当失败后自动重投。 |
| 浏览器测试找不到 Chromium | 安装锁定 Playwright 对应浏览器，或在本机显式选择已安装的 Chrome。 |

项目是可信本机、单用户工作台，尚非多租户服务。原始 trace、凭据和运行产物不提交；报告中的字段路径与摘要也应在分享前审核。全部文档见 [工程文档索引](README.md)，仓库见 [GitHub](https://github.com/ykduan0916/agent-repair-workbench)。


## 网页修复与本机常驻服务

修复页顶部选择内置案例（返回类型错误/计数值偏移）与单/三角色，然后开始。展开预算可查看上限；运行期间显示结构化事件与停止按钮。停止请求已登记不等于调用已结束；未知用量保留原预留。

候选生成后按以下顺序操作：

1. 在「代码差异」中核对具体改动，切到「回归结果」查看基线原错误、候选修复和正常路径的逐项结果。任务结束不代表候选通过。
2. 只有当前候选与回归证据核对一致，人工决定区才允许接受或拒绝。接受只保存决定，不修改项目文件。
3. 接受后点击「下载补丁」。服务端重新核对当前版本；浏览器核对下载字节摘要，版本变化时停止下载。下载后仍需在自己的项目中审阅、应用并运行项目测试。
4. 提交决定时断连，先刷新审批状态；按原请求重试会复用请求 ID，不重复创建决定。旧决定失效后仍保留历史，但不能据此继续下载。

刷新只查询已有请求。若显示“提交结果待确认”，先查询；明确点击“重新提交同一请求”才可能首次启动。不要清空会话存储后反复新建。已结束但资源未确认回收时继续占槽，按恢复说明处理。

macOS常驻使用当前用户LaunchAgent，前端3010、后端8030仅监听127.0.0.1。登录后自动运行，异常退出自动重启；注销/关机期间不可访问，开机登录后再启动，不承诺睡眠时继续计算。服务重启不会自动重放中断任务。

```bash
# 查看状态（不显示模型密钥）
.venv/bin/python ops/local_service.py status
# 停止两个本机服务；正常关闭等待已接收任务收尾
.venv/bin/python ops/local_service.py stop
# 更新代码后先完成依赖、显式迁移与前端生产构建，再启动
.venv/bin/python ops/local_service.py install
```

启动器读取本地`artifacts/local-service/model-env.json`（用户所有、权限0600），只接受指定AWB_MODEL配置；不放进Git、plist或前端。首次安装前需自行准备现有模型配置、`.venv`及`frontend/.next/BUILD_ID`，日志在同目录`api.log`、`web.log`；运维时自行归档日志，本版不包含自动轮转。不能把该文件复制进公开问题报告。

本机服务文件为`~/Library/LaunchAgents/io.agentrepair.local.api.plist`和`io.agentrepair.local.web.plist`。卸载时先运行stop，再删除这两个本项目的plist；不要删除运行账本或凭据来停止服务。更新前端必须先stop再build再install，避免在服务运行时覆盖`.next`。启用新版前按[迁移记录](39-p1-01-b3-web-controls.md)备份；带新数据不能直接降级到0007。

### 网页修复分层验收

默认测试不调用付费模型。工程门禁始终使用锁定工具，避免本机全局 Ruff 版本造成不同结果：

```bash
uv run --frozen ruff check .
uv run --frozen ruff format --check .
uv run --frozen mypy src
uv run --frozen pytest -o addopts='--import-mode=importlib' -q
```

真实容器门禁需要 Docker 运行、已准备的冻结镜像及镜像清单；分别检验两案例冻结回归、执行中取消和 owner 异常退出后的回收。它们使用确定性模型适配器或手写候选，不证明模型生成补丁的效果：

```bash
AWB_HELDOUT_DOCKER=1 uv run --frozen pytest tests/test_repair_case_docker.py -q
AWB_WEB_ANALYSIS_DOCKER=1 uv run --frozen pytest tests/test_web_analysis_docker.py -q
AWB_CANDIDATE_DOCKER_TEST=1 uv run --frozen pytest tests/test_candidate_docker.py::test_real_native_repeated_cancel_reclaims_actual_container -q
# 在项目根目录执行；镜像来源核验需要该绝对路径
AWB_REPAIR_RECOVERY_DOCKER=1 AWB_REPAIR_RECOVERY_IMAGE_ROOT="$PWD" uv run --frozen pytest tests/test_repair_recovery_docker.py -q
```

真实网页闭环另行显式执行，会消耗现有模型额度。需要已有本机私有配置、Docker、Chrome、Node 与前端依赖；不把密钥放到命令参数或浏览器里：

```bash
.venv/bin/python ops/b5_live_gate.py --execute
```

该命令使用独立端口 8038/3018、独立 SQLite/快照及 Next 生产构建目录，不改常驻服务主库。一次运行包含模型请求期间停止，以及两个案例各一次三角色修复、人工决定与补丁下载；不自动重试模型任务。每任务硬上限 12 次模型请求，三个任务合计上限 36 次；取消测试目标是在首个请求期间停止，实际调用数以账本为准。若端口被占用会拒绝启动。每次生成新的 `artifacts/b5-live/<attempt-id>/report.json`，记录成功/失败阶段、任务与精确资源核对；下载字节还会和本地隔离快照再次比较。原始产物只保留本机，不入 Git。报告为 failed 时先查该次原因，不删除证据后重复运行来获得通过数。完整验收范围与实际结果见[网页修复闭环验收](41-p1-01-b5-delivery.md)。

## 网页项目经验与 Codex 事件

在「环境与接入 → 经验与事件」新建项目和来源；事件入口、投递凭据、接收暂停、事件处理结果与内容外发授权在同一区域管理。凭据只用于对应来源的事件投递，不能编辑经验或运行代码。新事件生成的草稿在「项目经验」审核；编辑工具标识、适用版本和内容后显式确认。确认不等于允许外发：索引、检索及诊断/修复模型参考分别授权，并设置有效期。

Codex 插件安装方式、允许命令与本机私有配置见 [网页与 Hook 交付记录](48-m4-r-web-memory-hooks.md)。客户端必须自行审核并信任 Hook 定义；新会话加载插件后才能验证实际触发。未知退出结果保持未知，不能把收到事件等同于测试成功。配置凭据、relay 队列、实际事件与运行产物不提交 Git。

诊断或多角色修复开始前，可以展开「参考项目经验」选择项目和来源。默认不选择；单角色修复与仅检查模式不支持该选项。每次任务只检索一次，每次模型请求重新复核权限；验证角色不接收经验正文。运行详情可刷新查看引用当前状态、具体请求授权与检索/Agent 用量。候选通过本次冻结回归并被人工接受后，可以单独「保存经验草稿」，再到经验页审核。历史结果不代替下一次回归，保存草稿不自动授权或索引。

### 记忆功能测试的层次

- 锁定环境下运行 `uv run --frozen pytest -o addopts='--import-mode=importlib' -q`，验证领域、权限、幂等、预算、撤回竞态与原功能兼容；跳过的真实服务测试不能计为通过。
- 前端使用 `frontend/playwright.memory.config.ts` 验证管理与角色选项；真实管理测试必须显式设置 `AWB_MEMORY_LIVE=1` 并使用独立 SQLite/API，不能向常驻主库写测试资料。
- 真实引擎/模型验收入口为 `.venv/bin/python ops/m4r_live_gate.py --execute` 与 `.venv/bin/python ops/m3r_live_gate.py --execute`；使用现有私有模型、embedding 和 SDK 配置，M3 另需固定 Docker 镜像。命令会产生真实请求与费用，不是普通单测。每次独立目录保留成功及失败，不自动重跑未知请求。详见 [M3 实施与完整尝试记录](49-m3-r-agent-memory.md)。
- M3 的权限、角色隔离、闭环验收不等于记忆提高修复成功率；有无记忆对照由 M5-R 单独评估。
