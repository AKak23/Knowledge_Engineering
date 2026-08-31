# Ontology Construct

基于长文本自动构建本体的项目目录。

本项目参考根目录下的 `ontology_construction_design.md`，目标是从 TXT / Markdown / PDF / DOCX / JSON / JSONL 等长文本中抽取概念、属性和关系，经过归并、去重、可选精炼后，输出结构化本体图谱。

## 当前状态

已完成第一阶段开发：

- Python 3.12 项目骨架
- 默认 YAML 配置加载
- TXT / Markdown / PDF / DOCX / JSON / JSONL 文本读取
- 文本清洗
- 段落优先的 chunker
- `ontology-construct chunk` CLI
- `ontology-construct extract` CLI，支持 DeepSeek、Qwen 和 mock LLM 后端
- `ontology-construct status` CLI，查看 chunk 处理状态
- `ontology-construct merge` CLI，合并局部本体
- `ontology-construct export` CLI，导出标准 JSON
- `ontology-construct build` CLI，一键执行抽取、合并和 JSON 导出
- `Entity` / `Triple` / `OntologyAction` / `OntologyInstance` / `ActionInvocation` 等数据模型，instance 严格挂在类下并承载 `attribute_values` 与 `link_values`
- 类层 schema 与实例化分层输出（详见「输出结构」）
- 本体 JSON 保存与加载
- SQLite 进度追踪，支持跳过已完成 chunk
- 精确实体归并和重复三元组去重
- DeepSeek / Qwen OpenAI-compatible LLM client
- Qwen OpenAI-compatible embedding client
- 可选 embedding 模糊实体去重 / schema-first 概念语义聚类
- LLM chunk 抽取进度条
- 基础单元测试

规划文档：

- `IMPLEMENTATION_PLAN.md`: 功能范围、阶段拆分、模块边界和验收标准

## 目标能力

- 读取 TXT / Markdown / PDF 输入并清洗
- 按段落、句子和长度约束进行稳定分块
- 对每个 chunk 并发调用 LLM 抽取局部本体
- 合并实体、属性和关系，形成全局本体
- 支持断点续跑和失败 chunk 重试
- 可选使用向量召回和 LLM 精炼关键实体
- 导出标准 JSON

## 建议项目形态

当前采用仓库内 `ontology_generation` 一致的 Python src-layout：

```text
ontology_construct/
├── README.md
├── IMPLEMENTATION_PLAN.md
├── pyproject.toml
├── configs/
├── src/
│   └── ontology_construct/
└── tests/
```

## 运行

安装开发依赖：

```bash
uv sync --extra dev --python python3.12
```

执行测试：

```bash
.venv/bin/python -m pytest
```

配置 API key：

```bash
export DEEPSEEK_API_KEY="..."
export DEEPSEEK_API_KEY_2="..."
export MOONSHOT_API_KEY="..."
export DASHSCOPE_API_KEY="..."
export QWEN_EMBEDDING_API_KEY="..."
```

也可以在本地 `.env` 文件中配置，程序会自动读取；`.env` 已被 `.gitignore` 忽略，不应提交：

```bash
DEEPSEEK_API_KEY=...
DEEPSEEK_API_KEY_2=...
MOONSHOT_API_KEY=...
DASHSCOPE_API_KEY=...
QWEN_EMBEDDING_API_KEY=...
```

DeepSeek 可配置 `DEEPSEEK_API_KEY` 以及 `_2` 至 `_5` 五个顺序密钥；鉴权、余额/权限、限流或服务端错误时客户端会切换到下一把，并优先复用最近成功的密钥。Kimi 使用 `MOONSHOT_API_KEY`（也兼容 `KIMI_API_KEY`），默认 endpoint 为 `https://api.moonshot.cn/v1`，默认模型为 `kimi-k3`。

Qwen 文本抽取默认使用 DashScope OpenAI-compatible 接口：

- 默认 `base_url`: `https://dashscope.aliyuncs.com/compatible-mode/v1`
- 默认模型：`qwen-plus`
- API key 查找顺序：`DASHSCOPE_API_KEY`、`QWEN_API_KEY`、`QWEN_EMBEDDING_API_KEY`
- 可用环境变量覆盖：`QWEN_BASE_URL`、`QWEN_MODEL`、`QWEN_PLUS_MODEL`

