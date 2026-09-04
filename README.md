# LOM 本体构建与问答系统

LOM 是一个面向长文档和业务材料的本体构建演示系统。前端提供上传、构建、图谱浏览和问答界面；后端基于 FastAPI 封装本体构建流水线，支持从非结构化材料中抽取对象、属性、关系、行动和实例，并输出可视化图谱与标准 JSON。

当前仓库只包含代码、配置、测试和少量内置本体示例。`data/`、本地 `.env`、运行输出、虚拟环境、PDF/Word 原始材料不会提交到 Git。

当前产品融合的阶段计划、实际进展和跨会话交接信息见 [`PLANS.md`](PLANS.md)、[`PROGRESS.md`](PROGRESS.md) 与 [`docs/README.md`](docs/README.md)。仓库协作约束见 [`AGENTS.md`](AGENTS.md)。

## 功能概览

- 多文档构建：支持一次上传多个 `.txt`、`.md`、`.markdown`、`.pdf`、`.docx`、`.log`、`.json`、`.jsonl` 文件。
- 构建模式：
  - 标准抽取：按 chunk 调用 LLM 抽取局部本体，再合并成全局本体。
  - 概念优先：先抽取/泛化概念 schema，再围绕 schema 抽取实例。
- 本体类型配置：内置 `default`、`people_relation`、`business_process`、`industrial_process`、`operator_service` 等 profile。
- 结构化数据入口：支持上传 `.yaml/.yml` 配置文件，后端连接 PostgreSQL，按 hybrid 弱规范库路径构建 schema、实例和实例关系。
- 已有本体导入：支持上传 `formatted_ontology.json/jsonl`，直接加载图谱和问答。
- 图谱浏览：支持本体层和实例层切换，展示节点、关系、属性和邻域。
- 本体问答：基于当前构建/导入的本体进行问答，并返回可高亮节点与边。
- 本体智能体：绑定已完成本体版本，组合官方原子 Agent，支持 Pi Agent 协同对话、配置修改、发布和受凭证保护的外部调用。
- 追加文档更新：从当前已完成本体选择内置或自定义业务文档，约束对象/关系/规则/行动范围，确认节点级变更并在只读预览后生成新版本。
- 本体版本历史：左侧工具栏下方常驻当前本体名称与版本，可切换文档追加形成的历史版本，并统一高亮当前版本新增节点。
- 历史任务：后端保存 API 构建结果，前端可加载历史构建结果。
- CLI：后端包同时提供分块、抽取、合并、导出、完整构建等命令。

## 目录结构

```text
.
├── README.md
├── readme.txt                    # 极简启动命令备忘
├── index_V0427.html              # 前端入口页面，由 API 服务托管
├── frontend/
│   ├── app.js                    # 前端交互、上传、轮询、图谱和问答
│   ├── app.css                   # 前端样式
│   ├── ontology-workbench.js     # 可编辑本体画布与工作副本
│   ├── ontology-update.js        # 追加文档更新流程
│   ├── ontology-update.css       # 更新弹窗与预览样式
│   ├── ontology-history.js       # 本体版本历史、切换与新增节点高亮
│   └── ontology-history.css      # 添加元素菜单与版本历史样式
├── imported_ontologies/          # 可导入/展示的本体示例 JSON
└── ontology_construct/
    ├── pyproject.toml            # Python 项目与依赖
    ├── configs/default.yaml      # 默认模型、chunk、本体 profile 配置
    ├── prompts/                  # LLM 抽取与概念泛化提示词
    ├── src/ontology_construct/
    │   ├── api.py                # FastAPI 服务
    │   ├── cli.py                # CLI 构建流水线
    │   ├── input/                # 文档读取
    │   ├── preprocessor/         # 文本清洗与分块
    │   ├── extractor/            # chunk 级本体抽取
    │   ├── concept/              # 概念优先构建
    │   ├── merger/               # 本体合并与去重
    │   ├── output/               # formatted/canonical 输出
    │   ├── qa.py                 # 本体问答与图谱响应
    │   └── storage/              # JSON 保存与 SQLite 进度追踪
    └── tests/                    # 单元测试与 API 测试
```

## 环境要求

- Python 3.12
- `uv`
- 可访问的 OpenAI-compatible LLM 服务
  - 默认配置使用阿里云 DashScope OpenAI-compatible 接口。
  - 也支持 DeepSeek 和 mock 后端。

