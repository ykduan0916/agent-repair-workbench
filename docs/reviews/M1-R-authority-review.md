# 企业代码质量审计报告

- **审计对象**: M1-R `experience_memory`、0011迁移、RunStore兼容增量、记忆测试及前端边界说明。
- **审计版本**: 基础HEAD `67b50d056213c00c1350c81c47de779d05b20866` 上的未提交工作树；最终文件SHA-256见第7节。
- **技术栈**: Python 3.12、Pydantic、SQLAlchemy、SQLite、Alembic；Next.js/React。
- **业务场景**: General SaaS（本地可信单用户领域层）。
- **审查严苛度**: Mission-Critical。
- **审计时间**: 2026-10-19，Asia/Shanghai。
- **评审者**: 独立专职 reviewer `/root/m1r_review`；未修改实现或测试，仅提交本报告。

---

## 1. 代码质量评估卡

- **综合质量得分**: **100 / 100** — 🟢 健康，指修复后的本批范围。
- **上线评估结论**: **✅ Pass / M1-R限定范围通过**。可交付权威领域层、显式迁移和前端说明；不代表Cognee生产worker、HTTP/Hook、经验召回或Agent注入已上线。
- **审计范围**: 后端实现、迁移及测试约1,550行，另审RunStore/旧迁移测试和前端说明的少量增量，合计低于约2,000行；按后端实现、测试、前端声明三批阅读。未混入工作树中的REL-01/02等其他里程碑改动。
- **未评估项**: 本批必需范围内none。M2-R引擎实际处理/来源授权用途/费用上界、M3-R内部回归绑定、M4-R签名与HTTP入口、M5-R效果对照属于明确排除的未来范围。MR01-SC-01依赖残余仍由M2-R跟踪，本批没有新增引擎依赖。

**缺陷汇总（开放项）**

| P0 阻断 | P1 严重 | P2 主要 | P3 次要 |
| :---: | :---: | :---: | :---: |
| 0 | 0 | 0 | 0 |

初审发现1项Defect P1、1项Defect P2，均由实现者修复，独立复审关闭。初审得分为`100−8−3=89`，存在P1封顶79；最终按开放缺陷重新计算，不把已修复历史问题当残余风险。

**评分判定轨迹**

```
STRICTNESS:   Mission-Critical
SCENARIO:     General SaaS
DEDUCTIONS:   P0×0, P1×0, P2×0, P3×0 → Σ = 0
RAW SCORE:    100
CAP APPLIED:  none
FINAL SCORE:  100 / 100 (Healthy)
VERDICT:      Pass — 本批无开放缺陷；初审P1/P2均已复审关闭
NOT ASSESSED: none within M1-R; future M2-R/M3-R/M4-R/M5-R excluded
```

**验证证据与来源**

评审者亲自执行：

- `uv run --frozen pytest tests/test_memory_authority.py tests/test_memory_jobs.py`：初审`37 passed in 2.51s`；两项修复后`45 passed in 2.06s`；最终加入只读`event_for_job`及其测试后`46 passed in 2.03s`。
- `uv run --frozen pytest tests/test_run_store.py tests/test_run_store_concurrency.py tests/test_web_repair_migration.py`：`39 passed in 3.22s`。
- 定向Ruff：`All checks passed!`；`uv run --frozen mypy src/agent_workbench/experience_memory`：`Success: no issues found in 5 source files`。
- 在临时真实SQLite以纯合成输入复现清理预留问题；未发送任何provider请求。
- 阅读修复后的前端能力声明、说明组件、文档入口、更新记录和浏览器测试；查看390px浅/深色截图，两个状态均为待开发，文案可读且无操作按钮。

实现者运行、评审者读取产物核对：

