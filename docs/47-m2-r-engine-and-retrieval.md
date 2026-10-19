# M2-R：Cognee接入、参考有效性与请求记账

2026-10-19，Feature P1。状态 done（planned → ready → in_progress → in_review → done）。依据[44](44-p1-02-memory-product-and-integration.md)、[45](45-mr-01-cognee-contract-validation.md)、[46](46-m1-r-authority-event-store.md)。本批实现可信本地宿主SDK与显式命令；产品Agent注入在M3-R，Hook/管理页面在M4-R。

## 1. 冻结实施边界

- 权威SQLite新增0012，保留M1-R表及所有原运行数据。新增5张表：授权、适用策略、引擎操作、派生存储登记与引擎根绑定；不将Cognee图或向量视为确认权威。
- Cognee 1.6.5及MR-01锁定的LanceDB/Ladybug继续使用独立Python环境。主进程持有现有DeepSeek/bge-m3凭据；子进程只获得临时回环凭据。无新模型下载。
- 每条经验的每次索引使用独立私有引擎目录。来源间没有共享图节点/缓存，删除不会破坏另一条经验。代价是重复索引及串行多库检索；这是可审计的隔离选择，不宣称大型知识库性能。
- 全局单worker文件锁覆盖索引、查询、清理；目录在调用SDK之前入账。SDK中途退出也有待清理来源。独立watchdog监视宿主pipe EOF及剩余期限，宿主异常退出后终止整个引擎进程组。重新执行索引生成新操作，不重放unknown调用。

## 2. 授权、适用性与并发

授权键为项目+连接+内容类型+用途(index/retrieve)，包含CAS版本、启用和到期时间；查询文本使用独立query类型授权。经验须当前confirmed、正文摘要一致、来源启用，且具备绑定当前代际的适用策略。跨版本仅在查询和经验策略均允许时返回historical_hint；exact也不是本次回归通过。

project_constraint必须给出宿主定义的conflict_key/value。同项目同tool的有效约束进行完整扫描，同键不同值则拒绝注入。自由文本语义矛盾仍不能确定性证明，不声称自动识别。扫描超过1000条、可检索范围超过8条、存在未索引/未知来源或复核变化时返回limited/unavailable，不返回部分经验冒充完整。

每个操作固定来源引用、授权版本、策略和查询摘要；实际每次HTTP外发前在同一个BEGIN IMMEDIATE事务中复核并预留。撤销先提交则不能预留；已预留请求不承诺追回。结果返回前再次复核整组来源与冲突。返回正文取权威记录，Cognee只提供可映射的来源引用，不直接采用无出处总结。

## 3. 资源与费用

复用M1每作业6次LLM/40次embedding、累计预留100000tokens不退款；单次预留上限65536。bge-m3响应必须条数匹配、每项1024维有限数值，有index时必须依序。180秒作业、30秒单HTTP、64KiB请求、1MiB响应、4096输出、embedding最多16项每项4096字符。输入按UTF-8请求字节+4096输出+1024协议余量进行条件性上界预留，要求宿主显式接受该provider profile；非已验证的网关强制上限。actual超界/缺失usage/非200/取消造成响应不完整时unknown；已经收到的完整可信账单可保留received，但操作结果仍拒用，立即停止新外发并保留账单。价格未配置，金额unknown。

SDK内重试每一次经过宿主；同调用ID重传无再次发送许可。检索embedding也记账。子进程不持上游密钥；stdout/stderr丢弃；报告只含安全状态、ID、摘要和数值。已授权经验正文只用于本地上下文返回，不写日志。

## 4. 供应链与删除

MR01-SC-01仍是未修复上游依赖风险。强制显式sqlite cache并验证实际适配器类型；禁止FSCache回退。运行根及后代目录检查当前UID、私有权限、无符号链接；临时请求文件0600并清理。失败关闭，不把离线环境变量称为OS网络隔离。可信单用户宿主，不提供恶意本地用户/多租户隔离。

派生目录登记先于SDK调用，清理在同一worker锁下移除整个指定目录，先持久标记不可用再删除，失败可重试。撤回/删除立即使来源不可用；派生物理清理是显式维护操作，可在停用来源后执行。备份和已在途请求不在物理删除承诺内。

## 5. 验收矩阵

| 编号 | 门禁 |
|---|---|
| ER01 | 缺授权、过期、暂停、跨项目、草稿/撤回/删除/篡改均不能发送 |
| ER02 | exact/historical_hint分级；完整范围冲突、超限/未索引显式受限 |
| ER03 | 请求预留与撤回串行化；重放/未知/真实重试计数与超限 |
| ER04 | SDK引用映射及发布前/返回前复核；无来源结果不得注入 |
| ER05 | 单worker锁、进程终止、租约、私有缓存、部分写入清理与重启 |
| ER06 | 真实固定Cognee+现有模型/embedding：索引→查询→撤回拒用→物理清理；失败全保留 |
| ER07 | 0011→0012备份迁移旧数据不变；全仓静态/回归及独立分批评审 |
| ER08 | 前端边界/文档/深浅色移动端，常驻服务刷新 |

