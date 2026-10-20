# M4-R：网页经验管理与外部事件接入

2026-10-19。Feature P1，状态 done（planned → ready → in_progress → in_review → done）。用户明确要求将本批提前到 M3-R 之前，先接通管理与事件，再接入产品 Agent。依据 44、46、47；不改变来源确认、用途授权、预算和冻结回归边界。

## 实施契约

- 新增 0013：来源凭据摘要和事件处理回执。升级显式执行并先备份；服务启动不自动迁移。
- 本机管理 API 位于 `/api/memory/projects`；写操作要求站点 Origin 与 `X-AWB-Local: 1`。项目、连接、经验、策略、用途授权分别显式操作，确认经验不自动授权外发。
- 外部 `POST /api/integrations/{connection_id}/events` 使用单连接 Bearer 凭据；只保存 SHA-256 摘要，创建/轮换时返回一次。凭据不可访问管理操作或指定其他项目。
- 凭据检查、暂停检查、事件去重与 outbox 提交在同一写事务；同 ID 同正文幂等，不同正文 409。接收和处理分别记录，接收不表示测试通过或经验已确认。
- 有界后台 worker 将外部事件转为仅含事件类型、来源版本未知、外部自报结果的草稿；处理和回执在同一事务。无模型调用，无任意命令执行，无自动确认。暂停阻止后续处理，重启可继续处理持久队列。
- 请求最大 32 KiB、读取 5 秒、事件正文受既有严格 schema 限制。所有错误为安全代码，不回显原始输入、凭据或异常。
- 管理页面提供可操作的确认、编辑、撤回、删除、用途授权；外部凭据不具备这些权限。物理索引清理与当前权威拒用分别显示。

## 验收门禁

凭据轮换/撤销/跨项目、Origin/内容类型/大小/重复字段、幂等冲突、暂停与并发、处理重启与草稿唯一性、显式授权、版本 CAS、正文删除、无凭据日志、迁移旧数据保留；浏览器实际 API 操作与独立评审另附交付证据。平台 Hook 真实安装覆盖与通用适配器契约测试分别记账。

## 本地事件适配器与 Codex

