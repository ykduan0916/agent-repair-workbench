# 企业代码质量审计报告

- **审计对象**: B5 首批 Ruff 历史基线修复：`repair_store/decision_models.py`、`repair_store/decisions.py`、`run_store/migrations/versions/v0010_decision_events.py`、`run_store/store.py`。
- **审计版本**: `31a5acf37aad048058052cfd52142917af0239bd` + 四文件工作树 diff；独立 reviewer，不参与生产实现。
- **技术栈**: Python 3.12 / uv / Ruff 0.17.0 / Pydantic / SQLAlchemy / Alembic。
- **业务场景**: General SaaS。
- **审查严苛度**: Mission-Critical。
- **审计时间**: 2026-10-19（Asia/Shanghai）。

## 1. 代码质量评估卡

- **综合质量得分**: **100 / 100** — 🟢 健康，仅对本次四文件格式增量。
- **上线评估结论**: **✅ Pass / 准予合入本次格式修复**。这不代表 B5 真实运行验收通过。
- **审计范围**: 4 files，29 additions / 31 deletions；全部为换行、括号与布局变化。
- **未评估项**: 本批之外的既有实现不做全面重审；B5 新运行器、网页验收与整体验收尚待增量复审，不能以本报告关闭 P1-01。

**缺陷汇总**

| P0 阻断 | P1 严重 | P2 主要 | P3 次要 |
| :---: | :---: | :---: | :---: |
| 0 | 0 | 0 | 0 |

**评分判定轨迹**

```text
STRICTNESS:   Mission-Critical
SCENARIO:     General SaaS
DEDUCTIONS:   none → Σ = 0
RAW SCORE:    100
CAP APPLIED:  none
FINAL SCORE:  100 / 100 (健康)
VERDICT:      Pass — 四文件 AST 不变，锁定工具的全仓检查通过
NOT ASSESSED: 四文件增量之外的既有实现；尚未提交审查的 B5 实现
```

**工具实证**

2026-10-19 reviewer 独立执行：

```text
command -v ruff
/opt/anaconda3/bin/ruff
ruff --version
ruff 0.12.0
uv run --frozen ruff --version
ruff 0.17.0
uv run --frozen ruff check .
All checks passed!
uv run --frozen ruff format --check .
416 files already formatted
```

`uv.lock` 锁定 `ruff==0.17.0`，CI `.github/workflows/checks.yml` 使用 `uv run --frozen`。全局 Anaconda 的 0.12.0 与项目版本不同，因此不能用裸 `ruff` 的格式结果替代门禁。依赖及锁文件未被修改，未放宽规则或添加忽略项。

额外对四文件分别读取 `git show HEAD:<path>` 与当前源码，以 `ast.dump(ast.parse(...), include_attributes=False)` 比较，输出四条 `AST identical`；没有语义变化。

```text
uv run --frozen pytest -q tests/test_b4_decisions.py tests/test_b4_api.py tests/test_b4_concurrency.py tests/test_web_repair_migration.py
...............                                                          [100%]
```

退出码 0，共 15 项。审阅时一次先行命令误用了不存在的历史测试文件名，退出码 4；上面的实际存在文件集已重新运行通过，不把错误命令算入通过数。

## 2. 生产事故风险推演

none。本次 AST 等价格式修复未引入运行时、迁移或资源行为变化。B5 整体风险必须结合真实运行链路另行审计。

## 3. 详细审计明细

### 🚨 P0 — 阻断性缺陷

none。

### ⚠️ P1 — 严重缺陷

none。

### ⚡ P2 — 主要缺陷

none。

### 💡 P3 — 次要缺陷

none。

## 4. 维度覆盖声明

| # | 维度 | 发现数 | 备注 |
|---|---|---|---|
| 1 | 架构与边界 | 0 | 无模块/import/调用边界变化 |
| 2 | SOLID 与现代设计 | 0 | AST 等价，无抽象或分支变化 |
| 3 | 并发、异步与资源安全 | 0 | 无锁、事务、线程或 await 语义变化 |
| 4 | 容错与防御性编程 | 0 | 校验/异常分支不变；15 项回归通过 |
| 5 | 性能、伸缩性与计算效率 | 0 | 查询及迭代行为不变 |
| 6 | 安全与数据合规 | 0 | 无新外发、密钥、路径或权限变化 |
| 7 | 可观测性与运维友好度 | 0 | 无日志/指标/错误契约变化 |
| 8 | 类型安全与契约表达 | 0 | 类型与 Pydantic 契约不变 |
| 9 | 可测试性与死代码 | 0 | 决定、并发、HTTP 与迁移定向回归通过 |
| 10 | 工程规范与一致性 | 0 | Ruff 锁定版本全仓 lint/format 均通过 |
| 11 | 数据与事务完整性 | 0 | 迁移仅格式变化，AST 等价，迁移回归通过 |
| 12 | 依赖与供应链治理 | 0 | uv.lock 不变；明确使用锁定 0.17.0 |

