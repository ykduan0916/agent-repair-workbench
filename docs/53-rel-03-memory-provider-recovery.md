# REL-03：记忆初始化与向量请求恢复

日期：2026-10-21。Feature P1，Defect P1。状态：done（本地修复与一次真实索引/检索恢复验收；不代表接口稳定性或 M5 完成）。

## 1. 问题与边界

用户要求定位并修复 M5-R 的 embedding 请求失败与 Cognee 零请求初始化失败。本批修复项目内的初始化和请求恢复，不替换向量模型、不伪造向量、不删除失败、不重跑整个索引来找成功。

用户指定的 Semantic Query 配置已复制到本项目私有配置；本轮逐字段核对地址、密钥、bge-m3 模型名、1024 维全部一致。未复制或使用外部项目的业务逻辑、记忆与数据。该配置是远程接口配置，不是本地模型权重。

## 2. 可复核的诊断

| 观察 | 结论及限制 |
|---|---|
| 相同请求摘要出现 HTTP 200 和 500；字符串与数组输入均复现 | 不是简单的批量不兼容或漏复制配置。500 的固定类型/代码为 new_api_error / do_request_failed；无法仅凭客户端信息确定网关内部原因 |
| Semantic Query 对 HTTP 错误最多尝试三次，本项目原来遇到一次失败就终止 | 请求恢复策略存在实际差异；正常使用感受不等同于每一次 HTTP 都成功 |
| 新 TMPDIR、同锁定 SDK、禁止网络：bge-m3 adapter 初始化失败，tiktoken 下载尝试一次；放入已校验现有缓存后成功 | 确认初始化存在隐式分词资源下载依赖；历史两次错误文本已被旧代码丢弃，不能反推它们一定都是此原因 |
| Cognee 导入触发 LiteLLM 远程价格表请求 | 使用 SDK 自带价格表后，真实 adapter 初始化零网络尝试成功；不影响实际网关用量账单 |

证据为本机 `artifacts/embedding-fix/` 与 `artifacts/embedding-review/`，不入 Git。最早诊断误请求基础路径得到 404 的尝试保留在 `probe-wrong-base-path.json`，这是诊断调用错误，不是生产拼接错误。密钥、原文、完整上游异常不进入报告。

## 3. 修复契约

### 3.1 离线初始化

- 复用本项目既有 cl100k_base 缓存，SHA256 固定为 `223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7`，与锁定 tiktoken 内置期望一致。
- 安装到 SDK 环境 `share/awb-tokenizers`，原子建立文件、不覆盖现存资源、私有权限；不下载新模型或文件。
- worker 在导入 Cognee 前校验并预热；缺失/损坏明确失败。禁用 tiktoken 的网络读取 fallback，其他编码也不能静默下载。
- `LITELLM_LOCAL_MODEL_COST_MAP=true` 使用锁定包内资源。
- 只输出固定阶段码：tokenizer / sdk_import / cache_check / input / add / source_lookup / cognify / search / output_validate；不输出异常正文。

### 3.2 有限 embedding 恢复

只有 embedding 返回 HTTP 500 且固定类型和代码匹配时允许恢复。**收到该响应不能证明上游未执行或未计费。**

1. 同一序列化请求、同一 job/lease，最多三次总尝试，等待 0.5 / 1 秒。
2. 每次独立预留调用、tokens；每次重查来源、授权、scope、预算、取消与原截止时间。
3. 不提高原有 40 embedding / 6 LLM / 100,000 tokens / 180 秒上限。Agent 另保留固定 100,000 的记忆预算，任务预算不超过 1,900,000。
4. 每次失败保留 `unknown` 与 `actual_tokens=NULL`，预留不退还。第三次失败停止，SDK 隐式重试不能越过宿主停止开关。
5. 只有后续向量通过维数、数量、有限数值与来源授权校验，才能写 `recovered_by`；它表示结果恢复，**不表示费用已知**。
6. 其他 HTTP 错误、网络超时、传输异常、坏向量、LLM 失败继续停止。不能借重试发送不同正文、不同工具调用或重跑 cognify。
7. 遗留 unknown 没有恢复组和代码，不获此豁免；owner 崩溃、取消后不能自动重新领用任务。

