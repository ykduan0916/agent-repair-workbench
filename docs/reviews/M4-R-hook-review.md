# 企业代码质量审计报告：M4-R Hook 适配器

- **审计对象**：`integrations/codex/plugin.json`、`integrations/codex/hooks/hooks.json`、`integrations/codex/hooks/relay.py`、`tests/test_memory_hook.py`、独立反例 `tests/test_memory_hook_review.py`。
- **审计版本**：2026-10-19 工作区，源码摘要见第 7 节；本报告不涵盖其他 M4-R/M3-R 改动。
- **技术栈**：Python 标准库、SQLite、HTTP、声明式 Codex 插件 Hook 配置。
- **业务场景**：General SaaS，可信本机单用户的可选事件采集。
- **审查严苛度**：Mission-Critical。
- **审计者**：独立子 Agent `m4_hook_review`；没有修改实现，只增加反例测试与本报告。
- **审计时间**：2026-10-19。

## 1. 代码质量评估卡

- **综合质量得分**：100 / 100（修复后代码范围，无遗留缺陷扣分）。
- **代码门禁**：Pass，所审适配器没有遗留 Defect P0/P1/P2。
- **平台接入上线结论**：Conditional Pass。安装增量已复核（第 8 节）；信任确认与真实 Codex 触发尚未验收，不能把本报告或模拟 Hook 输入写成“Codex 已连接”。
- **审计范围**：5 个文件，765 行。直接核对服务端 `web_memory/store.py` 和 `web_memory/api.py` 的认证、暂停及回执语义，未替代服务端专门评审。
- **未评估项**：宿主信任界面、平台版本的真实事件载荷与触发行为。这部分阻止整体平台接入取得无条件 Pass；不否定 HTTP 适配器代码测试结果。

| 遗留 P0 | 遗留 P1 | 遗留 P2 | 遗留 P3 |
| :---: | :---: | :---: | :---: |
| 0 | 0 | 0 | 0 |

```text
STRICTNESS:   Mission-Critical
SCENARIO:     General SaaS
DEDUCTIONS:   remaining findings 0 -> 0
RAW SCORE:    100
CAP APPLIED:  none
FINAL SCORE:  100 / 100
VERDICT:      code Pass; platform integration Conditional Pass
NOT ASSESSED: trusted Codex platform trigger
```

## 2. 生产事故风险推演

### H-01：暂停导致事件永远不再投递（Defect P1，已修复）

来源暂停后服务端返回 `409 connection_paused`。原实现把它与所有不可恢复 4xx 一同标为 `rejected`，恢复来源后也不再尝试。独立实际 HTTP 反例的修复前结果为 `pause_resume {'rejected': 1} requests 1`。影响单连接的待投递事件；当时只可见 rejected 计数，无法自动恢复。凭据轮换过程中收到 401 有同类影响。

修复后明确持久化 `paused` / `credential_required`，继续采用退避；修正配置或恢复来源后使用原事件 ID 和原正文重试。真正的 `idempotency_conflict` 不盲目重试。独立用例已经验证两种恢复路径。

### H-02：两个首次 Hook 同时初始化（Defect P1，已修复）

原实现先 `exists()` 再 `O_EXCL` 创建数据库。两个首次异步事件都看到文件不存在时，第二个创建者可能得到 `FileExistsError`，未把该事件加入持久队列。现捕获竞争创建后继续执行私有权限检查。独立 barrier 测试强制两个调用同时经历不存在检查，验证两个事件均持久化。

### H-03：成功事件积累使队列永久满（Defect P1，已修复）

原实现把全部 delivered 历史也计入 1000 条上限，持续正常使用最终停止接收。现新增事件前清理过旧 delivered，保留最近 100 条已投递记录；1000 条总上限保留对未投递/拒绝积压的背压。Codex 事件没有可靠源时间时明确 `occurred_at=0`，服务器另记接收时间，清理后重投的事件正文仍稳定。独立测试投递并标记超过 1000 条，再重投早期事件，确认容量恢复且正文一致。没有伪造真实发生时间。