- `artifacts/m1r/full-tests.log`：初轮`1 failed, 1660 passed, 67 skipped, 1 warning in 218.79s`。旧0006迁移测试把新增9张空表计为旧数据变化；修正仅排除这9张新表，所有旧表逐行比较与非空回退断言仍保留。
- `artifacts/m1r/full-tests-final.log`：`1669 passed, 67 skipped, 1 warning in 224.43s`。warning为既有fork测试的多线程进程弃用提示。此完整结果在最后的只读`event_for_job`增量之前取得；最后增量由上述46项定向测试、Ruff、mypy覆盖，不宣称完整1670项已重跑。
- `artifacts/m1r/frontend-build.log`：生产构建成功。`frontend-browser.log`与`frontend-browser-final.log`均保留13通过/2失败的定位阶段；新测试最初对带sr-only说明的能力徽标作了错误的exact-text计数。最终明确分别检查`data-capability=planned`和外部事件徽标，没有删除状态、无按钮、无写请求、axe或溢出断言。
- `artifacts/m1r/frontend-browser-verified.log`：`15 passed (23.5s)`，覆盖文档阅读、390px浅/深色说明、axe、站内跳转与导航。截图为`memory-light.png`、`memory-dark.png`。
- `artifacts/m1r/migration.json`：0010→0011，备份路径保留，20张旧表183行的before/after摘要逐项一致；9张新表全空。该运行是实现者按46执行的主库停服、备份和显式迁移，评审者未执行主库迁移。
- `artifacts/m1r/acceptance.json`：临时SQLite端到端事件持久化、重开后的删除receipt、投影1→0、正文清空、连接暂停时清理完成；100预留/20结算明确标为模拟账本数值，不是provider/Cognee调用成本。
- `artifacts/m1r/services.json`：三个catalog可用且无活跃任务；运行、分析、修复历史读取均HTTP 200；API/Web进程PPID均为1。不是只用health端点推断旧库可读。

---

## 2. 生产事故风险推演

以下为已修复的初审问题，不是当前开放风险。

> **场景1 — 暂停连接的清理任务仍获得模型额度**
> **触发条件**: 一条经验删除后，宿主暂停其来源连接，再领取cleanup任务并请求LLM或embedding预留。一次调用即可触发。
> **失效机制**: `jobs.py`的`_current`为保证清理继续而跳过连接enabled；原`reserve_call`复用了该检查，未额外禁止cleanup模型调用。
> **用户可见症状**: 账本返回`admitted=True`。当前M1-R没有HTTP发送器，因此未发生实际外发；未来worker若信任该返回值，会违反暂停后不再准入模型请求的契约。
> **爆炸半径**: 单个清理作业，受单作业预算限制。
> **可检测性**: 初审现有测试未覆盖；独立临时SQLite探针复现。新增四种paused×kind负向测试已覆盖。

---

## 3. 详细审计明细

### 🚨 P0 — 阻断性缺陷

none。

### ⚠️ P1 — 严重缺陷

#### M1R-P1-01. 清理作业绕过暂停连接的模型准入限制（已关闭）

- **位置**: `src/agent_workbench/experience_memory/jobs.py:16–28` `_current`；最终`reserve_call:185–190`（原审计快照为166–168）。
- **审查维度**: 6.安全与数据合规；11.数据与事务完整性。
- **置信度**: `[confirmed]`。
- **问题分析**: cleanup可在连接暂停时领取是正确语义，但原模型预留也直接复用此权限，导致已删除/暂停来源获得新的外发额度，违反46第2/3节。独立探针原样输出：

```text
{'kind': 'cleanup', 'connection_enabled': False, 'memory_state': 'deleted', 'admitted': True, 'reserved_tokens': 100}
```

- **修复补丁**: 实现者已落地最小修复，在同一预留事务中拒绝cleanup，并独立检查连接启用状态：

```diff
 job = self._owner(c, project_id, lease)
+if job["kind"] == "cleanup":
+    raise StoreError("cleanup_model_forbidden")
+connection(c, project_id, job["connection_id"])
```

- **验证方式**: `tests/test_memory_jobs.py:225–238`，LLM/embedding × 暂停/启用四组合均拒绝模型预留，清理仍能完成。独立复跑通过。