## 6. 本机使用

本批不安装外部Hook。网页“环境与接入 → 经验与事件”提供说明和本站文档；命令需本机维护者操作。`EngineStore`负责显式登记授权和确认经验，`CogneeRuntime`负责执行，CLI不自动确认草稿或添加授权。

### 配置与隔离

- 主环境用`uv sync --frozen`。Cognee独立环境严格使用`experiments/mr01/uv.lock`，安装命令见45及该实验README；本次复用已验证环境，没有下载模型。SDK子进程校验Cognee版本1.6.5。
- 模型配置采用现有`ops.local_service.read_environment`私有JSON；embedding私有JSON包含`endpoint/api_key/model/dimensions`，model为bge-m3、dimensions为1024，可选`revision`默认`bge-m3-1024-host-v1`。两文件要求本机UID及0600权限，密钥不传命令参数或浏览器。
- endpoint、model、dimensions、宿主声明revision共同生成指纹，凭据不参与。服务在同endpoint升级embedding时必须增加revision并重建索引；服务偷偷替换权重无法自动检测。不同向量空间不可混查。重建时按匹配指纹选择索引，不按随机ID猜新旧。
- 权威库绑定唯一规范engine-root，engine-root也绑定权威库路径。更换目录不能把实际仍存在的索引误报为清理完成；迁移整个存储需停worker并另行验证迁移方案。

### 准备已审核的合成经验

以下示例只写独立本地状态；不会自行联网。实际用户材料须逐来源审核，不可把本示例synthetic授权复制成全量会话授权。

```python
from pathlib import Path
from time import time
from agent_workbench.experience_memory import DraftInput, EventInput
from agent_workbench.experience_memory.engine_models import Grant, Policy
from agent_workbench.experience_memory.engine_store import EngineStore

store = EngineStore(Path("artifacts/memory-example"))
store.initialize()  # 显式建库或迁移；已有库先停服务并备份
store.register_project("example")
store.register_connection("example", "fixture", "synthetic")
store.accept_event(
    "example",
    "fixture",
    EventInput(
        event_id="count_failure",
        event_type="test_completed",
        source_run_id="fixture_run",
        occurred_at=time(),
        outcome="failed",
    ),
)
item = store.create_memory(
    "example",
    "create_count",
    DraftInput(
        kind="repair_experience",
        connection_id="fixture",
        event_id="count_failure",
        tool_id="count",
        source_version="v1",
        title="Synthetic count output",
        body="Synthetic test: converting the count from string to integer fixed the output type.",
    ),
)
item = store.change_memory("example", item.resource_id, "confirm_count", 1, state="confirmed")
store.set_policy(
    "example",
    item.resource_id,
    Policy(expires_at=time() + 3600, allow_historical=True),
    expected_generation=item.version,
    expected_version=0,
)
for kind, purpose in [
    ("repair_experience", "index"),
    ("repair_experience", "retrieve"),
    ("query", "retrieve"),
]:
    store.set_grant(
        "example",
        Grant(
            connection_id="fixture",
            content_type=kind,
            purpose=purpose,
            enabled=True,
            expires_at=time() + 3600,
        ),
        expected_version=0,
    )
print(item.resource_id)  # 安全ID；不打印正文/凭据
store.close()
```

相同授权操作以expected_version做CAS，不是可无限重放的授权脚本；修改需先读取宿主保存的版本。上例用于空的独立示例库。`project_constraint`策略必须给出配对conflict_key/value；完整冲突扫描不依赖模型。

### 执行

将下列`MEMORY_ID`替换为上例安全ID。运行前先准备两份私有配置文件；现有模型配置可复用，命令只传文件路径。

```bash
uv run --frozen python ops/memory_engine.py index --execute \
  --state artifacts/memory-example --engine-root artifacts/memory-example-engine \
  --project example --memory-id MEMORY_ID \
  --sdk-python artifacts/mr01/venv/bin/python \
  --model-config artifacts/local-service/model-env.json \
  --embedding-config artifacts/mr01/embedding-env.json \
  --accept-conditional-token-profile
```

查询文件是严格JSON，不是任意日志，最多8192字节；text最多1024字符。如下查询文本也是预先审核的合成内容：

```json
{"connection_id":"fixture","event_id":"count_failure","tool_id":"count",
 "source_version":"v2","allow_historical":true,
 "text":"Synthetic count tool returns a string. What previous repair is relevant?"}
```

用`search`替换命令中的`index`，用`--query-file artifacts/query.json`替换`--memory-id MEMORY_ID`。`--accept-conditional-token-profile`表示接受文中条件性预算上界，不表示外发所有本机文件。CLI只输出来源ID/摘要/适用分级/数值账单；宿主SDK返回的正文应作为不可信参考，不能作为提示词权限。