## 3. 详细审计明细

### P0 — 阻断性缺陷

none。

### P1 — 严重缺陷

无遗留。H-01 / H-02 / H-03 已由实现者修复，并由独立测试复核。

- **位置与最小修复**：`relay.py:191–265` 对恢复型 HTTP 失败分类；`relay.py:141–148` 处理独占创建竞争；`relay.py:176–181` 清理已完成历史；`relay.py:131` 保持未知源时间的明确稳定编码。
- **审查维度**：并发、故障恢复、数据完整性。
- **置信度**：confirmed。
- **验证方式**：`test_recoverable_rejection_retries_identical_event`、`test_simultaneous_initialization_retains_both_events`、`test_delivered_retention_does_not_exhaust_queue_and_redelivery_is_stable`。

### P2 — 主要缺陷

无遗留；本轮追加修复了这些边界：

- `relay.py:210–255`：原 socket timeout 只限制无数据间隔；持续少量返回可突破整体 flush 时间。现 timer 对本次 socket 执行 shutdown，独立 slow-trickle 用例确认整体返回有界，事件保留 pending。
- `relay.py:244–247`：409 非对象 JSON 先判断类型，不再从 `.get()` 漏出 AttributeError。
- `relay.py:50–51`：endpoint 必须字符串。独立新增类型反例初跑 `3 failed, 28 passed`；修复后字典、列表、整数、空值均走固定错误码。
- `relay.py:3`：延迟解析类型注解，避免 macOS 系统 Python 3.9 在模块导入时因 `dict | None` 失败。实际 `/usr/bin/python3 --help` 成功；不据此声称全部系统 Python 版本均受支持。

### P3 — 次要缺陷

none。

## 4. 维度覆盖声明

| # | 维度 | 遗留发现 | 复核结论 |
|---|---|---|---|
| 1 | 架构与边界 | 0 | Hook 仅把元数据事件送至固定 loopback API，不执行测试、不读会话存档、不调用模型。 |
| 2 | SOLID 与现代设计 | 0 | 配置校验、规范化、持久队列、命令入口分离；3 类业务事件均经同一校验。 |
| 3 | 并发与资源安全 | 0 | 初始化竞争修复；事务不跨 HTTP；网络总截止时间、关闭连接及 timer 取消；事件允许至少一次投递。 |
| 4 | 容错 | 0 | 离线 pending、暂停/凭据可恢复；不可判定回执不报告 delivered；安全健康标记。 |
| 5 | 性能与伸缩性 | 0 | 输入 256 KiB、配置 16 KiB、回执 16 KiB、单轮最多 3 条、私有队列总量受限。 |
| 6 | 安全与合规 | 0 | 精确 cwd 与命令白名单；持久内容无命令/输出/路径；凭据只发127.0.0.1，不跟随302；私有文件拒绝软链接。 |
| 7 | 可观测性 | 0 | 队列分状态计数与固定错误 health.json；原始异常和凭据不入输出；平台连接状态需真实验收才可声明。 |
| 8 | 类型与契约 | 0 | 字段白名单、schema version、ID/时间/事件类型校验；Python 边界运行时检查有反例。 |
| 9 | 可测试性 | 0 | 独立加入 18 个用例；真实本机 HTTP 失败注入，无真实模型费用。 |
| 10 | 工程规范 | 0 | 锁定 Ruff check/format 通过；没有引入忽略项或改验收阈值。 |
| 11 | 数据完整性 | 0 | connection+event 主键、事务入队、原正文重试、终态防止被后来的失败响应覆盖；服务器仍承担权威幂等校验。 |
| 12 | 依赖治理 | 0 | 适配器只用标准库；插件信任不绕过、不自动启用；平台安装属于后续明确验收边界。 |

## 5. 整改清单与证据