## 5. 整改清单

none（本次格式增量）。

## 6. 架构防腐与长效治理建议

1. **统一运行锁定工具** —— 继续使用 CI 已有的 `uv run --frozen ruff check .` / `format --check .`；成本零新依赖，防住开发机全局 Ruff 版本漂移。
2. **真实门禁分层列证据** —— B5 分别记录确定性、真实 Docker、真实模型、真实网页端到端命令与结果；成本为现有验收台账，防住 fake 浏览器测试被误报为真实模型成功。

### B5 验收必须保留的证据清单

以下是待实施/待验证清单，不是已通过记录：

| 契约 | 既有验证位置 | B5 交付需核对的证据 |
|---|---|---|
| W01 | `test_web_repair_store.py`、`test_web_repair_api.py`、`test_b2_api_review.py` | 首次请求、同参幂等、丢响应恢复，数据库一任务/一 claim，真实调用不重复 |
| W02 | store/API/review 负向测试 | 409/410/伪造 claim 明确拒绝，零新增调用 |
| W03 | `test_web_repair_processes.py` | 争槽、SIGSTOP/SIGKILL 不重放；真实 Docker recovery 测试独立开启后才算实测 |
| W04 | `test_web_repair_execution.py`、API preflight 测试 | 缺模型/镜像配置拒绝发生在调用前，未产生模型及容器 |
| W05 | execution 取消测试、`test_repair_recovery_docker.py` | 调用前/模型中/T05/T07取消，未知预留不退款、精准回收；区分 fake 测试与当前真实 Docker 证据 |
| W06 | B3 browser、pending 前端测试 | 生产 Next 刷新/断线恢复同一任务，无自动 POST，新调用计数不增长 |
| W07 | `test_web_repair_migration.py`、旧 CLI 回归 | 主库升级已有交付记录，当前增量跑回归，不再次为验收迁移用户主库 |
| W08 | `test_b4_decisions.py` | 版本/摘要篡改不能接受或下载，历史决定不改写 |
| W09 | `test_b4_concurrency.py`、决定 store 测试 | CAS 唯一结果、旧 request 幂等、冲突在 UI 可见 |
| W10 | B4 API/store/前端下载测试 | 生产浏览器下载 bytes 与服务端 diff 精确匹配；SHA-256/文件名/任务候选关联一致 |
| W11 | repair 浏览器测试 | 390/1440、浅深色、键盘、错误/未知恢复、停止可发现；真实页无大段无意义 ID |
| W12 | **尚需 B5 新增真实门禁** | 当前网关、两个固定案例、真实 Docker、生产 Next/API/SQLite、人工决定与下载；保留全部尝试/失败/不可判定，不自动写回 |

现有测试的信任边界：

- `tests/test_b3_browser_live.py` 自述 **only model/Docker are fake**；真实 HTTP/SQLite，不是真实模型或 Docker。
- `tests/test_repair_case_docker.py` 自述 **hand-authored patches, no LLM**；覆盖两案例 T05/T07 与故意破坏回归候选，但不是模型修复效果。
- `tests/agents/test_repair_tool_bridge_docker.py` 执行真实五容器与正负候选，仍为手写候选；不能据此报告模型能力。
- `tests/test_repair_recovery_docker.py` 必须显式 `AWB_REPAIR_RECOVERY_DOCKER=1` 与正确镜像根目录，真实 host-death cleanup；默认跳过不算验证。
- CI 未开启真实网关/Docker门禁；当前 CI 成功不能替代本机真实验收。凭据只进运行环境，原始模型参数/响应不能写入公开报告。

## 7. 审计声明

- 已审计版本：`31a5acf37aad048058052cfd52142917af0239bd` + 本报告限定四文件 diff；改变这四文件需重新审计增量。
- 未评估：B5 后续新增实现及完整真实验收。**仅四文件格式修复 Pass；B5 整体当前不得据此标 done。**
- 已施加豁免：none。
- 置信度：本次无 suspected 发现；工具版本、AST 等价和测试结果均由独立 reviewer 实际执行。


