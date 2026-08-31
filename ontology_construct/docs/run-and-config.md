# 运行命令与配置说明

本文档说明本体构建服务的常用运行命令、实例重建命令，以及 `configs/default.yaml` 中主要配置项的含义。

## 1. 启动服务

进入后端目录：

```bash
cd ontology_construct
```

安装依赖由 `uv` 自动处理。启动 FastAPI 后端：

```bash
uv run ontology-construct-api --host 127.0.0.1 --port 8000
```

局域网访问时使用：

```bash
uv run ontology-construct-api --host 0.0.0.0 --port 8000
```

启动后访问：

```text
http://127.0.0.1:8000/
```

健康检查：

```bash
curl http://127.0.0.1:8000/health
```

## 2. 常用 CLI 命令

快速验证链路，不调用真实模型：

```bash
uv run ontology-construct build \
  --input README.md \
  --output-dir outputs/mock_run \
  --backend mock
```

推荐的 schema-first 构建：

```bash
uv run ontology-construct build \
  --input README.md \
  --output-dir outputs/run_001 \
  --backend qwen-plus \
  --profile industrial_process \
  --construction-mode schema-first \
  --max-concepts 50 \
  --instance-count 0
```

只做分块：

```bash
uv run ontology-construct chunk \
  --input README.md \
  --output outputs/chunks.jsonl
```

标准模式分步执行：

```bash
uv run ontology-construct extract \
  --input README.md \
  --output-dir outputs/run_001 \
  --backend qwen-plus

uv run ontology-construct status \
  --run-dir outputs/run_001

uv run ontology-construct merge \
  --local-dir outputs/run_001/local \
  --output outputs/run_001/global.json

uv run ontology-construct export \
  --ontology outputs/run_001/global.json \
  --format json \
  --output outputs/run_001/formatted_ontology.json
```

## 3. 常用 API 命令

创建本体构建任务：

```bash
curl -X POST http://127.0.0.1:8000/ontology/jobs \
  -F "files=@/path/to/input.md" \
  -F "profile=industrial_process" \
  -F "backend=qwen-plus" \
  -F "construction_mode=schema-first" \
  -F "max_concepts=50" \
  -F "extract_real_instances=false"
```

查询任务状态：

```bash
curl http://127.0.0.1:8000/ontology/jobs/{jobId}/status
```

获取格式化本体：

```bash
curl http://127.0.0.1:8000/ontology/jobs/{jobId}/result
```

获取图谱数据：

```bash
curl "http://127.0.0.1:8000/ontology/jobs/{jobId}/graph?view=instance"
```

基于已有 schema-first job 重新抽取实例，不重跑 schema：

```bash
curl -X POST "http://127.0.0.1:8000/ontology/jobs/{jobId}/instances/rebuild?backend=qwen-plus"
```

如果输入主要是 `.log/.json/.jsonl`，实例重建主要走规则/结构化抽取；如果包含 `.txt/.md/.pdf/.docx` 且 `instance_builder.llm_enabled_for_unresolved=true`，会对这些普通文本调用 LLM 做 schema 约束实例补充。

## 4. 输出文件

API 任务输出目录：

```text
ontology_construct/outputs/api_jobs/{jobId}/
```

常见输出：

| 文件 | 说明 |
|---|---|
| `job.json` | 任务状态、参数、输入文件记录 |
| `chunks.jsonl` | 清洗分块后的输入文本 |
| `concept_global.json` | schema-first 的概念层结果；实例重建以它作为 schema 输入 |
| `global.json` / `ontology.json` | 最终内部本体，包含实例层 |
| `formatted_ontology.json` | 前端展示和问答使用的格式化本体 |
| `schema.json` | canonical schema/type layer |
| `graph.json` | canonical graph/instance layer |
| `concept_instances.json` | 旧 concept pipeline 的实例报告 |
| `instance_candidates.jsonl` | 新实例构建器的候选实例 |
| `typed_instances.jsonl` | 新实例构建器完成类型映射后的实例 |
| `instance_links.jsonl` | 新实例构建器生成的实例关系候选 |
| `instance_build_report.json` | 新实例构建统计报告 |
| `failed_instance_batches.jsonl` | 普通文本 LLM 实例抽取失败批次 |
| `instance_postprocess.json` | 实例连通性后处理报告 |

## 5. 配置项说明

主要配置文件：

```text
ontology_construct/configs/default.yaml
```

### `chunk`

| 配置 | 含义 |
|---|---|
| `size` | 单个 chunk 的目标 token 上限。越大上下文越完整，但 LLM 请求更慢。 |
| `overlap` | 相邻 chunk 的重叠 token 数。用于减少边界信息丢失。必须小于 `size`。 |

### `llm`