| ID | 严重度 | 负责人 | 截止 | 状态 |
|---|---|---|---|---|
| H-01 | Defect P1 | root 实现者 | M4-R 代码门禁前 | fixed / 独立复核通过 |
| H-02 | Defect P1 | root 实现者 | M4-R 代码门禁前 | fixed / 独立复核通过 |
| H-03 | Defect P1 | root 实现者 | M4-R 代码门禁前 | fixed / 独立复核通过 |
| H-04～07 | Defect P2 | root 实现者 | M4-R 代码门禁前 | fixed / 截止时间、形状、类型、解释器兼容检查通过 |

最终独立执行：

```sh
uv run --frozen pytest tests/test_memory_hook.py tests/test_memory_hook_review.py -o addopts='--import-mode=importlib' -q
uv run --frozen ruff check tests/test_memory_hook.py tests/test_memory_hook_review.py integrations/codex/hooks/relay.py
uv run --frozen ruff format --check tests/test_memory_hook.py tests/test_memory_hook_review.py integrations/codex/hooks/relay.py
/usr/bin/python3 integrations/codex/hooks/relay.py --help
```

原始摘要输出：`31 passed in 7.82s`；`All checks passed!`；`3 files already formatted`；系统 Python 的 help 正常返回。独立测试文件的一处长行格式问题已用锁定格式器修正；没有改变测试逻辑。

## 6. 架构防腐与长效治理建议

1. 保持 HTTP 适配器契约测试与平台实际安装验收分栏。每次升级宿主插件格式重新记录真实事件字段、信任流程和停止语义；不能用手工 stdin 事件替代平台触发证据。
2. 保留恢复型失败、不可恢复冲突、未知回执三类测试；新增事件来源仍复用服务端权限和幂等协议，不能在 Hook 内扩充正文采集范围。
3. 接入指南明确状态命令、健康文件、凭据轮换、队列容量和未知源时间语义。满队列须先检查积压，不能通过静默丢弃 pending 解除容量限制。

## 7. 审计声明

本评审者没有启用插件、修改宿主信任配置、读取其他项目、读取会话存档、发送真实用户事件或调用模型。没有风险豁免。初审时安装/信任/真实触发未评估；第 8 节补充安装复核后，客户端信任和真实触发仍未评估，因此平台整体结论维持 Conditional Pass。

已审代码摘要（SHA-256）：

| 文件 | 摘要 |
|---|---|
| `integrations/codex/plugin.json` | `4ec04a96096a2b5624e9aec1dbcf031a2f6817d7ab85c97ffcbbc3f8ffb345e0` |
| `integrations/codex/hooks/hooks.json` | `0dbd14a56ac5ad7a0ae6235949aa0276e205cebc39d9a99723b8214cf9bd7a19` |
| `integrations/codex/hooks/relay.py` | `1419dd543eda926d0995741e1a884239a6b9dfd89d8ae2249e4f3f8e956d6257` |
| `tests/test_memory_hook.py` | `0e1c8127b332114895314ef307caa0e1079fff179f091b3a6f8967bc9b41009b` |
| `tests/test_memory_hook_review.py` | `3ae998060f1d4abaaa20c96a1e676bd8f37182d9ff4f369fcc813a3aa14c9118` |

其后实现变更须对增量复审。上表为初审摘要，部署变更后的 `hooks.json` 摘要以第 8 节为准。本报告不证明宿主插件已经被信任或实际触发。

## 8. 部署增量复审：本地 marketplace 与显式配置目录

2026-10-19，用户已明确批准本机安装/启用，同时保留客户端信任确认。实现者通过官方 CLI 安装 `agent-repair-events@agent-repair-local`；本评审者只读复核本仓库发布元数据以及已安装缓存的三个文件，没有执行安装、改配置或替用户信任 Hook。

**增量代码门禁：Pass；平台触发验收仍待完成。**