---

# B5 第二批独立增量复审：真实验收运行器与网页门禁

- 审计对象：`ops/b5_live_gate.py`、`tests/test_b5_live_gate.py`、`frontend/playwright.b5.config.ts`、`frontend/tests/b5-real-repair.spec.ts`、`frontend/next.config.ts`，以及本批紧凑操作路径、站内指南和能力文案。
- 基线：B4 `31a5acf37aad048058052cfd52142917af0239bd` + B5 工作树；本 reviewer 没有参与生产实现，也没有执行真实模型调用。
- 场景：General SaaS，本机可信单用户显式验收脚本；严苛度：Mission-Critical。
- 当前结论仅限代码可运行门禁，真实闭环结果另列，不把测试代码本身视为真实运行证据。

## 1. 代码质量评估卡

**代码增量 100 / 100，Pass，可执行一次显式真实门禁；B5 整体仍等待真实运行及产物复核。** 无未解决 confirmed Defect P0/P1/P2/P3，100 − 0 = 100，无封顶或豁免。

2026-10-19 独立 reviewer 实际运行：

```text
uv run --frozen pytest -q tests/test_b5_live_gate.py
........................                                                 [100%]
uv run --frozen ruff check .
All checks passed!
uv run --frozen ruff format --check .
420 files already formatted
cd frontend && npm run typecheck
> tsc --noEmit
exit 0
git diff --check
exit 0
```

24 项运行器测试包含三个真实父退出、子进程继续存活的进程组回归；这些真实子进程测试不消耗模型、不运行 Docker，不能计为模型或容器验收。

## 2. 生产事故风险推演

本轮发现并修复下列具体失败路径：

1. 首版 SIGKILL 后的二次 `wait()` 再次超时，异常会跳出整个 finally，阻止另一服务停止、数据库/容器核对及报告保存。改为固定 `unconfirmed`，并将每个资源清理独立保护；未知清理导致门禁失败。
2. 首版 build/browser 使用 `subprocess.run`，超时仅停止父进程；后续初修虽然建组，父进程已退出仍立即返回 `already_exited`。独立反例确认该返回时 sleep 子进程仍活。现已用独立 session、完整组探测、TERM/KILL 升级及组消失确认；真实三组回归通过。
3. 独立复跑曾观察 macOS 的已终止孤儿组探测短暂返回 EPERM：14 项通过、1 项失败，清理仍为 `unconfirmed`，没有误报通过。最终实现对 EPERM 通过 `/bin/ps` 严格解析精确 PGID 和同 UID 核对；命令失败、空输出、坏格式、外 UID 均仍失败，不能把 EPERM 直接当作不存在。修复后 24 项通过。
4. Next 隔离构建会改写 tracked `next-env.d.ts` 和 `tsconfig.json`。现已保存两个文件的原始字节，在服务停止后逐文件恢复；其中一个恢复失败不阻止另一个和资源 inventory，且结果保持 failed。

上述问题在任何真实模型 gate 执行前处理，未以重新运行模型掩盖失败。

## 3. 详细审计明细

当前无未解决 Defect P0/P1/P2/P3。整改历史为上节四项，均经代码复核和定向回归。

门禁的 pass 不能仅由 Playwright exit 0 推导：还要求精确三任务、两案例 verified/一任务 cancelled、无活动 receipt/pending operation、精确容器不存在、服务及子组可确认停止、Next 文件恢复、下载字节与本地 immutable snapshot 再次交叉核对。任何未知项失败。

## 4. 维度覆盖声明

| 维度 | 覆盖与边界 |
|---|---|
| 架构与边界 | 单次显式运维 gate；领域执行、权限、模型解析复用现有模块，没有复制角色实现 |
| SOLID 与设计 | 准备、构建、服务、浏览器、资源核对、报告分开；不新增通用平台 |
| 并发与资源 | 独立进程组；精确 PGID/UID 清理；cleanup 失败互不遮蔽；3 个真实孤儿回归 |
| 容错 | 超时、进程早退、端口占用、单资源清理/文件恢复失败、未知 inventory 保持 failed |
| 性能与边界 | 固定端口、3 任务、有限预算/轮询/时间，browser workers=1/retries=0，不重复模型写请求 |
| 安全合规 | 私有配置仅 API 环境；build/browser 不继承 AWB_MODEL_*；产物本机私有，不输出 URL/key/模型响应 |
| 可观测 | 每次 UUID 独立目录，成功与失败均保留；结构化固定错误码、任务/用量/资源核对 |
| 类型契约 | TypeScript 通过；已有 Config、Task、RepairReader 和 snapshot 契约复用 |
| 可测试性 | 24 项运行器失败语义测试；生产 Playwright 无 mock；单测不冒充真实模型效果 |
| 工程规范 | 锁定 Ruff 全仓 lint/format、typecheck、diff check通过；无忽略规则放宽 |
| 数据事务 | 独立 SQLite/state，主库不迁移；原下载和 immutable diff 字节核对，审批/拒绝均通过真实 API |
| 供应链 | uv/npm 锁未新增依赖；镜像经现有审核链校验；非任意镜像/项目执行器 |