| 配置 | 含义 |
|---|---|
| `backend` | 默认模型后端，如 `qwen`、`qwen-plus`、`qwen-32b`、`jiutian`、`deepseek`、`mock`。 |
| `base_url` | OpenAI-compatible API 地址。部分 backend 会在代码里按环境变量覆盖。 |
| `model` | 模型名称。 |
| `api_key_env` | API key 所在环境变量名。 |
| `temperature` | 抽取温度。结构化抽取建议保持低温。 |
| `max_tokens` | 单次模型输出 token 上限。 |
| `timeout_seconds` | 单次请求超时时间。实例抽取超时多时可适当调大。 |
| `max_retries` | 失败重试次数。 |
| `concurrency` | schema 候选抽取等主 LLM 调用并发。过大可能导致 503/限流。 |
| `enable_thinking` | 是否向支持的模型发送 `enable_thinking` 参数（主要用于 DashScope/Qwen）。结构化 JSON 抽取通常关闭。DeepSeek V4 使用新版 `thinking` 对象，由后端适配器自动关闭，并在 JSON 类请求中自动启用 `response_format=json_object`。 |

### `merge`

| 配置 | 含义 |
|---|---|
| `embedding_backend` / `embedding_base_url` / `embedding_model` | embedding 去重使用的模型配置。 |
| `embedding_api_key_env` | embedding API key 环境变量。 |
| `similarity_threshold` | embedding 合并实体的相似度阈值。越高越保守。 |
| `use_llm_synonym_judgment` | 是否额外用 LLM 判断同义实体。 |

### `concept`

该段控制 schema-first 的概念候选、概念筛选、schema 关系和旧实例抽取流程。

| 配置 | 含义 |
|---|---|
| `max_candidates_per_chunk` | 每个 chunk 最多抽取多少个候选概念。 |
| `max_concepts` | 最终 schema 概念数量上限。 |
| `min_evidence` | 保留概念所需的最小证据数。 |
| `selection_score_threshold` | 概念选择的基础分数阈值。 |
| `selection_score_ratio` | 相对最高分的动态阈值比例。默认 `0`（关闭），避免短文档中因关系中心度差异误删有明确证据的概念；需要强制裁剪长尾时可显式调高。 |
| `abstraction_level` | 期望概念抽象层级，常用 `high`、`medium`、`low`。 |
| `use_embedding_clustering` | 是否用 embedding 做候选概念语义聚类。 |
| `embedding_similarity_threshold` | 概念 embedding 聚类阈值。 |
| `relation_vocab` | schema 关系词表；profile 可覆盖。 |
| `candidate_prompt` / `generalize_prompt` / `relation_prompt` | schema-first 各阶段 prompt 路径。 |
| `controlled_relation_build` | 是否让 LLM 在受控词表下构建概念关系。 |
| `extract_real_instances` | 旧 concept pipeline 是否抽真实实例。现在最终实例层会被 `instance_builder` 替换；想减少旧实例 LLM 耗时可设为 `false`。 |
| `extract_instance_relations` | 旧 concept pipeline 是否构建实例关系。 |
| `instance_extraction_mode` | 旧实例抽取模式，`per_chunk` 或 `per_concept`。 |
| `instance_chunk_batch_size` | 旧 per-chunk 实例抽取每次合并的 chunk 数。 |
| `instance_llm_concurrency` | 旧实例抽取和旧实例关系构建的并发。 |
| `max_instances_per_concept` | 旧实例抽取每个概念最多保留多少实例。 |
| `instance_relation_batch_size` | 旧实例关系判断每批候选对数量。 |
| `max_instance_relation_pairs` | 旧实例关系候选对总上限。 |
| `min_relation_pairs_per_instance` | 旧实例关系候选选择时，每个实例至少保留的候选对数量。 |
| `instance_source_char_limit` | 旧实例抽取输入原文长度上限。 |
| `instance_relation_source_char_limit` | 旧实例关系判断输入原文长度上限。 |

### `instance_builder`

该段控制新增的独立实例构建器。schema-first 完成后会用它基于已生成 schema 和原始输入重新生成最终实例层。

| 配置 | 含义 |
|---|---|
| `enabled` | 是否启用新实例构建器。设为 `false` 时保留旧 schema-first 输出实例。 |
| `mode` | 实例构建模式。当前 `hybrid` 表示规则/结构化优先，普通文本再用 LLM 补充。 |
| `max_instances_per_type` | 每个 schema 类型最多保留多少实例，防止图谱过大。 |
| `max_log_records_per_file` | 每个 `.log` 文件最多处理多少行原始日志。 |
| `max_log_records_per_template` | 每种日志模板最多处理多少行，防止重复模板淹没实例图。 |
| `llm_enabled_for_unresolved` | 是否对普通文本和无法规则处理的内容启用 LLM 实例补充。想降低 LLM 调用可设为 `false`。 |
| `llm_chunk_batch_size` | 普通文本 LLM 实例抽取时每批合并的 chunk 数。越小越稳，越大调用更少。 |
| `llm_concurrency` | 新实例构建器普通文本 LLM 调用并发。 |
| `source_text_char_limit` | 普通文本单批传给 LLM 的原文字符上限。 |

### `instance_postprocess`

