# 长文本本体构建系统实现规划

## 1. 功能边界

项目目标是实现一个命令行驱动的长文本本体构建流水线：

```text
输入文档 -> 文本预处理 -> 智能分块 -> LLM 局部抽取 -> 全局归并 -> 可选精炼 -> 多格式导出
```

首版聚焦离线批处理，不做 Web UI，不做人工审核界面，不依赖长会话记忆。LLM 只负责语义抽取和少量同义判断，全局状态、重试、合并和导出都由程序维护。

## 2. 阶段拆分

### Phase 0: 项目骨架

要实现的能力：

- 建立 Python src-layout 项目结构
- 定义 CLI 入口 `ontology-construct`
- 定义配置加载、日志、输出目录约定
- 提供最小 smoke test

建议文件：

```text
pyproject.toml
configs/default.yaml
src/ontology_construct/cli.py
src/ontology_construct/config.py
tests/test_cli.py
```

验收标准：

- `ontology-construct --help` 可执行
- `pytest` 通过
- 默认配置能被加载并转换为强类型对象

### Phase 1: 输入与预处理

要实现的能力：

- TXT 输入读取
- PDF / DOCX 输入预留接口，首版可先不启用
- 文本清洗：空白归一、页眉页脚预留、无效字符过滤
- 智能分块：优先段落，其次句子，最后长度截断
- chunk 元数据记录：id、起止字符、token 估算、章节或页码占位

建议模块：

```text
src/ontology_construct/input/
src/ontology_construct/preprocessor/cleaner.py
src/ontology_construct/preprocessor/chunker.py
src/ontology_construct/models/chunk.py
```

验收标准：

- 同一输入和配置下 chunk 结果稳定
- chunk 之间按配置保留 overlap
- 每个 chunk 都能追溯到原文位置

### Phase 2: 数据模型与本体存储

要实现的能力：

- 定义 `Entity`、`Triple`、`LocalOntology`、`GlobalOntology`
- 区分实体关系与字面量属性
- 保留 source chunk、confidence、aliases、attributes
- 支持模型 JSON 序列化和反序列化

建议模块：

```text
src/ontology_construct/models/ontology.py
src/ontology_construct/storage/json_store.py
```

验收标准：

- LLM 抽取结果可以被校验并落盘
- 本体对象能从 JSON 恢复，恢复后语义等价

### Phase 3: LLM 局部抽取 Map 阶段

要实现的能力：

- OpenAI-compatible / Qwen-compatible LLM client
- Prompt 模板管理
- 结构化 JSON 输出解析
- 并发控制、超时、重试
- 单 chunk 失败不终止整体任务

建议模块：

```text
prompts/extract_ontology.j2
src/ontology_construct/llm/client.py
src/ontology_construct/extractor/local_extractor.py
src/ontology_construct/extractor/parser.py
```

验收标准：

- mock LLM 下可稳定返回 `LocalOntology`
- 非法 JSON、超时、空结果都有明确错误状态
- 并发数由配置控制

### Phase 4: 断点续跑与任务状态

要实现的能力：

- SQLite 记录 chunk 处理状态
- 已成功 chunk 跳过
- 失败 chunk 可单独重试
- 保存原始 LLM 响应、解析结果和错误信息

建议模块：

```text
src/ontology_construct/storage/progress_tracker.py
src/ontology_construct/storage/sqlite_store.py
```

验收标准：

- 中断后重新运行不会重复调用已完成 chunk
- 可以列出 pending、done、failed chunk

### Phase 5: Reduce 合并归一

要实现的能力：

- 名称标准化：大小写、空白、全角半角等
- 精确实体去重
- alias 合并
- relation 去重，保留最高 confidence 和全部 source chunks
- 基于 embedding 的候选同义实体发现
- 可选 LLM 判断候选同义实体

建议模块：

```text
src/ontology_construct/merger/normalizer.py
src/ontology_construct/merger/entity_deduplicator.py
src/ontology_construct/merger/ontology_merger.py
```

验收标准：

- 相同实体名稳定合并为一个 canonical entity
- 重复三元组只保留一条
- source_chunks 不丢失
- embedding / LLM 同义判断可通过配置关闭