## 5. 整改清单

上述清理、进程组和构建文件恢复缺陷已修复。真实验收尚未进行，属于待执行门禁，不是代码缺陷豁免。

## 6. 架构防腐与长效治理建议

继续保留全部尝试，不把浏览器失败、模型失败或不可判定清理删除后再采样为成功率。两例成功只能证明当前固定案例闭环，不能推广为任意仓库或多角色效果。

## 7. 审计声明

本节签署时关键快照 SHA-256：

| 文件 | SHA-256 |
|---|---|
| ops/b5_live_gate.py | `031d52ce974e967e6d0d3453a8aa935ebc338ee88b239621476e349701eaf973` |
| tests/test_b5_live_gate.py | `9245ea310b7ef606636a825aedf4134c1d2806dc8eaf73d3f0328cfc7f1b1124` |
| frontend/playwright.b5.config.ts | `e516281ce505f445340aa059c691edce4d9bc88f415d5edb6095e219f31a4e33` |
| frontend/tests/b5-real-repair.spec.ts | `8815b487c919cb1939704b4c880bbb21424a7ac91c179517b2083f314dc16f36` |
| frontend/next.config.ts | `779ec44913f542d9bafe9d00076cb6a23112f1ae3691ba19a10d961d31ed124f` |

后续安全模型配置标识等增量必须复审；真实产物尚未审阅，暂不能用本节宣告 B5 done。没有审计豁免。

---

# B5 第三批独立验收复核（进行中）

2026-10-19，新的独立 reviewer 完整阅读上述运行器、浏览器门禁、24 项单测与紧凑 UI 增量，并复跑 `uv run --frozen pytest -q tests/test_b5_live_gate.py`：24 passed。五个文件的 SHA-256 与第二批签署值逐一一致。reviewer 未调用真实模型；以下来自主执行者保留的本机产物与 reviewer 对代码、私库安全投影的独立核对。

## 1. 代码质量评估卡

第二批代码门禁结论保留为历史结论。新发现的取消门禁契约和预算文案已最小修正并复审；当前代码门禁 **Pass，100 − 0 = 100 / 100，无未解决 confirmed P0/P1/P2/P3，无豁免**，允许执行修正后的最后一次显式真实门禁。**真实闭环结果仍待复核，B5 暂不得关闭。** 之前失败与产物继续保留，不以抽取成功样本替代全部尝试。

## 2. 事故与验收失败推演

- `35dd5a2a-ff0c-4801-95b8-0150c7570b7a`：三次提交均在接收前返回 503，`image_unavailable`；最终 `inventory_unavailable`。报告为 failed，两个服务确认 terminated、build/browser 子组确认退出、两个 Next 文件恢复。此轮不计为模型效果样本。
- `3054def7-1812-4771-952b-683060d7878a`：三任务已落库且全部 inactive，无 pending operation，精确容器全部不存在；模型取消任务保留未知用量，返回值类型案例 `INVALID_MODEL_OUTPUT`，计数案例 `candidate_verified` 但浏览器审阅链路未完成。不能据一个 candidate_verified 宣称两案例网页闭环通过。

## 3. 详细审计明细

### Defect P1 — 已有取消契约被错误的验收断言拒绝（已修正并复审）