该段是实例图后处理。它仍然可以调用 LLM 处理离散实例，并用确定性兜底补连边。

| 配置 | 含义 |
|---|---|
| `enabled` | 是否启用实例连通性后处理。想完全关闭 LLM 去离散点/补边，可设为 `false`。 |
| `min_instances` | 实例数低于该值时不触发后处理。 |
| `max_llm_instances` | 传给 LLM 后处理的实例数量上限。 |
| `max_anchor_instances` | 确定性兜底可使用的锚点实例数量上限。 |
| `max_drop_ratio` | LLM 建议删除离散/噪声实例时允许删除的最大比例。 |
| `min_link_confidence` | LLM 补边最小置信度。 |
| `fallback_predicate` | 确定性兜底补边时使用的关系名。 |
| `source_tag` | 后处理补边写入 `source_chunks` 的来源标记。 |

### `frontend_build`

前端简化构建表单的默认值。修改后需要重启 API。

| 配置 | 含义 |
|---|---|
| `default_profile` | 前端默认本体类型。 |
| `default_backend` | 前端默认模型后端。 |
| `construction_mode` | 前端默认构建模式，推荐 `schema-first`。 |
| `max_concepts` | 前端默认最大 schema 概念数。 |
| `semantic_clustering` | 前端默认是否启用语义聚类。 |
| `instance_mode` | 前端实例策略显示值。 |
| `instance_count` | 格式化输出中额外生成示例实例数量。真实实例由抽取结果决定。 |
| `qa_context_mode` | 问答上下文模式。 |
| `qa_fallback_enabled` | 问答是否启用 fallback。 |
| `llm_timeout` / `llm_retries` / `llm_concurrency` | 前端构建时覆盖 LLM 超时、重试和并发；空值表示使用 `llm` 默认。 |
| `min_concept_evidence` / `abstraction_level` | 前端构建时覆盖概念筛选参数；空值表示使用 `concept` 默认。 |

### `ontology_profiles`

不同业务场景的 schema 建模配置。

| 配置 | 含义 |
|---|---|
| `ontology_name` | 输出本体名称。 |
| `ontology_desc` | 输出本体描述。 |
| `prompt` | standard 模式抽取 prompt；schema-first 也会读取 profile 信息。 |
| `domain_prompt` | 领域背景文件，会注入 supplemental prompt。 |
| `relation_vocab` | profile 级关系词表覆盖。为空则使用 `concept.relation_vocab`。 |

## 6. 环境变量

| 变量 | 说明 |
|---|---|
| `DASHSCOPE_API_KEY` | Qwen / DashScope 默认 key |
| `QWEN_API_KEY` | Qwen 备用 key |
| `QWEN_EMBEDDING_API_KEY` | Qwen embedding key |
| `QWEN_EMBEDDING_MODEL` | 覆盖 embedding 模型，默认 `qwen3.7-text-embedding` |
| `QWEN_EMBEDDING_BASE_URL` | 覆盖 embedding endpoint |
| `JIUTIAN_API_KEY` | 九天网关 key |
| `DEEPSEEK_API_KEY` / `DEEPSEEK_API_KEY_2..5` | DeepSeek 主/备用 key，失败后按顺序切换 |
| `DEEPSEEK_BASE_URL` / `DEEPSEEK_MODEL` | 覆盖 DeepSeek endpoint 和模型 |
| `MOONSHOT_API_KEY` / `KIMI_API_KEY` | Kimi key，前者为官方变量名 |
| `KIMI_BASE_URL` / `KIMI_MODEL` | 覆盖 Kimi endpoint 和模型 |
| `KIMI_REASONING_EFFORT` | Kimi K3 推理强度 |
| `QWEN_BASE_URL` / `QWEN_MODEL` | 覆盖 Qwen chat endpoint 和模型 |
| `QWEN_32B_BASE_URL` / `QWEN_32B_MODEL` | 覆盖 qwen-32b endpoint 和模型 |

本地可在 `ontology_construct/.env` 中配置这些变量。

## 7. 测试命令

运行全部后端测试：

```bash
uv run pytest
```

只运行实例构建相关测试：

```bash
uv run pytest tests/test_instance_builder.py tests/test_api.py tests/test_config.py
```

## 8. 调优建议

- schema 已经稳定时，优先调 `instance_builder.*`，不要改 `concept.*` 的 schema 抽取参数。
- 日志实例少时，增大 `instance_builder.max_log_records_per_file` 和 `instance_builder.max_log_records_per_template`。
- 普通文本实例 LLM 超时多时，减小 `instance_builder.llm_chunk_batch_size`，降低 `instance_builder.llm_concurrency`，或增大 `llm.timeout_seconds`。
- 想尽量减少 LLM 调用时：
  - `concept.extract_real_instances: false`
  - `instance_builder.llm_enabled_for_unresolved: false`
  - `instance_postprocess.enabled: false`
- 想保留 LLM 补边和去离散点时，保持 `instance_postprocess.enabled: true`。
