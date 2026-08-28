# SFT 训练数据适配 Graph RAG 推理计划

## 目标

让 `/Users/shuaichen/Documents/data_intelligence/ontology_model/jiutian_sft` 的 SFT 数据生成逻辑与本项目新的 Graph RAG 线上推理格式一致，避免 train-inference mismatch。

结论先行：

- 训练主流程大概率不用重写。
- 需要重点改 SFT 数据生成逻辑和样本 prompt 格式。
- 当前训练仍可保留 Alpaca 三列：`instruction/input/output`。
- `input` 应从旧的 `[SCHEMA] + [GRAPH 子图] + 问题` 升级为 Graph RAG 证据包。

## 当前 SFT 项目状态

目标仓库：

```text
/Users/shuaichen/Documents/data_intelligence/ontology_model/jiutian_sft
```

关键文件：

```text
src/jiutian_sft/industrial_generator.py
src/jiutian_sft/dataset_builder.py
src/jiutian_sft/training.py
configs/jiutian_sft.yaml
configs/llama_factory/sft_jiutian_35b.yaml
outputs/industrial/industrial_samples.jsonl
outputs/data/train.jsonl
```

当前数据流：

```text
ontology_construct job_dir(schema.json, graph.json)
 -> industrial_generator.prepare_industrial_data()
 -> outputs/industrial/industrial_samples.jsonl
 -> dataset_builder.build_dataset()
 -> outputs/data/train.jsonl
 -> LLaMA-Factory SFT
```

当前训练映射在 `dataset_builder.py` 中是 Alpaca 格式：

```python
ALPACA_COLUMNS = {
    "formatting": "alpaca",
    "columns": {"prompt": "instruction", "query": "input", "response": "output"},
}
```

当前工业样本已经包含：

```text
schema_injected
full_graph_size
subgraph_size
context_policy
anchor_nodes
input_chars
```

说明它已经在训练“schema + task subgraph 推理”，不是简单全图训练。

## 为什么仍然需要改

线上如果改为 Graph RAG，模型看到的输入会从旧格式：

```text
[SCHEMA]
ObjectType:
...
RelationType:
...

[GRAPH]
节点（N个，子图，锚节点：...）：
...
关系：
...

问题：...
```

变成新格式：

```text
[QUESTION]
...

[RELEVANT_SCHEMA]
...

[EVIDENCE_SUBGRAPH]
...

[RETRIEVAL_TRACE]
...

[ANSWER_REQUIREMENT]
...
```

如果 SFT 仍主要训练旧格式，模型会学到旧上下文组织方式，线上新 prompt 的指令遵循和证据使用能力会打折。

因此 SFT 需要同步训练“基于 Graph RAG 证据包回答”的能力。

## 总体改造方案

新增或改造 SFT 数据生成层，使它复用或对齐本项目线上 retriever：

```text
任务生成器给出问题/标准答案/锚点
 -> Graph RAG retriever 召回证据
 -> Graph RAG prompt renderer 生成 input
 -> 原有 output 保持 <think></think><answer></answer>
 -> dataset_builder 继续按 Alpaca 三列输出
```

建议优先实现“格式对齐”，再实现“算法完全复用”。

## 样本格式设计

### instruction

保留当前角色定位，但补充证据约束：

```text
你是一位本体推理专家。请基于给定的本体定义和证据子图回答问题。
本体定义说明有哪些对象类型、关系类型和可用规则；证据子图是从完整图中检索得到的相关事实。
推理时必须同时利用两者：用本体定义确认规则，用证据子图查找事实。
只能基于给定证据回答；如果证据不足，请说明缺少哪些节点、关系或属性。
推理过程用 <think></think> 包裹，最终答案用 <answer></answer> 包裹。
```

### input

推荐新格式：

```text
[QUESTION]
如果 built_inst_000002 发生取消或失效，会影响哪些下游对象？

[RELEVANT_SCHEMA]
ObjectType:
  生产工单: ...
  加工工序: ...
  生产资源: ...

RelationType:
  包含工序_生产工单_加工工序: 生产工单 -> 加工工序 ...
  流转到: 加工工序 -> 加工工序 ...
  执行于_加工工序_生产资源: 加工工序 -> 生产资源 ...

[EVIDENCE_SUBGRAPH]
Seeds:
  built_inst_000002 | 生产工单 | 0001号生产工单 | reason=exact_anchor

Nodes:
  built_inst_000002 | 生产工单 | 0001号生产工单 | score=1.000 | reason=seed
  built_inst_000003 | 加工工序 | 钢卷进厂检验 | score=0.912 | reason=ppr,path

Edges:
  built_inst_000002 -> built_inst_000003 | 包含工序_生产工单_加工工序 | score=0.955 | reason=path
  built_inst_000003 -> built_inst_000009 | 流转到 | score=0.931 | reason=downstream

Paths:
  built_inst_000002 --包含工序_生产工单_加工工序--> built_inst_000003 --流转到--> built_inst_000009

[RETRIEVAL_TRACE]
- seed: built_inst_000002 from anchor
- schema gate: 生产工单, 加工工序, 生产资源
- graph expansion: downstream directed traversal + PPR rerank
- budget: nodes=33, edges=40, chars=4515

[ANSWER_REQUIREMENT]
只能基于上述证据回答。输出 <think></think><answer></answer>。
```