- 位置：`frontend/tests/b5-real-repair.spec.ts` 的模型 pending 取消用例与 `ops/b5_live_gate.py::verify_browser_evidence` 的取消任务 outcome 核对。
- 维度：9 可测试性与契约强度；置信度 `[confirmed]`。
- 机制：`repair_runtime/controller.py::_model` 在取消时结算无 token 用量的 reply；`repair_store/store.py::_finish` 将其定为 `finished / outcome_unknown / MODEL_USAGE_UNKNOWN`，保留预留；后续 `_stop` 不覆写既有终态。门禁却强制 `cancelled` 并期待 `MODEL_CANCELLED` 公开事件，与落库契约不符。
- 最小修复：本场景要求取消前确实 model_pending、显式取消已登记，最终 inactive、`outcome_unknown / MODEL_USAGE_UNKNOWN`，预留及未知用量保持、调用不增长；公开事件要求 `model / settled / outcome_unknown / MODEL_USAGE_UNKNOWN`，刷新后无重放。不要改生产结算以迎合测试，也不要无条件接受任意终态。
- 验证：补无模型契约断言；再次真实运行仍需保存全部原失败。独立读取第二轮私库 `public_events` 已确认 revision 1 为 model/reserved/pending，revision 2 为 model/settled/outcome_unknown/MODEL_USAGE_UNKNOWN，无后续调用事件。

### Defect P2 — 取消任务预算说明超出实际硬限制（已修正并复审）

- `docs/27-local-development-guide.md` 宣称取消案例最多增加 1 次模型请求，但 gate 轮询只要求 `model_calls >= 1`，每任务硬上限仍为 12 次。应将预先预算写为三任务最多 36 次；当次实际 1 次可独立报告，不把观察值当成硬限制。

修正复核：spec 与 runner 均已核对 `outcome_unknown / MODEL_USAGE_UNKNOWN`；spec 还核对显式取消、未知用量、取消前后预留相等、调用不增长与刷新无重放。文档已写三任务硬上限 36 次。reviewer 独立再次执行 24 项运行器测试、全仓 Ruff check/format（420 files）、TypeScript，全部通过。

真实计数案例网页详情曾遇到只读并发槽 429。`frontend/lib/repairs.ts::readRepairJSON` 新增仅 GET 的有界退避（250/500/1000 ms，最多 4 次请求），用于列表、详情和 diff 三个读取入口；取消会清理等待，非 429 立即失败。所有写操作不经过本 helper。reviewer 已追踪调用方及共享 readJSON，未发现额外写重试或无界等待；恢复/持续繁忙的两项浏览器回归由主执行者运行，结果另列。

## 4. 维度覆盖声明

沿用第二批十二维覆盖，新增核对公开取消事件、完整真实尝试保留、资源 inventory 与模型配置摘要。三任务的 receipt/持久化任务包含 `model_config_sha256`；其来源绑定 transport config、角色提示词和 schema，不需要公开密钥或 URL。

## 5. 整改清单

| ID | 级别 | 负责人 | 截止 | 状态 |
|---|---|---|---|---|
| B5-R3-01 | Defect P1 | B5 主实现者 | B5 done 前 | 已修正并独立复审 |
| B5-R3-02 | Defect P2 | B5 主实现者 | B5 done 前 | 已修正并独立复审 |

## 6. 长效措施

1. 用既有持久化状态机契约校验端到端断言，保留取消与远端用量未知的区分。
2. 同一验收记录中保留接收失败、模型失败及 UI 失败；固定两案例不推导通用修复率。

## 7. 审计声明

本节代码增量 Pass，真实整体验收尚待产物复核；没有未验证的生产缺陷推断。仅上述评审报告由 reviewer 编辑，生产代码与测试由实现者负责。修正后关键文件 SHA-256 如下，未列出的第二批关键文件保持原签署哈希：

| 文件 | SHA-256 |
|---|---|
| ops/b5_live_gate.py | `826a5447028c9f5c9ddc24129fb59161c05ac216a3892f419e93ee86bed0d424` |
| frontend/tests/b5-real-repair.spec.ts | `eceefcf818305b4142a1cf226c151676bdd6bae9c0cc44fbab1032e2e50d2461` |
| frontend/lib/repairs.ts | `95a68508c84ed302287aa4ccd490cbe0699e2b2b976d98378828d918c0cfead7` |
| frontend/tests/repairs.spec.ts | `72adaece81401a4b0e73917ef7b2132d59efc8fcd123f262e3ee98566f814b6e` |

---

# B5 最终独立代码与真实产物复核

日期：2026-10-19。对象仍为上述 B5 增量，基线 B4 `31a5acf`；reviewer 不参与实现、不调用真实模型，只读核对现有产物、持久化账本、不可变快照与 Docker 精确资源状态。

## 1. 代码质量评估卡