- `.agents/plugins/marketplace.json` 仅引用本仓库 `./integrations/codex`，安装策略为 `AVAILABLE`，没有自动强制安装或跳过信任的命令。
- 三个 Hook 命令明确传入 `--directory "${HOME}/.config/agent-repair/hooks"`；解释器、relay 路径、动作和目录构成固定 argv。路径双引号保留含空格目录语义；命令中没有外部载荷插值、凭据、内联测试执行或 trust bypass。
- `PostToolUse` 仍仅匹配 Bash 并调用 capture；SessionStart/Stop 仅 flush。relay 未变，精确 workspace/命令过滤、私有权限校验和凭据边界继续生效。
- 本机缓存 `~/.codex/plugins/cache/agent-repair-local/agent-repair-events/local` 中 `plugin.json`、`hooks/hooks.json`、`hooks/relay.py` 与当前发布文件逐字节相同。
- 显式全局目录只定位一个私有连接配置，不扩大允许采集的 workspace。用户切换项目时不会自动采集其他项目；配置/凭据创建及0700/0600权限由实现者随后验证，不能从已安装缓存推断已经配置完成。

独立 JSON/argv/缓存一致性检查结果：`deployment_metadata_and_installed_bytes: passed; trust_and_trigger: not_verified`。`git diff --check` 通过。仅修改部署声明，未改变 relay 或既有31项测试的执行逻辑，因此没有重复运行无关模型/全仓库测试。

| 部署增量文件 | SHA-256 |
|---|---|
| `.agents/plugins/marketplace.json` | `2c5d2f0e1a999a13c1e747a71666407679a1aa68fca0b2a8d88d5808a7dcf404` |
| `integrations/codex/hooks/hooks.json` | `f6c7fd25d8ab5d45d3ee2055d2d564be25fec5b3906b21249a4fad13d3d9e283` |

对外可表述“插件已安装，需在客户端审核信任并验证实际触发”；不可表述“已自动采集 Codex 会话”。平台信任、配置就绪、真实触发、API 接收、草稿处理是不同验收状态。

## 9. 实际平台触发证据追加复核

2026-10-19，独立评审者 `m4_engine_review` 追加只读复核；保留前述“当时未触发/待信任”的历史结论，不覆盖初审记录。本次没有调用模型、执行测试命令、启用插件或修改信任配置。

**本次限定平台链路：Pass（一个实际新会话事件）。** 证据 `artifacts/m4r/codex-trigger-f5df200f-2daa-4122-addf-49edaa426cf8/acceptance.json` 记录用户已确认信任、CLI exit=0、platform_event_verified=true、delivery=delivered、processing=done、当时生成一个 draft。

独立交叉核验而非只重复报告字段：

- 客户端日志具备 Codex banner 和本次 session 标识；仅在本机解析，没有在工具输出或文档打印日志正文、命令、session 标识或配置凭据。
- 将该 session 按 relay 当前算法计算摘要，与私有 outbox 中同 event_id 的 `source_run_id` 一致。
- 私有 outbox 该事件为 `delivered`、attempts=1；主库同 event_id 的事件为 `test_completed`，`memory_event_processing` 对应一行 `done`。
- 主库同来源事件关联一条经验；复核时已经为 `confirmed`，因此 acceptance 中 `drafts=1` 是生成时快照，不能说当前仍有一个未确认草稿。确认属于后续状态变更，不能据此声称 Hook 自动确认。
- acceptance.json 与 client.log 权限均为0600；评审只输出安全状态和布尔核验结果。

**业务结论边界**：该事件 `outcome=unknown`，原因 `no_recognized_structured_exit_code`。这证明经受信任的实际新 Codex 会话触发、过滤、持久投递、服务端处理及经验生成链路；不证明测试通过，也不保证当前平台事件中已经提供稳定退出码。relay 仍不得从输出文字或 CLI 整体 exit=0 推断测试成功。

本次只覆盖已配置 workspace/白名单中的一个新会话事件。安装之前已开启的会话、其他事件类型和其他宿主版本不能从该结果外推；前述标准库负向测试与暂停/轮换/容量测试继续适用。摘要身份也不等于匿名化。