### ⚡ P2 — 主要缺陷

#### M1R-P2-01. schema_version接受布尔值和浮点数（已关闭）

- **位置**: `src/agent_workbench/experience_memory/models.py:19–34` `EventInput`。
- **审查维度**: 8.类型安全与契约表达。
- **置信度**: `[confirmed]`。
- **问题分析**: Pydantic的`Literal[1]`即使在strict配置下仍将True和1.0匹配并规范化成1，不符合MT08对非法类型的拒绝约束；独立构造测试确认该行为。
- **修复补丁**: 在Literal校验前显式要求准确整数类型和值：

```python
@field_validator("schema_version", mode="before")
@classmethod
def exact_schema_type(cls, value: object) -> int:
    if type(value) is not int or value != 1:
        raise ValueError("invalid_schema_version")
    return value
```

- **验证方式**: `tests/test_memory_authority.py:298–305`拒绝True、1.0、字符串和2，同时覆盖正常模型构造及`model_copy`后领域边界重验。独立复跑通过。

### 💡 P3 — 次要缺陷

none。

---

## 4. 维度覆盖声明

| # | 维度 | 开放发现数 | 备注 |
|---|---|---|---|
| 1 | 架构与边界 | 0 | 领域模块不导入Cognee；无HTTP/Hook/模型发送入口。既有RunStore承担事务与schema门禁。 |
| 2 | SOLID与现代设计 | 0 | 类型、公共事务辅助、租约账本和权威操作分离；无新产品伪Agent。 |
| 3 | 并发、异步与资源安全 | 0 | BEGIN IMMEDIATE串行写；跨进程CAS、租约fencing、过期及进程中断实测；连接经context manager关闭。 |
| 4 | 容错与防御性编程 | 0 | unknown终态不自动重放，迟到usage保留但不恢复准入；队列超限整事务回滚。 |
| 5 | 性能、伸缩性与计算效率 | 0 | 单用户SQLite范围；事件/经验/修订/队列/列表/调用预算有固定上限；未评估超出规格的多租户吞吐。 |
| 6 | 安全与数据合规 | 0 | P1已关闭；项目与来源复核、冻结输入再校验、安全错误码、删除清空所有逻辑正文；不声称物理擦除。 |
| 7 | 可观测性与运维友好度 | 0 | 任务状态、receipt、实际usage/unknown持久可查；外部worker告警属于后续运行接入。 |
| 8 | 类型安全与契约表达 | 0 | P2已关闭；公开命令/返回对象有严格类型和边界验证。 |
| 9 | 可测试性与死代码 | 0 | 真实SQLite、多进程、故障注入、预算并发、重启与过期覆盖；最后只读helper做范围/损坏/过期测试。 |
| 10 | 工程规范与一致性 | 0 | Ruff/mypy通过；产品UI保留待开发状态；旧迁移测试只补新增表排除项。 |
| 11 | 数据与事务完整性 | 0 | 经验、修订、receipt、outbox同事务；代际失效、幂等、不可复用投影和非空downgrade拒绝；实际升级旧表摘要一致。 |
| 12 | 依赖与供应链治理 | 0 | 本批无新依赖；不将MR-01实验依赖装入主项目。MR01-SC-01未被本次评分或测试清除。 |

---

## 5. 整改清单

| ID | 严重级 | 负责人 | 截止 | 状态 |
|---|---|---|---|---|
| M1R-P1-01 | Defect P1 | M1-R实现Codex（主Agent） | M1-R完成前 | closed，独立复审通过 |
| M1R-P2-01 | Defect P2 | M1-R实现Codex（主Agent） | M1-R完成前 | closed，独立复审通过 |

无需风险接受；没有推迟的本批缺陷。

---

## 6. 架构防腐与长效治理建议