### output

短期保留当前格式：

```text
<think>
{
  "reasoning_steps": [...]
}
</think>
<answer>{...}</answer>
```

如果线上需要 highlight，可在后续新增输出字段：

```json
{
  "answer": "...",
  "highlight_nodes": [...],
  "highlight_edges": [...]
}
```

但第一阶段不建议同时改输出格式，避免训练和评测范围过大。

## 需要修改的文件

### 1. `src/jiutian_sft/industrial_generator.py`

当前职责：

- 发现 job_dir。
- 读取 `graph.json` 和 `schema.json`。
- 调用 `cot_gen.pipeline.task_generator.TaskGenerator` 生成样本。
- `_annotate_sample()` 增加 `context_policy/input_chars/anchor_nodes` 等字段。

建议改造：

- 新增参数：
  - `context_format: str = "graph_rag"`
  - `include_retrieval_trace: bool = True`
  - `max_schema_items: int`
  - `max_evidence_nodes: int`
  - `max_evidence_edges: int`
  - `max_prompt_chars: int`
- 在 `sample` 生成后，不直接使用原始 `sample["input"]`，而是：
  1. 从 sample 中解析 question、anchor、task_type。
  2. 调用 Graph RAG evidence builder。
  3. 用新 renderer 重建 `row["input"]`。
  4. 写入 retrieval metadata。

建议新增字段：

```json
{
  "context_format": "graph_rag_v1",
  "retrieval_policy": "exact_bm25_ppr_path_v1",
  "retrieval_seed_count": 1,
  "retrieval_node_count": 33,
  "retrieval_edge_count": 40,
  "retrieval_schema_count": 6,
  "retrieval_prompt_chars": 4515,
  "retrieval_has_trace": true
}
```

兼容策略：

- 保留旧格式开关 `context_format="legacy"`。
- 默认新生成数据使用 `graph_rag`。

### 2. 新增 `src/jiutian_sft/graph_rag_formatter.py`

建议职责：

- 把 retriever 输出格式化为训练 input。
- 保证线上和训练 prompt 的 section 名称一致。
- 控制属性字段和字符预算。

核心函数：

```python
def render_graph_rag_input(
    *,
    question: str,
    relevant_schema: Any,
    evidence_nodes: list[Any],
    evidence_edges: list[Any],
    evidence_paths: list[Any],
    retrieval_trace: list[str],
    answer_requirement: str,
) -> str:
    ...
```

### 3. 新增 `src/jiutian_sft/graph_rag_retriever.py`

短期可以在 SFT 仓库内实现轻量版，长期建议和本项目共享同一套算法。

职责：

- 从 `graph.json/schema.json` 构建索引。
- 根据 sample 的 question/anchor/task_type 生成 evidence。
- 输出与线上 `RetrievalResult` 对齐的数据结构。

第一版可以先用 task generator 已经给出的子图作为 evidence，再改格式。
第二版再接入 exact/BM25/PPR/shortest path。

### 4. `src/jiutian_sft/dataset_builder.py`

训练映射可不变。

建议增加健康检查：

- `context_format` 分布。
- `retrieval_policy` 分布。
- `retrieval_prompt_chars` 分布。
- 训练集中 `legacy` 和 `graph_rag_v1` 比例。
- 超预算样本进入 long eval，不进入 train。

新增 health 字段：

```json
{
  "train_by_context_format": {...},
  "train_by_retrieval_policy": {...},
  "avg_retrieval_node_count": ...,
  "avg_retrieval_edge_count": ...
}
```

### 5. `configs/jiutian_sft.yaml`

建议新增数据配置：

```yaml
data:
  context_format: graph_rag
  retrieval_policy: exact_bm25_ppr_path_v1
  include_retrieval_trace: true
  max_prompt_chars: 32768
  max_schema_items: 80
  max_evidence_nodes: 80
  max_evidence_edges: 120
  legacy_sample_ratio: 0.2
```

`train.cutoff_len` 可暂时保持 32768。即使底座支持 256K，也不建议训练样本长期依赖超长输入。

## 数据配比建议

第一版不要完全丢弃旧格式，建议：

```text
graph_rag_v1: 70%
legacy_schema_graph: 20%
evidence_insufficient / noisy_retrieval: 10%
```

原因：