安装 `uv`：

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

## 本地部署与运行

进入后端目录并安装依赖：

```bash
cd ontology_construct
unset UV_PROJECT_ENVIRONMENT
uv sync --extra dev --python 3.12
```

配置环境变量。推荐复制 `.env.example` 为本地 `.env`：

```bash
cp .env.example .env
```

然后写入实际 key：

```bash
DASHSCOPE_API_KEY=你的 DashScope Key
DEEPSEEK_API_KEY=你的 DeepSeek Key
DEEPSEEK_API_KEY_2=第二个 DeepSeek Key（可选）
MOONSHOT_API_KEY=你的 Kimi Key
QWEN_EMBEDDING_API_KEY=你的 Qwen Embedding Key
QWEN_EMBEDDING_MODEL=qwen3.7-text-embedding
QWEN_EMBEDDING_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
```

`.env` 已被 `.gitignore` 忽略，不要提交。

启动 API 和前端：

```bash
uv run ontology-construct-api --host 127.0.0.1 --port 8000
```

浏览器打开：

```text
http://127.0.0.1:8000
```

如果部署到服务器，需要允许外部访问：

```bash
uv run ontology-construct-api --host 0.0.0.0 --port 8000
```

生产环境建议放在 Nginx 或其他反向代理后面，并限制上传大小、访问来源和 API key 权限。

## 前端使用流程

1. 打开 `http://127.0.0.1:8000`。
2. 在“构建设置”中选择本体类型、模型后端、构建模式和实例策略。
3. 点击“选择构建文档”，可一次选择多个文档。
4. 点击“开始构建本体”，前端会上传文件并轮询任务状态。
5. 构建完成后可切换“本体层/实例层”，查看节点详情和关系。
6. 在底部输入问题，后端会基于当前本体返回答案和图谱高亮信息。
7. 如已有 `formatted_ontology.json/jsonl`，可点击“上传已有本体”直接导入。
8. 如需要登记结构化数据源配置，可点击“上传结构化配置”上传 YAML。当前只保存配置，暂不执行数据库连接和抽取。
9. 加载已完成的后端本体后，可点击顶部“本体数据源”，选择补充文档和更新范围；分析变更、勾选候选并预览，最终确认会生成并加载一个新 job，原 job 保持不变。
10. 左侧工具栏下方的“当前本体”持续显示名称和版本；点击后可查看/切换版本链，当前版本相对父版本新增的节点会以统一绿色描边高亮。

## 第二阶段联调测试

仓库提供了包含对象、实例、规则和行动的测试文档：

```text
ontology_construct/tests/fixtures/phase2_cooling_system.txt
```

默认 chunk 配置会把当前纯业务样例拆成一个 1,338 token 的分块；测试/验收说明已经从业务原文中移除，避免模型把 UI 要求误抽成概念。先运行完整回归并启动服务：

```bash
cd ontology_construct
uv run pytest -q
uv run ontology-construct-api --host 127.0.0.1 --port 8000
```

浏览器打开 `http://127.0.0.1:8000`，在顶部选择已配置密钥的构建模型，然后点击“新建本体”→“选择构建文档”→“开始构建本体”。DeepSeek 用于文本理解和本体抽取；语义聚类单独使用 Qwen embedding，默认模型为 `qwen3.7-text-embedding`。若没有配置 embedding key，非结构化文档构建会自动跳过语义聚类并继续运行，任务状态中会记录降级原因。

也可用下面的请求明确关闭 embedding、采用标准抽取做后端烟雾测试；把 `kimi` 换成实际可用的 provider：

```bash
curl -X POST http://127.0.0.1:8000/ontology/jobs \
  -F files=@tests/fixtures/phase2_cooling_system.txt \
  -F profile=industrial_process \
  -F backend=kimi \
  -F construction_mode=standard \
  -F instance_count=0 \
  -F use_embedding=false
```

取得 `jobId` 后检查：

```bash
curl http://127.0.0.1:8000/ontology/jobs/<jobId>/status
curl http://127.0.0.1:8000/ontology/jobs/<jobId>/quality
curl 'http://127.0.0.1:8000/ontology/jobs/<jobId>/graph?view=schema'
curl 'http://127.0.0.1:8000/ontology/jobs/<jobId>/graph?view=instance'
```