### Phase 6: 输出导出

要实现的能力：

- 标准 JSON 导出

建议模块：

```text
src/ontology_construct/storage/json_store.py
```

验收标准：

- `merge` 输出 `global.json`
- `export --format json` 可以将全局本体重写为标准缩进 JSON
- 导出 JSON 可以被 `GlobalOntology` 重新加载

### Phase 7: RAG 精炼，可选

要实现的能力：

- 为 chunk 构建向量索引
- 按实体名召回 top-k chunk
- 调用 LLM 补全属性和关系
- 合并精炼结果，保留来源和置信度

建议模块：

```text
src/ontology_construct/refiner/vector_store.py
src/ontology_construct/refiner/ontology_refiner.py
prompts/refine_entity.j2
```

验收标准：

- `enable_refinement=false` 时主流程不依赖向量库
- 精炼只更新目标实体相关属性和关系
- 精炼新增信息能追溯到召回 chunk

## 3. CLI 规划

首版建议提供以下命令：

```bash
ontology-construct chunk --input docs/book.txt --output outputs/chunks.jsonl
ontology-construct extract --input docs/book.txt --output-dir outputs/run_001
ontology-construct merge --local-dir outputs/run_001/local --output outputs/run_001/global.json
ontology-construct export --ontology outputs/run_001/global.json --format json --output outputs/run_001/ontology.json
ontology-construct build --input docs/book.txt --output-dir outputs/run_001
ontology-construct status --run-dir outputs/run_001
```

`build` 是完整流水线入口，其他命令用于调试和断点恢复。

## 4. 配置规划

建议默认配置：

```yaml
chunk:
  size: 1500
  overlap: 200

llm:
  backend: openai_compatible
  model: qwen-plus
  temperature: 0.1
  max_tokens: 2000
  timeout_seconds: 60
  max_retries: 3
  concurrency: 10

merge:
  embedding_model: paraphrase-multilingual-MiniLM-L12-v2
  similarity_threshold: 0.92
  use_llm_synonym_judgment: true

refine:
  enabled: false
  top_k: 5

output:
  formats:
    - json
```

## 5. 推荐 MVP 范围

第一轮实现建议只做：

- TXT 输入
- 文本清洗和 chunk
- mock LLM + 真实 LLM client 接口
- `LocalOntology` / `GlobalOntology` 数据模型
- 精确去重 merge
- JSON 导出
- SQLite 断点续跑
- CLI `build`、`chunk`、`status`

暂缓实现：

- PDF / DOCX 解析
- embedding 模糊去重
- LLM 同义判断
- RAG 精炼
- 非 JSON 导出格式
- 人工审核界面

原因：MVP 应先打通可测试、可恢复、可导出的主链路；embedding、RAG 和多格式导出都可以在主链路稳定后增量接入。

## 6. 测试策略

单元测试：

- chunk 边界、overlap、元数据
- ontology model 校验
- LLM 响应 parser
- entity normalizer
- merger 去重逻辑
- exporters 输出合法性

集成测试：

- mock LLM 跑完整 `build`
- 模拟 chunk 失败后断点续跑
- 从 local ontology 合并到 global ontology 并导出 JSON

不建议在默认测试中调用真实 LLM。真实 LLM 测试放到手动 smoke test 或带环境变量开关的测试里。

## 7. 关键风险

- LLM 输出不稳定：必须有结构化解析、重试和错误落盘
- 长文本 chunk 过多：必须支持并发限制和断点续跑
- 实体同义归并误合并：MVP 先做精确去重，模糊合并默认保守
- 属性和关系边界不清：数据模型里要明确字面量属性与实体关系
- source 丢失：任何合并和导出都必须保留来源 chunk，便于审计

## 8. 后续实现顺序

建议按以下顺序推进：

1. Phase 0 项目骨架
2. Phase 1 chunker
3. Phase 2 数据模型
4. Phase 3 mock extractor 和 parser
5. Phase 4 progress tracker
6. Phase 5 精确 merge
7. Phase 6 JSON export
8. 接入真实 LLM
9. 增加 embedding 去重
10. 增加 RAG 精炼和高级导出