概念优先模式下，候选概念抽取使用 `llm.concurrency` 控制并发；真实实例抽取和实例关系构建使用 `concept.instance_llm_concurrency`，默认较低，用于降低批量实例抽取时触发模型服务 429 限流的概率。实例关系判断默认保留更长上下文，并通过 `concept.min_relation_pairs_per_instance` 给每个实例保留候选关系对，避免全局高分候选挤占预算后产生大量孤立节点。

结构化数据构建通过 `/ontology/structured-configs` 上传 YAML 触发。v1 只支持 PostgreSQL，只读连接，密码只能通过 `password_env` 提供；默认使用弱规范库 hybrid 路径，把 `meta.*`、主外键、注释、采样数据、字段名、值重叠、embedding 和 LLM 都作为证据来源生成 schema/关系 mapping，不执行 YAML 中的自由 SQL 查询。

本地测试结构化 YAML 前，需要先启动 PostgreSQL 服务：

```bash
brew services start postgresql@16
```

测试结束后可关闭服务：

```bash
brew services stop postgresql@16
```

当前模拟库示例使用 `ontology_simulate` 数据库；如果本机 Homebrew PostgreSQL 使用当前系统用户免密连接，可在 `.env` 中配置：

```bash
PGUSER=shuaichen
PGPASSWORD=
```

默认 hybrid 构建需要 LLM 和 embedding key；缺少环境变量时任务会失败并在 job status 中显示缺失项。Qwen/DashScope 可配置：

```bash
DASHSCOPE_API_KEY=...
QWEN_EMBEDDING_API_KEY=...
```

构建会额外输出 `database_profile.json`、`structured_candidates.json`、`structured_mapping.json`。LLM 不逐行生成实例；实例按 YAML 中的采样限额从数据库行批量生成。

分块示例：

```bash
.venv/bin/ontology-construct chunk \
  --input README.md \
  --output outputs/chunks.jsonl
```

完整 DeepSeek 流水线示例：

```bash
.venv/bin/ontology-construct build \
  --input README.md \
  --output-dir outputs/run_001 \
  --profile business_process \
  --instance-count 3
```

完整 Qwen 流水线示例：

```bash
.venv/bin/ontology-construct build \
  --input README.md \
  --output-dir outputs/run_qwen \
  --backend qwen-plus \
  --profile business_process \
  --llm-timeout 600 \
  --llm-retries 0 \
  --llm-concurrency 1 \
  --max-extracted-instances 20 \
  --instance-count 3
```

`extract` 和 `build` 会在处理 chunk 时显示进度条，并实时更新 running / done / skipped / failed 数量。HTTP 客户端重试结束后，流水线还会按 `build_quality.chunk_retry_rounds` 对最终失败的 chunk 做补偿重试；`require_all_chunks=true` 时仍有失败会终止构建，不再发布局部本体。
`--profile` 可选择 `default`、`people_relation`、`business_process` 等配置化本体类型；不传时使用 `default`。
`build` 会额外输出 `formatted_ontology.json`，详见下文「输出结构」。

实例数量相关参数：

- `--max-extracted-instances N`：限制 LLM 从原文真实抽取后保留的实例数量；会同步清理被裁掉实例相关的实例链接和行动调用引用。
- `--instance-count N`：在 `formatted_ontology.json` 中额外生成 N 个演示实例，字段 `generated=true`；生产构建默认是 `0`，不得用占位节点掩盖真实实例缺失。

长文档抽取常见耗时较长。若进度条显示 `running>0` 但 `done=0`，通常表示正在等待 LLM 返回；可通过 `--llm-timeout`、`--llm-retries`、`--llm-concurrency` 临时调整请求超时、重试次数和并发数。

## 输出结构

`formatted_ontology.json` 把类（对象类型）与实例（具体个体）分开建模，方便业务系统直接消费：