当前随源码提供 `integrations/codex` 插件包及标准库 relay；仅通过官方 CLI 管理插件，不手工改写信任配置，也不绕过 Hook 信任审核。验证本机 CLI 版本为 `0.163.0-alpha.10.2`。官方支持与本地安装成功分别记录：[Hook 事件与信任](https://learn.chatgpt.com/docs/hooks)、[插件打包](https://developers.openai.com/plugins/build/plugins)。客户端主动触发已独立验收，见文末；回放输入契约不计作平台触发。

网页在项目下创建事件来源并生成凭据。凭据仅显示一次，复制或下载到私有 `connection.json`；不要放进仓库。格式如下，token 为本机生成的来源凭据，不是模型密钥：

```json
{
  "endpoint": "http://127.0.0.1:8030",
  "connection_id": "my-tests",
  "token": "REPLACE_WITH_LOCAL_CONNECTION_CREDENTIAL",
  "workspace": "/absolute/path/to/your/project",
  "test_commands": ["uv run pytest"]
}
```

配置目录权限0700、文件0600。Codex手动安装源码中的插件后，在客户端检查并信任当前 Hook 定义；将配置保存到 `~/.config/agent-repair/hooks/connection.json`（当前打包命令的显式私有配置位置）。修改Hook后需要重新审核信任，不使用bypass参数。未完成配置/信任时页面不得称“Codex已连接”。独立适配器也可显式指定私有配置目录：

```bash
python3 integrations/codex/hooks/relay.py status --directory /absolute/private/directory
python3 integrations/codex/hooks/relay.py flush --directory /absolute/private/directory
```

- `PostToolUse`只识别当前精确workspace及显式列出的测试命令。仅上传事件类型、伪名化运行/调用ID和结构化退出结果；不保存命令、输出、正文、环境变量或会话存档。哈希身份不是匿名化。未知输出格式记unknown，不从“成功”文字猜测。
- Codex没有稳定源时间字段的输入记`occurred_at=0`，表示未知，接收时间由服务记录；同一session/tool调用重投保持完整事件一致。
- `SessionStart`、`Stop`只补送队列，不把停止当测试成功。异步观察不改变模型上下文、不阻断开发工具。
- 其他测试/CI、契约变更与用户纠正通过`emit --directory ...`从stdin提交规范的六字段事件：`schema_version,event_id,event_type,source_run_id,occurred_at,outcome`。支持`test_completed/contract_changed/user_correction`；这是调用方声明，不升格内部回归事实。适配器不执行测试命令，不读取任意报告路径。
- 私有SQLite队列最多1000条，已投递仅保留最近100条；服务端幂等账本继续阻止重复处理。单次最多发送3条，整体1.5秒deadline，网络错误保留pending，暂停与凭据问题分别可见并退避，修复配置后可续传；不可恢复输入或幂等冲突记rejected。
- 配置/队列满等问题写私有`health.json`的固定错误码，不写原始异常；后台Hook不向模型注入错误内容。可用status检查队列。未投递/拒绝不会被计入服务端“已处理”。

## 网页引擎操作补充

明确点击建立索引、查询或清理才创建持久请求。请求ID绑定完整操作摘要；重复提交返回原状态，不能以同ID变更目标。单活跃引擎操作由进程间锁约束；浏览器关闭不释放后台所有权。源内容与查询的外发仍须M2授权，确认草稿本身不授予外发。宿主使用现有DeepSeek和bge-m3配置，延续M2已声明的条件性token上限。

引擎操作保存job_id，失败仍可查看逐请求费用账本；未知账单不按零。宿主中断的running记录恢复为unknown，不自动重放。索引/查询每作业最多180秒，清理不调用模型。逻辑撤回即时阻止检索，物理清理需显式执行。网页持久表不保存查询正文和检索正文。

## 管理查询与容量反馈

列表采用每页100条的有界分页。`GET /api/memory/projects?page=0`返回`pagination`；项目overview接受独立的`connections_page/events_page/memories_page/grants_page/policies_page/jobs_page`，每组返回`page/has_more/next_page`，不会默默隐藏后续记录。页码范围0–100000；重复参数、未知字段和非法数字拒绝。分页为当前状态视图，并发新增/修改可能移动顺序，刷新后以资源ID和CAS版本进行写入，不把列表位置作为身份。

选中经验的`GET .../memories/{id}/context`提供该经验当前策略和所属来源的完整用途授权；`GET .../connections/{id}/grants`提供单来源全部授权（固定4种内容×2种用途，最多8条）。编辑授权和策略不依赖当前列表页猜测版本。

项目累计经验记录达到10000时，新接收事件保持持久队列并显示`capacity_limited`，不会误报处理成功。后台选择时先排除容量已满项目，再取最多20项，避免某项目的旧事件阻塞其他项目。删除保留审计头记录，因此不声称删除能释放累计记录额度；后续扩容/归档需单独维护策略。


## 本次验收记录（持续更新）

- 本机已通过官方CLI安装 `agent-repair-events@agent-repair-local`，来源为本仓库 `.agents/plugins/marketplace.json`。安装成功不等于Hook可信并已运行，客户端信任和平台实际触发单列验收。
- 真实HTTP引擎门禁：`uv run --frozen python ops/m4r_live_gate.py --execute`，首次运行通过。独立合成状态执行索引→查询得到1条exact来源→撤回→清理1个根。2次LLM、9次embedding、6360已知tokens，金额未知。报告`artifacts/m4r/44c48e54-3987-427d-b4a9-5def04b827e2/acceptance.json`。不计为真实项目效果或记忆收益。
- 适配器31项测试通过，独立评审修复暂停/凭据续传、首次建库竞争和成功历史队列满三项P1；没有绕过客户端信任。
- 网页实际API/SQLite操作与受控mock错误路径分开测试；容量、取消、分页及并发独立评审继续记录，下述最终门禁完成前不标done。

### 最终门禁

- Python 全量1781 passed/69 skipped/1已知fork警告；最终Ruff lint/483格式、mypy162、TS与生产构建通过。独立UI22项（含真实API/SQLite流程），5项专项回归关闭来源错显、分页统计和焦点问题。
- 独立评审：[权威层](reviews/M4-R-authority-review.md)、[引擎/取消](reviews/M4-R-engine-review.md)、[Hook](reviews/M4-R-hook-review.md)、[UI](reviews/M4-R-ui-review.md)，均无遗留P0/P1。
- 主库0012→0013显式备份迁移，34张旧表228行逐行摘要保持一致；首次迁移命令因尚在编写的0014相对导入被Alembic拒绝，数据库未变化，修正绝对导入后成功。报告`artifacts/m4r/migration-12093690-7a80-4c03-947b-adf47cf52c7f/acceptance.json`。
- 用户明确回复已审核并信任。新Codex CLI会话执行白名单测试命令后，真实PostToolUse产生1条delivered事件，服务处理done并生成1条draft。输入未提供认可的结构化退出码，outcome=unknown；不从工具结果文本猜成功。报告`artifacts/m4r/codex-trigger-f5df200f-2daa-4122-addf-49edaa426cf8/acceptance.json`。未捕获本次安装之前已打开会话的测试，不把该范围声称为覆盖。
- 本机配置位于`~/.config/agent-repair/hooks/connection.json`，0600/父目录0700，仅当前workspace与精确命令白名单；凭据不入库。项目agent-workbench、来源codex-local。自动采集不等于自动授权模型外发。
- 常驻API8030、Web3010已更新，经验页、站内指南与API均HTTP200；进入M3-R后按独立批次继续实施。