撤回或删除使用`change_memory(... expected_version=当前版本, state="revoked"或"deleted")`，先使参考资格失效，再显式清理。清理不需要模型配置，也不会发送HTTP：

```bash
uv run --frozen python ops/memory_engine.py cleanup --execute \
  --state artifacts/memory-example --engine-root artifacts/memory-example-engine \
  --project example --sdk-python artifacts/mr01/venv/bin/python
```

`index_required`需先索引；`authorization_required`需宿主审核授权；`embedding_profile_changed`需重建；`scope_conflict`需人工纠正规则；`engine_busy`须等待在途worker或先停止它。unknown调用不自动重试、不退款；人工确认停止后可建立新的独立操作，但历史失败一直保留。超出8条相关经验返回limited，本批不承诺大规模检索性能。超范围/未授权/过期记录不能静默当完整查询。

## 7. 当前验收记录

- 三次真实合成门禁全部保留。首次`f1d29afbdcf242c0ba52e92dea79d80a`失败：1次LLM预留11934，usage未知，停止外发、回收1根。当时安全原因未细分，不能倒推超时或网关具体错误；随后增加固定failure_code。
- 第二次`f701b65229594518afb78adccea1a0cf`通过：11次请求、7646已知tokens。该次用于验证安全原因记录及整体链路，尚未包含最终向量/配置/进程补偿。
- 第三次`8f8ce2065ebc48f9ab4b8133628875b1`在补偿后通过：2次LLM+9次embedding、7206已知tokens、31817累计预留；1条historical_hint，撤回后0引用且0额外请求，1个完整引擎根移除，0未知账单。证据各在`artifacts/m2r/<ID>/acceptance.json`。这些是合成契约实测，不是记忆收益或修复成功率统计。
- 独立权威层评审关闭2项P1：未终结根进入检索、重建后被随机UUID排序误挡。运行层关闭5项P1：向量响应校验、宿主死亡回收、物理目录绑定、embedding指纹、正文被误当资源读取。见[权威评审](reviews/M2-R-authority-review.md)、[运行评审](reviews/M2-R-runtime-review.md)。供应链P2保留，按本页补偿在可信单用户范围接入。
- 最终全量Python回归1720 passed / 68 skipped / 1历史fork警告，212.34秒。末尾正文保护属于额外安装SDK契约，未重复付费模型调用；最后记忆四组含真实SDK契约98 passed / 25.41秒。默认套件中的Docker/模型/SDK门禁须显式开启，不把skip算通过。
- 第一次全仓执行1715 passed / 1 failed：历史0006迁移测试未排除新加5张表，旧行实际一致；补上新增表清单后全量通过。CLI配置单文件测试最初缺少repo路径导入，修复隔离测试导入后最终专项通过。所有失败如实保留，不以最终通过抹去失败过程。
- Ruff check/format、mypy154文件、前端TypeScript及生产构建通过。15项浏览器验收25.7秒：浅/深色、390/1440文档排版、axe、无横向溢出、导航、CLI边界；锚点点击进一步断言“本机使用”实际进入视口。界面截图`artifacts/m2r/memory-light.png`、`memory-dark.png`。
- 主库显式备份迁移0011→0012：29旧表、228旧行完全一致，5新表初始空。备份及结果在`artifacts/m2r/migration-7384460ff64a4212903ee59347071cbf/`。没有把合成验收数据灌入用户主库。
- 前后端通过既有launchd服务常驻；最终健康与HTTP证据在`artifacts/m2r/services-final.json`。

### 可复跑命令与测试分层

```bash
uv run --frozen pytest -o addopts='--import-mode=importlib' -q
AWB_COGNEE_CONTRACT=1 uv run --frozen pytest tests/test_memory_runtime.py tests/test_memory_engine.py tests/test_memory_jobs.py tests/test_memory_authority.py -o addopts='--import-mode=importlib' -q
uv run --frozen ruff check .
uv run --frozen ruff format --check .
uv run --frozen mypy
uv run --frozen python ops/m2r_live_gate.py --execute
```

最后一条会调用现有外部模型与embedding，只发送脚本内已审核的合成经验；每次生成新目录、独立权威库、独立预算并保留失败。普通pytest不调用远程模型。运行器测试包括真实子进程取消、父进程kill及deadline；向量坏响应与授权并发使用确定性适配器；安装SDK单独验证显式SQLite错误拒用与字面摄入；它们不能代替真实模型账单证据。

第三次真实模型实验发生在最后字面摄入补丁之前。该补丁使用固定SDK真实`add`额外验证本地路径、HTTP与S3字符串均存为原文，未触发资源读取；独立评审追踪了add到storage完整调用链。强制`ALLOW_HTTP_REQUESTS=false`仅是SDK配置，不宣称OS网络隔离。

后续为M3-R：将取用接口接入诊断/修复的参考上下文，绑定每次模型请求的来源引用，验证角色仍不能把记忆当作通过依据；BH-06只生成可确认草稿。网页经验管理与外部客户端属于M4-R。本批未触发这些功能，也没有记忆收益实验结论。