状态中的 `qualityStatus` 应为 `passed`；若存在 `qualityWarnings`，先检查对应缺实例类或已隔离孤立类。任务目录的 `build_quality.json` 会给出逐类覆盖明细。验收时在“新建本体”的历史结果中加载该任务：本体画布应同时出现对象、规则和行动；点击对象应看到 `关联实例` 与 `来源分块`；切换“本体实例 3D”应看到独立实例节点和实例关系。

## API 接口

默认服务地址：`http://127.0.0.1:8000`

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/` | 返回前端页面 |
| `GET` | `/health` | 健康检查 |
| `GET` | `/ontology/profiles` | 获取可选本体 profile |
| `GET` | `/ontology/jobs` | 获取历史构建结果摘要 |
| `POST` | `/ontology/jobs` | 创建本体构建任务，支持 `files` 多文件字段 |
| `POST` | `/ontology/jobs/import` | 导入已有 formatted ontology JSON/JSONL |
| `POST` | `/ontology/structured-configs` | 上传结构化数据 YAML 配置 |
| `GET` | `/ontology/jobs/{jobId}/status` | 查询任务状态和 chunk 进度 |
| `GET` | `/ontology/jobs/{jobId}/quality` | 查询类—实例覆盖、孤立类和构建质量报告 |
| `GET` | `/ontology/jobs/{jobId}/result` | 获取 formatted ontology |
| `GET` | `/ontology/jobs/{jobId}/canonical` | 获取 schema/graph 标准输出 |
| `GET` | `/ontology/jobs/{jobId}/graph?view=schema` | 获取前端图谱数据，`view` 可为 `schema`、`instance`、`auto` |
| `POST` | `/ontology/jobs/{jobId}/question` | 基于当前本体问答 |
| `GET` | `/ontology/jobs/{jobId}/sources` | 获取本体名称、已有业务文档和内置补充文档 |
| `GET` | `/ontology/jobs/{jobId}/versions` | 获取 `baseJobId` 父子链形成的版本历史、变更摘要和新增节点 ID |
| `POST` | `/ontology/jobs/{jobId}/updates` | 上传补充文档并按范围异步分析更新影响 |
| `GET` | `/ontology/jobs/{jobId}/updates/{updateId}/plan` | 获取节点级变更方案 |
| `POST` | `/ontology/jobs/{jobId}/updates/{updateId}/preview` | 根据所选变更生成只读预览图 |
| `POST` | `/ontology/jobs/{jobId}/updates/{updateId}/confirm` | 确认并创建新的 completed job |
| `GET` | `/ontology/agent-catalog` | 获取官方原子 Agent 目录 |
| `GET/POST` | `/ontology/agents` | 列出或创建本体智能体 |
| `GET/PUT` | `/ontology/agents/{agentId}` | 读取或按 revision 修改智能体 |
| `POST` | `/ontology/agents/{agentId}/publish` | 发布/重新发布智能体 |
| `GET/POST` | `/ontology/agents/{agentId}/sessions` | 列出或新建智能体会话 |
| `POST` | `/ontology/agents/{agentId}/sessions/{sessionId}/messages` | Pi Agent 平台内对话 |
| `POST` | `/v1/ontology-agents/{invokeName}/invoke` | 携带 `X-Agent-Key` 外部调用已发布智能体 |

`POST /ontology/jobs` 常用表单参数：

| 参数 | 说明 |
|---|---|
| `files` | 一个或多个上传文档 |
| `profile` | 本体类型，默认 `default` |
| `backend` | LLM 后端，如 `qwen`、`deepseek`、`kimi`、`mock` |
| `construction_mode` | `standard` 或 `schema-first` |
| `supplemental_prompt` | 用户补充构建说明 |
| `instance_count` | 额外生成示例实例数量 |
| `max_extracted_instances` | 限制真实抽取实例数量 |
| `use_embedding` | 是否在合并时使用 embedding 模糊去重 |
| `max_concepts` | 概念优先模式的最大概念数 |
| `abstraction_level` | 概念抽象级别，`high`、`medium`、`low` |
| `drop_instances` | 概念优先模式是否丢弃实例 |
| `extract_real_instances` | 概念优先模式是否抽取真实实例 |

## CLI 使用

CLI 适合离线批处理、调试 prompt、生成中间文件。

进入后端目录：

```bash
cd ontology_construct
```

分块：

```bash
uv run ontology-construct chunk \
  --input README.md \
  --output outputs/chunks.jsonl