1. **保持作业领取与模型准入分开** —— 清理、读取、模型调用各自检查明确用途，防止清理权限意外变成模型权限；成本为每类新增worker增加一组负向权限用例。本轮已补清理矩阵。
2. **保持新版本与只读增量的证据区分** —— 完整测试、定向增量和真实迁移各自记录对应代码范围，保留失败日志；成本为更新验收记录，防止将旧全量结果冒充最终全部代码验收。
3. **M2-R接入沿用本账本门禁** —— 实际发送前还要验证内容用途授权、token上界、来源有效性与取消，并落实MR01-SC-01；这是既定后续范围，不用本次账本模拟数值代替真实网关验收。

---

## 7. 审计声明

- 已审计版本为上述HEAD上的最终工作树SHA-256快照。对清单内实现、测试或界面语义的后续修改，需复审增量；台账及本报告链接/验收记录更新不改变已审代码。
- 未评估：本批范围内none；完整记忆产品、真实外部接入与效果对照未实施，不在本次Pass范围。
- 已施加豁免：none。
- 置信度：两条历史发现均为`[confirmed]`且已修复；无`suspected`开放项。
- 评审者只使用本项目文件、指定审计技能及当前提供的运行产物；未读取其他项目、全局记忆或凭据，未执行主库迁移、模型调用或外发。

### 最终代码快照

| 文件 | SHA-256 |
|---|---|
| `src/agent_workbench/experience_memory/__init__.py` | `2db0b49b5512f4e5943c7aee82c30b07a4270a84ff9c5418eb2dcd1dbdb7dd87` |
| `src/agent_workbench/experience_memory/common.py` | `64aceba7e0a9ce26d6228ce6e000e472785dee7b204c58d8e677af36bd3dd56b` |
| `src/agent_workbench/experience_memory/jobs.py` | `4697367bdb55b8d3711cce2c15e67e1c1d89e064b6f51f03585986a59f327916` |
| `src/agent_workbench/experience_memory/models.py` | `22a97e220198384d6980b7df7ec126e9e56ae7c6df1ea8d7b63b19289dbd178b` |
| `src/agent_workbench/experience_memory/store.py` | `1159219dbcd2b4473fab3601515a7212caf2f76e0a831741c50217a9248cf60f` |
| `src/agent_workbench/run_store/store.py` | `a0a94766a6e1bff6dbc6d2daf480c5532ee0d4ca76e4931ebcec8f870db71576` |
| `src/agent_workbench/run_store/migrations/versions/v0011_memory_authority.py` | `aa22a51cd217cc16cbe555df6c95fab234698f0ad3b829f7900b175711d146b4` |
| `tests/test_memory_authority.py` | `ca714253d39a495e58531245d45851c814b37237db1f0b3c65cdb4fd6d9ec16d` |
| `tests/test_memory_jobs.py` | `ff529fad7c7ff58250f413bf5fd417f9096d921155f10927e4b144f57c29e635` |
| `tests/test_web_analysis_store.py` | `9222e50077f2a3440078403942a8ac32ed51c1a3a5126cb098e1511330796bb4` |
| `frontend/components/connections-console.tsx` | `879fdb5ac577bc96e838c964437c9d2204609a9fac3a28fa47bc6cfc16edc164` |
| `frontend/lib/capabilities.ts` | `f43c9d2ed6a695c8550965517963e2c9f760666de02fc8b5a6edfbe8b0d7a817` |
| `frontend/lib/documentation.ts` | `c3fe745171da15f5c6d66107aa83fcaed4a6c0ee7a9b967d5bd3215bec1b5b4d` |
| `frontend/lib/milestones.ts` | `ae3d121de0157b83e41ae7161344e29bedb8db7637756bff2a8f36f878d78643` |
| `frontend/lib/modules.ts` | `41683bf0d9a5b8b7b24934e551cca1ee89577c0449e205863583b7aa38b0da14` |
| `frontend/tests/memory-boundaries.spec.ts` | `a18956667179d179cbdaabfd6a030c20959bb4822519ab6bba681ad97a44bcb5` |