- `graph_rag_v1` 负责对齐线上新格式。
- `legacy_schema_graph` 保留旧能力和回滚兼容。
- `evidence_insufficient/noisy_retrieval` 训练模型在证据不足时拒绝编造。

后续稳定后可调整为：

```text
graph_rag_v1: 85%+
legacy_schema_graph: <=10%
negative/noisy: 5-10%
```

## 负样本与噪声样本

建议新增几类训练样本：

1. 证据不足：
   - 问题问到某实例，但 evidence 中没有相关边。
   - 期望回答：无法确定，缺少某关系或目标节点。

2. schema 不支持：
   - 问题要求沿某关系推理，但 relevant schema 中没有该关系。
   - 期望回答：当前本体定义不支持该推理。

3. 噪声节点：
   - evidence 中混入低分无关节点。
   - 期望模型忽略无关节点，只使用高分/路径证据。

4. 方向错误：
   - 上游/下游任务中加入反向边。
   - 期望模型尊重关系方向。

这些样本对线上 Graph RAG 很重要，因为 retriever 不可能永远完美。

## 评测计划

新增 eval 维度：

- `eval_graph_rag_in_dist.jsonl`
- `eval_graph_rag_ood.jsonl`
- `eval_evidence_insufficient.jsonl`
- `eval_noisy_retrieval.jsonl`

评测指标：

- answer exact / set match。
- affected_ids / path nodes 的集合准确率。
- 关系方向准确率。
- evidence groundedness：答案中的节点/边是否都出现在 evidence 中。
- insufficient evidence accuracy：证据不足时是否拒答或说明缺失证据。

可在 `eval_local.py` 中增加检查：

```text
1. 解析 <answer> JSON。
2. 检查 answer ids 是否都在 EVIDENCE_SUBGRAPH Nodes 中。
3. 检查 answer edges 是否都在 EVIDENCE_SUBGRAPH Edges 或 Paths 中。
4. 按 task_type 聚合准确率。
```

## 实施顺序

### 第一步：格式对齐

- 新增 `graph_rag_formatter.py`。
- 在 `industrial_generator.py` 中支持 `context_format=graph_rag`。
- 第一版可以复用当前 task generator 生成的子图，只改变 input section 格式。
- 生成少量样本做人工检查。

验收：

- `industrial_samples.jsonl` 中出现 `context_format=graph_rag_v1`。
- `input` 包含 `[QUESTION] [RELEVANT_SCHEMA] [EVIDENCE_SUBGRAPH] [RETRIEVAL_TRACE] [ANSWER_REQUIREMENT]`。
- `output` 保持可训练格式。

### 第二步：接入真实 retriever

- 实现或复用线上 Graph RAG retriever。
- 用 exact/BM25/PPR/shortest path 重新生成 evidence。
- 写入 retrieval metadata。

验收：

- `retrieval_node_count/retrieval_edge_count/retrieval_prompt_chars` 正常。
- 子图大小受预算控制。
- 对相同 anchor/task，evidence 覆盖标准答案所需节点和边。

### 第三步：数据混合与健康检查

- `dataset_builder.py` 增加 context format 统计。
- 加入 `legacy_sample_ratio`。
- 超预算样本进入 long eval，不进入 train。

验收：

- `health_report.json` 有新统计。
- train 中无超预算工业样本。
- eval 中包含 long/noisy/insufficient 样本。

### 第四步：小规模训练/评测

- 用少量 graph_rag_v1 样本 smoke train。
- 跑 `eval_local.py` 或服务器评测。
- 比较旧格式和新格式在：
  - 关系遍历
  - 上下游影响
  - 路径解释
  - 证据不足
  上的表现。

## 与本项目联动要求

为了避免两套逻辑漂移，建议最终做到以下一致：

- section 名称一致：
  - `[QUESTION]`
  - `[RELEVANT_SCHEMA]`
  - `[EVIDENCE_SUBGRAPH]`
  - `[RETRIEVAL_TRACE]`
  - `[ANSWER_REQUIREMENT]`
- retriever metadata 字段一致。
- relation/type 命名规范一致。
- prompt budget 规则一致。
- 线上 `qa.py` 的 prompt renderer 和 SFT `graph_rag_formatter.py` 至少用相同模板文本。

长期最佳方案是把 Graph RAG retriever/prompt renderer 抽成共享包，两个项目共同依赖；短期可以复制实现，但要用测试样例保证输出格式一致。

## 风险与注意事项

- 不要第一版同时改变输入格式和输出格式，否则定位效果变化困难。
- 不要把所有样本都做得过短；模型仍需要学习中等规模子图的推理。
- 不要只训练完美 evidence；线上 retriever 一定会有噪声和漏召回。
- 如果训练 cutoff 仍是 32768，数据生成阶段必须严格控制 `input_chars`。
- 如果线上使用 `graph_rag`，SFT 评测集也必须包含 `graph_rag_v1`，否则评测不能反映真实上线效果。