```

完整构建：

```bash
uv run ontology-construct build \
  --input README.md \
  --output-dir outputs/run_001 \
  --backend qwen-plus \
  --profile business_process \
  --construction-mode schema-first \
  --max-concepts 50 \
  --instance-count 3
```

分步执行：

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

使用 mock 后端快速验证链路：

```bash
uv run ontology-construct build \
  --input README.md \
  --output-dir outputs/mock_run \
  --backend mock
```

更完整的运行命令、API 调用、实例重建和配置项说明见
[`ontology_construct/docs/run-and-config.md`](ontology_construct/docs/run-and-config.md)。

## 输出文件

API 任务输出默认保存在：

```text
ontology_construct/outputs/api_jobs/{jobId}/
```

常见文件：

| 文件 | 说明 |
|---|---|
| `job.json` | 任务状态、参数、输入文件记录 |
| `progress.sqlite` | chunk 进度数据库 |
| `local/*.json` | chunk 级局部本体 |
| `global.json` | 合并后的内部全局本体 |
| `formatted_ontology.json` | 前端、导入和问答使用的格式化本体 |
| `schema.json` | canonical schema/type layer |
| `graph.json` | canonical graph/instance layer |
| `build_quality.json` | chunk 完整性、逐类真实实例覆盖、孤立类隔离和质量告警 |

`formatted_ontology.json` 顶层结构：

| 字段 | 说明 |
|---|---|
| `ontology` | 本体元数据 |
| `objects` | 对象类型和属性 schema |
| `links` | 对象类型之间的关系 schema |
| `rules` | 结构化规则定义及其作用对象、触发行动 |
| `actions` | 行动定义 |
| `instances` | 具体实例、属性值和实例间关系 |
| `actionInvocations` | 行动调用实例 |

## 实现原理

整体流程：

```text
前端上传文档
  -> FastAPI 创建任务并保存输入文件
  -> 读取 PDF/DOCX/JSON/JSONL/文本内容
  -> 文本清洗
  -> 按段落和长度切分 chunk
  -> LLM 抽取局部本体
  -> 对失败 chunk 做工作流级补偿重试（仍失败则任务失败）
  -> 合并实体、属性、关系、行动和实例
  -> 按合并后的 schema 再读原文补全真实实例，并与首轮实例去重合并
  -> 实例连通性与行动派生
  -> 质量门禁：类—实例覆盖检查、孤立类隔离
  -> 输出 formatted/canonical JSON
  -> 前端轮询状态并加载图谱
  -> 用户围绕当前本体问答
```

关键实现：

- `input/text_reader.py`：按文件类型读取文本。PDF 使用 `pypdf`，DOCX 读取 `word/document.xml`，JSON/JSONL 会格式化为文本。
- `.log` 文件不会直接作为长文本输入；读取时会先解析日志级别、时间戳、key-value 字段，按事件模板聚合、计数并保留典型样例，再输出压缩后的日志摘要文本，避免大日志生成数百个 LLM chunk。
- `preprocessor/chunker.py`：将长文本切成稳定 chunk，控制 token 规模和 overlap。
- `extractor/local_extractor.py`：调用 LLM，对单个 chunk 输出局部本体 JSON。
- `extractor/parser.py`：解析 LLM 返回，容忍 fenced JSON，并校验为内部模型。
- `merger/ontology_merger.py`：归并重复实体、属性和三元组。
- `concept/`：概念优先模式，先抽候选概念、泛化 schema，再补实例和实例关系。
- `instance_builder.py`：标准模式与概念优先模式共用的受 schema 约束实例补全；保留首轮真实实例和行动调用。
- `build_quality.py`：构建完整性、逐类实例覆盖和孤立类隔离报告。
- `output/formatted_ontology.py`：生成前端和问答使用的对象/关系/实例结构。
- `output/canonical_ontology.py`：生成 schema 与 graph 两层标准输出。
- `qa.py`：把本体压缩成问答上下文，调用 LLM 返回答案和高亮信息。
- `api.py`：封装任务管理、文件上传、历史记录、图谱接口和问答接口。
- `frontend/app.js`：管理上传、任务轮询、历史加载、图谱渲染和问答交互。

任务状态写入采用原子替换，避免前端轮询时读到半写入的 `job.json`。

## 配置说明

主要配置在 `ontology_construct/configs/default.yaml`：

- `chunk.size` / `chunk.overlap`：chunk 大小与重叠。
- `llm.backend`：默认 LLM 后端。
- `llm.base_url` / `llm.model` / `llm.api_key_env`：OpenAI-compatible 模型配置。
- `llm.timeout_seconds` / `llm.max_retries` / `llm.concurrency`：请求超时、重试和并发。
- `merge.embedding_*`：embedding 去重相关配置。
- `concept.*`：概念优先模式的候选概念、关系词表、实例抽取参数。
- `concept.instance_llm_concurrency`：实例抽取和实例关系构建的独立并发；候选概念抽取仍使用 `llm.concurrency`。
- `concept.instance_relation_source_char_limit` / `concept.max_instance_relation_pairs` / `concept.min_relation_pairs_per_instance`：控制实例关系判断上下文长度、候选关系对总预算，以及每个实例至少保留的候选关系对数量，用于减少实例孤立节点。
- `instance_builder.*`：schema 合并后独立补全最终实例层；标准与 schema-first 均启用，日志/JSON 优先规则抽取，普通文本可用 LLM 小批补充。
- `instance_postprocess.*`：实例图后处理；可用 LLM 处理离散点、补实例关系，并带确定性兜底补边。
- `build_quality.*`：失败 chunk/实例批次补偿重试、完整构建要求、标准模式实例补全和孤立类隔离策略。
- `replay.enabled`：是否记录构建过程回放日志；关闭后新任务不再写 `job_events.jsonl`，可节省存储。
- `qa_logging.enabled`：是否记录问答/推理日志；开启后写入 `qa_events.jsonl`，包含 question、prompt、rawAnswer、parsedAnswer 和推理元信息。
- `frontend_build.*`：前端简化模式下隐藏的构建默认参数，例如默认模型、本体类型、构建模式、最大概念数、实例策略、LLM 超时和并发。
- `ontology_profiles`：不同业务场景的本体名称、描述、prompt 和可选领域背景。

前端“开始构建本体”不会再展示大量高级控件，默认值来自 `frontend_build`：

```yaml
frontend_build:
  default_profile: default
  default_backend: jiutian
  construction_mode: schema-first
  max_concepts: 50
  semantic_clustering: true
  instance_mode: real
  instance_count: 1
  qa_context_mode: graph_rag
  qa_fallback_enabled: true
  llm_timeout:
  llm_retries:
  llm_concurrency:
  min_concept_evidence:
  abstraction_level:
```

其中空值表示使用后端对应模块的默认配置。修改该配置后重启 API 服务即可生效。

构建回放日志可通过以下配置开关控制：

```yaml
replay:
  enabled: true
```

设为 `false` 后，新构建任务不会记录回放日志；已存在 `job_events.jsonl` 的历史任务仍可回放。

问答/推理日志可通过以下配置开关控制：

```yaml
qa_logging:
  enabled: true
```

设为 `false` 后，新问答请求不会写入 `qa_events.jsonl`。

环境变量：

| 变量 | 说明 |
|---|---|
| `DASHSCOPE_API_KEY` | Qwen 文本抽取默认 key |
| `QWEN_API_KEY` | Qwen 备用 key |
| `QWEN_EMBEDDING_API_KEY` | Qwen embedding key |
| `QWEN_EMBEDDING_MODEL` | 覆盖 embedding 模型，默认 `qwen3.7-text-embedding` |
| `QWEN_EMBEDDING_BASE_URL` | 覆盖 embedding endpoint |
| `DEEPSEEK_API_KEY` / `DEEPSEEK_API_KEY_2..5` | DeepSeek 主/备用 key，失败后按顺序切换 |
| `DEEPSEEK_BASE_URL` / `DEEPSEEK_MODEL` | 覆盖 DeepSeek endpoint 和模型 |
| `MOONSHOT_API_KEY` / `KIMI_API_KEY` | Kimi key，前者为官方变量名 |
| `KIMI_BASE_URL` / `KIMI_MODEL` | 覆盖 Kimi endpoint 和模型 |
| `KIMI_REASONING_EFFORT` | Kimi K3 推理强度，如 `low`、`high`、`max` |
| `QWEN_BASE_URL` | 覆盖 Qwen base URL |
| `QWEN_MODEL` | 覆盖 Qwen 模型 |

## 结构化数据 YAML

上传 YAML 后会创建结构化构建任务，后端以只读方式连接 PostgreSQL，默认走弱规范库 hybrid 路径：`meta.*`、主外键、注释、采样数据、字段名、值重叠、embedding 和 LLM 都作为证据来源生成 schema mapping 和关系 mapping；实例和实例关系仍由数据库行、FK、显式关系表、事件字段和可验证 join 规则批量生成。

可直接上传的示例文件见 `ontology_construct/examples/structured_pg_factory.yaml`，根据本地数据库名、用户名环境变量和密码环境变量调整即可。

本地测试结构化 YAML 前，需要先启动 PostgreSQL 服务；否则后端连接 `localhost:5432` 会失败。

```bash
brew services start postgresql@16
```

查看服务状态：

```bash
brew services list
```

测试结束后如果不再需要数据库，可以关闭后台服务：

```bash
brew services stop postgresql@16
```

当前本地模拟库使用 `ontology_simulate` 数据库，Homebrew PostgreSQL 默认用户为当前系统用户，例如 `shuaichen`。如果使用 `user_env` / `password_env`，需要在 `ontology_construct/.env` 或后端进程环境里配置：

```bash
PGUSER=shuaichen
PGPASSWORD=
```

结构化 hybrid 构建需要 LLM 和 embedding key；非结构化文档构建缺少 embedding key 时会跳过语义聚类并在 job status 中记录原因。Qwen/DashScope 可配置：

```bash
DASHSCOPE_API_KEY=你的 DashScope key
QWEN_EMBEDDING_API_KEY=你的 embedding key
QWEN_EMBEDDING_MODEL=qwen3.7-text-embedding
```

示例：

```yaml
version: 1
source:
  type: postgresql
  connection:
    host: 127.0.0.1
    port: 5432
    database: business_db
    user_env: PGUSER
    password_env: PG_PASSWORD
    schema: public
metadata:
  tables:
    meta_tables: meta.meta_tables
    meta_columns: meta.meta_columns
    entity_map: meta.meta_entity_map
    relation_map: meta.meta_relation_map
construction:
  mode: hybrid
  use_meta: true
  use_information_schema: true
  use_pg_comments: true
  use_constraints: true
  use_sampling: true
  use_embedding: true
  use_llm: true
  class_merge_similarity: 0.88
  relation_candidate_similarity: 0.82
  value_overlap_threshold: 0.6
  min_relation_confidence: 0.7
ontology:
  name: 工业制造结构化数据本体
  profile: industrial_process
instances:
  sample:
    per_entity_table: 20
    per_reference_table: 50
    per_quality_table: 20
  relation_sample:
    per_relation_type: 100
  event_sample:
    per_event_type: 50
llm:
  enabled: true
  usage:
    schema_mapping: true
    relation_mapping: true
    schema_polish: true
    instance_generation: false
```

不要把数据库密码直接写进 YAML；接口会拒绝 `password` 字段，应使用 `password_env` 引用环境变量。v1 不执行 YAML 中的自由 SQL 查询。LLM 只生成和修正 schema/关系 mapping，不能逐行生成实例；实例按采样限额从数据库行生成。

## 测试

前端语法检查：

```bash
node --check frontend/app.js
```

后端全量测试：

```bash
cd ontology_construct
uv run pytest
```

当前测试覆盖 API、多文档上传、YAML 配置上传、文本读取、chunk、LLM client、解析器、合并、canonical 输出、问答等模块。

## Git 与部署注意事项

- 根目录是唯一 Git 仓库，`ontology_construct` 只是普通子目录。
- `data/` 默认忽略，不会上传 GitHub。如需示例数据，应单独准备脱敏小样本。
- `.env` 默认忽略，生产和本地 key 不应入库。
- `outputs/` 默认忽略，构建产物不应入库。
- 当前服务默认开放 CORS，生产部署前应按实际域名收紧。
- 上传文件大小、并发构建数量和 LLM 调用成本需要在生产环境额外限制。