| 顶层字段 | 说明 |
|---|---|
| `ontology` | 元数据（名称、描述、profile、时间戳） |
| `objects` | 对象类型表，每项含 `attributes`、反向实例索引，以及 `realInstanceCount`/`generatedInstanceCount`/`instanceCoverage` |
| `links` | 对象类型之间的链接 schema |
| `rules` | 结构化业务规则，含作用对象、AND/OR 条件、来源与对应行动 ID |
| `actions` | 行动定义（类层），含 `triggerRuleIds`、目标对象和 executor/计划/反馈字段 |
| `instances` | 具体实例，含 `attributeValues`（对齐类的属性，含 `attributeId`）和 `linkValues`（实例间关系，含 `linkId`、`targetInstanceId`） |
| `actionInvocations` | 具体行动调用，引用类层 `actionId`，`inputInstances` / `outputInstances` 指向具体实例 ID |

属性/链接/动作的 `*Code` 字段在各自表内全局唯一（重复时自动追加 `_2`、`_3` 后缀）；`description` 留作真正的说明位，示例值统一进 `sampleValue`。实例属性的 `dataType` 由多 chunk 同名属性的多数表决决定。

类 vs 实例的语义：
- `objects[*].attributes[*]` 是「类的属性定义」（金额是什么、是 number），实例的具体属性值在 `instances[*].attributeValues` 里，并通过 `attributeId` 回指 schema。
- `instances[*].objectId` 是实例所属类的真值，`objects[*].instanceIds` 是用于画布点开对象时快速查询的反向索引。
- `links` 描述「类之间存在某种关系」，`instances[*].linkValues` 描述「这个实例与那个实例有这条关系」，并通过 `linkId` 回指 schema。如果实例间出现了类层未声明的关系，formatter 会自动反推一条 `FormattedLink` 补齐 schema。
- `rules` 与普通三元组分开；`actions[*].triggerRuleIds` 回指触发规则，`actionInvocations` 描述「Alice 在 ... 执行了 ... 作用于 ...」并回指类层行动。
- 构建会额外写出 `build_quality.json`。无真实实例但仍有 schema/规则/行动引用的类保留并告警；无实例、无类关系、无规则/行动引用的真正孤立类默认隔离，不进入正式画布。

DeepSeek 小样本 smoke test：

```bash
scripts/deepseek_smoke.sh
```

分步 DeepSeek 流水线示例：

```bash
.venv/bin/ontology-construct extract \
  --input README.md \
  --output-dir outputs/run_001
```

本地 mock 流水线示例：

```bash
.venv/bin/ontology-construct extract \
  --input README.md \
  --output-dir outputs/run_001 \
  --backend mock

.venv/bin/ontology-construct status \
  --run-dir outputs/run_001

.venv/bin/ontology-construct merge \
  --local-dir outputs/run_001/local \
  --output outputs/run_001/global.json
```

使用 Qwen embedding 做模糊实体去重：

```bash
.venv/bin/ontology-construct merge \
  --local-dir outputs/run_001/local \
  --output outputs/run_001/global.json \
 --use-embedding
```

在 `schema-first` / 概念优先模式下，`--use-embedding` 会在概念聚类阶段调用
Qwen OpenAI-compatible embeddings，将名称不同但语义相近的候选概念合并；默认
embedding 模型为 `text-embedding-v4`，默认相似度阈值为 `0.9`。API key 查找
顺序为 `QWEN_EMBEDDING_API_KEY`、`DASHSCOPE_API_KEY`、`QWEN_API_KEY`。

继续导出标准 JSON：

```bash
.venv/bin/ontology-construct export \
  --ontology outputs/run_001/global.json \
  --format json \
  --output outputs/run_001/ontology.json
```

启动 API 服务：

```bash
.venv/bin/ontology-construct-api --host 0.0.0.0 --port 8000
```

API 支持上传 `.txt`、`.md`、`.markdown`、`.pdf`、`.docx`、`.json`、`.jsonl`：

- `POST /ontology/jobs`: 创建异步构建任务，表单参数包含 `file`、`profile`、`backend`、`instance_count`、`use_embedding`
- `POST /ontology/jobs/import`: 上传已有本体 JSON / JSONL，直接创建可展示和问答的已完成本体任务
- `GET /ontology/jobs/{jobId}/status`: 查询任务状态和 chunk 进度
- `GET /ontology/jobs/{jobId}/result`: 获取 `formatted_ontology.json`