### 3.3 状态与费用分离

有效检索结果可以供 Agent 参考，即使同请求先前失败的费用仍未知。宿主保持全部预留上界；验证角色仍不接收经验。网页保留“仍有未知用量”。M5 的 `usage_complete`、成本可比门禁继续要求全部账单已知，不把功能恢复包装成成本评估通过。

## 4. 数据库与部署

迁移 `0014_agent_memory → 0015_embedding_retries` 给 memory_calls 增加 retry_group、request_digest、failure_code、recovered_by。旧行四列均为 NULL；不修改历史状态/用量，不重放旧请求。新增恢复记录后拒绝降级，服务启动不自动迁移。

准备已有分词资源（路径由宿主提供）：

```bash
uv run --frozen python ops/prepare_memory_tokenizer.py \
  --sdk-python artifacts/mr01/venv/bin/python \
  --source /absolute/path/to/existing/cl100k_base/cache
```

迁移前停止常驻服务、备份主库并核对所有旧列/行；构建前端，完成迁移验收后恢复常驻服务。生产迁移与验收结果见下节补充。

## 5. 验收与真实尝试

- 确定性回归：恢复成功仍保留未知费用；三次耗尽；同 ID 改摘要；改组/改正文；预算不足；取消；撤权；deadline；二次超时；成功 HTTP 坏向量不能恢复；历史未知不能续跑；非空恢复账本拒绝降级；Agent 验证角色隔离。
- 独立锁定 SDK 离线初始化：零 socket 尝试，使用 TikTokenTokenizer。证明初始化，不等于真实检索收益。
- 本轮真实尝试全部保留：
  - `m4r/2d6cbf27-f362-457c-abf7-685fed40f6ef`：分词修复后通过初始化，embedding 500 停止，确认安全错误码落库。
  - `m4r/adf53be6-6a66-4320-8edc-81cbbb9ad8f0`：有限重试后三次均失败，停止且三次未知费用保留。
- `m4r/f67f0e16-e551-430f-9a9e-01374c93bc9b`：第二次有限恢复验收同样三次500，未完成索引。两次均不作为通过样本。
- 全量 Python **1905 passed / 69 skipped / 1 既有 fork warning**；浏览器 **23 passed**（含四类安全错误提示）；生产构建、Ruff 520 文件与 mypy 173 源文件通过。
- 独立评审[报告](reviews/REL-03-memory-provider-review.md)：本地增量 Pass，独立专项141 passed/2 skipped；真实接口稳定性与 M5 效果门禁仍 blocked。
- 主库已备份迁移到0015，40旧表238行旧列完全一致；常驻API与Web由launchd管理，PPID=1，health、记忆库、Hook页HTTP200。
- 精确请求对照 `embedding-fix/exact-bbe48865a02b440c82504a7e52a5789e`：真实Cognee索引 **ready**；4次embedding成功、5次指定500均由同摘要后续成功恢复，另2次LLM成功。11次调用，3,533已知tokens、32,001预留上界；5次失败用量仍未知。直接同payload三次post结果200/500/200，不能把失败归因于宿主转发或批量参数。
- 同一真实索引单次检索 **available / matched / 1个有效来源**，4次embedding调用、2次未知费用，32已知tokens、4,596预留。记录在同目录`retrieval.json`。没有重建索引或伪造向量。独立复核16项来源/版本一致，7条索引及检索失败的恢复关联全部有效，见`independent-ledger-review.json`。
- `exact-ad1d194162a64c84aadea2fcca33b517`为诊断脚本相对路径错误，零wire失败，改用绝对路径后执行上述精确对照；独立列出，不冒充历史根因。
- 实际证明有限恢复可用，不意味着所有请求都能恢复。前述三连失败也属于本轮验收结果。M5 整组收益评估尚未重跑，成本完整性仍受未知账单限制。

## 6. 前端

保留记忆独立入口与 Hook 页。索引操作显示固定安全诊断、已知用量和未知部分。不能把任意 provider 文本直接展示给用户。更新记录解释用户可见变化，不出现测试数量与内部里程碑号。