**Pass，100 / 100，允许关闭 B5 集成门禁。** 当前 confirmed Defect P0/P1/P2/P3 均为 0；100 − 0 = 100，无封顶、无豁免。该分数仅评价本次代码与验收完整性，不是模型效果或整产品商业可靠性评分。B5-R3-01、B5-R3-02 已修正并独立复审；进程组清理等第二批发现均已关闭。

独立复核最后一次真实门禁 `artifacts/b5-live/8170c202-32b4-4083-b761-69d380cada85/`：

- `browser-results.json`：expected 3、skipped 0、unexpected 0、flaky 0，52.76085 秒。
- 重新调用只读 `inventory`，确认 3 个任务 inactive、无 pending operation，10 个精确容器全部不存在。
- 重新运行 `verify_browser_evidence`，以及分别以 `RepairReader.diff` 读取不可变快照字节，与两个下载文件逐字节、SHA-256 比较：均一致，长度分别 478 / 389 字节。
- 两案例均为 candidate_verified；每个 comparison 的原故障基线 failed、正常基线 passed、候选故障与正常路径 passed，四个 slot 均 cleanup removed；人工决定历史都是 accepted → rejected。
- 取消任务从 model_pending 显式停止，最终 outcome_unknown / MODEL_USAGE_UNKNOWN，预留 1,056,768 未退；仅 1 次模型调用、0 次工具调用，刷新无重放。
- 三任务 `model_config_sha256` 一致为 `638e54f42cc3bb03fc21c0bfa164c281c103fc169c0456676021de0f01a6f926`，两个已完成修复分别 7 / 5 次模型调用；与浏览器 evidence、数据库、最终报告一致。
- 所有关键代码哈希仍与第三批修正后签署值一致。实际截图已查看：保留中性工程布局、紧凑操作路径、站内指南、候选差异及人工决定，无新装饰大卡片。

主执行者的完整门禁结果与命令已对照 [41 交付记录](../41-p1-01-b5-delivery.md)：1589 Python passed / 67 skipped / 1 warning；真实 Docker 10 + 3 + 1 + 2 项；同源 3010 前端 42 passed，包含 429 恢复/持续繁忙、浅深主题及窄屏。reviewer 独立重复的工具为 24 项运行器测试、Ruff check/420 文件 format、TypeScript、diff check；不冒称独立重复了主执行者全部套件。

## 2. 生产事故风险推演

没有未解决 confirmed P0/P1。已有失败完整保留：首轮环境预检拒绝、第二轮真实模型输出不合规与 UI 429、旧取消断言错误、前端 3017/3010 Origin 配置误用。首轮 Docker 瞬时预检失败根因尚未确认；系统拒绝接收，未调用模型，不把后续通过解释成该环境风险不存在。

## 3. 详细审计明细

未解决 Defect P0/P1/P2/P3：none。第三批修正未降低冻结回归或候选通过标准；取消预期以现有状态机契约为依据。UI 只读 GET 退避有界、可取消，未增加自动写请求重试。

累计效果必须保持交付记录中的口径：4 次真实修复尝试中 3 次 candidate_verified、1 次 inconclusive；两次模型期间取消另列；首轮 3 次接收前拒绝不计为模型样本。这是两个固定合成案例的集成验收，不是任意仓库修复率，也不证明三角色优于单角色。

## 4. 维度覆盖声明

沿用第二批完整十二维覆盖与第三批新增验证。新增最终证据补齐资源安全、可观测性、模型配置绑定、测试有效性、数据与下载完整性、UI 真实可操作性。没有新增依赖或数据库迁移，不扩大用户代码执行及外发边界。供应链已知漏洞全量扫描、跨平台部署、多租户、真实用户项目推广效果未评估，均不属于本次本地固定案例 B5 门禁。

## 5. 整改清单

none。此前列出的 B5-R3-01 / B5-R3-02 均 closed，无缺陷豁免。完成状态与站内静态文档需由主执行者同步后重建，并最终核对常驻服务健康。

## 6. 长效措施

1. 保留失败和成功的独立目录，以及模型配置/用量/容器 inventory；后续不得以最终一次通过掩盖完整尝试分布。
2. 发布前继续使用锁定 `uv run --frozen` 工具与真实生产网页闭环；共享只读槽繁忙只重试读取，不重放写操作。

## 7. 审计声明

代码签署依据为第三批 SHA-256 表及第二批未变文件。最终复核通过的是 B5 指定范围；不是部署到公网或自动写回代码的授权。历史阶段的“待复核”文字保留为当时记录，当前结论以本节为准。reviewer 只编辑本报告，未改变实现、测试或真实产物。
