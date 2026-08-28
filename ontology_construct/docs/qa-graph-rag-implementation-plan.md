# 本项目问答子图裁剪与 Graph RAG 改造计划

## 目标

把当前本体问答从 `direct` 全量上下文或简单 `filtered` 子图裁剪，升级为稳定的 Graph RAG 推理链路：

```text
问题解析 -> 多路召回 -> schema 约束 -> 图算法扩展/重排 -> token budget 压缩 -> 结构化证据 prompt -> LLM 回答与高亮
```

核心目标：

- 避免把完整 `objects/links/instances/actionInvocations` 输入模型导致上下文溢出。
- 提升召回质量，减少当前字符串匹配 + 固定 2 跳 BFS 的漏召回和噪声扩散。
- 让线上推理 prompt 与后续 SFT 训练样本格式保持一致。
- 保留现有 `/ontology/jobs/{jobId}/question` API 和前端交互形态，降低迁移成本。

## 当前问题

当前主要逻辑在 `ontology_construct/src/ontology_construct/qa.py`：

- `context_mode == "direct"` 时，`_render_direct_qa_prompt()` 把完整 schema 和 graph JSON 输入模型。
- `context_mode == "filtered"` 时，`_select_context_graph()` 使用 `_match_nodes()` 做关键词命中，再固定扩展最多 2 跳，最后硬截断 `120 nodes / 180 edges`。
- `_render_qa_prompt()` 虽然节点和边列表来自裁剪子图，但仍追加全图 `可用节点 id` 和 `可用边端点`，大图下仍会膨胀。
- 历史配置中前端默认 `qa_context_mode: direct`，会使主路径仍可能是全量上下文；本计划实施后默认改为 `graph_rag`。

给定示例 job 的规模：

- `formatted_ontology.json` 约 1.74 MB。
- schema: 50 个对象、479 条关系。
- instance: 1227 个实例、1670 条实例关系。

这个规模下 `direct` 模式很容易超过模型上下文或造成回答质量下降。

## 设计原则

1. schema 和 instance 分层处理。
   schema 是类型、关系、规则空间；instance 是事实空间。线上检索应互相约束，而不是混在一个大文本里截断。

2. 多路召回优先于单一算法。
   规则匹配负责高精度，BM25/文本匹配负责宽召回，图算法负责结构相关性。

3. 图扩展按相关性和 token budget 控制。
   不再固定 2 跳和固定节点数，而是根据问题类型、节点/边得分、路径价值和预算动态选择。

4. prompt 输入结构化。
   不再传完整 JSON 或无解释子图，改为传可审计的证据包。

5. 可渐进上线。
   新逻辑先作为新的 context mode 引入，例如 `graph_rag`，稳定后再切默认值。

## 推荐架构

新增一个 QA retrieval 层，建议拆到新模块：

```text
ontology_construct/src/ontology_construct/qa_retrieval.py
ontology_construct/src/ontology_construct/qa_prompt.py
```

候选数据结构建议：

```python
class RetrievedNode(BaseModel):
    id: str
    label: str
    type: str
    attrs: dict[str, Any]
    score: float
    reasons: list[str]

class RetrievedEdge(BaseModel):
    source: str
    target: str
    rel: str
    score: float
    reasons: list[str]

class RetrievalResult(BaseModel):
    relevant_schema_nodes: list[GraphNode]
    relevant_schema_edges: list[GraphEdge]
    evidence_nodes: list[RetrievedNode]
    evidence_edges: list[RetrievedEdge]
    evidence_paths: list[list[str]]
    trace: list[str]
    budget: dict[str, Any]
```

`qa.py` 中 `answer_question()` 保持对外入口，只负责：

```text
formatted ontology -> graph/schema views -> retriever -> prompt renderer -> LLM -> normalize/highlight
```

## 分阶段实施

### 阶段 1：止血改造

目标：快速避免上下文溢出。

改动：

- 将 `ontology_construct/configs/default.yaml` 中 `frontend_build.qa_context_mode` 从 `direct` 改为 `graph_rag`。
- 在 `_render_qa_prompt()` 中移除全图 `可用节点 id` 和 `可用边端点`，只保留裁剪子图范围。
- 给 `QuestionRequest.context_mode` 增加可选值，例如 `graph_rag`。如果暂时不加新模式，则先优化 `filtered`。
- 增加 token budget 参数，例如：
  - `qa.max_prompt_chars`
  - `qa.max_schema_items`
  - `qa.max_evidence_nodes`
  - `qa.max_evidence_edges`

验收：

- 示例 job 在实例层问答时 prompt 长度明显小于全量 `formatted_ontology.json`。
- 不再向 LLM 发送全图 edge endpoint 列表。
- 旧测试通过，新增 prompt 长度测试。

### 阶段 2：多路 seed 召回

目标：替换当前 `_match_nodes()` 的简单字符串包含逻辑。

召回通道：

- exact match:
  - node id
  - instanceName/objectName
  - instanceCode/objectCode
  - 属性值中的编码、批次、事件号、工单号
- lexical match:
  - 中文/英文 token 子串
  - 简单 BM25 或 TF-IDF 评分
  - relation name / linkInfo 匹配
- type match:
  - 问题中出现“工位、设备、事件、工序、物料、工单、质量”等词时召回对应类型节点。
- relation intent match:
  - “上游、下游、影响、依赖、属于、执行、处理、流转、异常、返工”等映射到关系方向或 meta-path。

输出 seed 时保留来源和分数：

```text
built_inst_000002 score=1.0 reason=exact_id
生产工单 score=0.8 reason=type_match
流转到 score=0.7 reason=relation_intent
```

验收：

- 单元测试覆盖：
  - id 精确命中。
  - 中文实例名命中。
  - 属性值命中。
  - 类型词命中。
  - 关系词命中。

### 阶段 3：schema-gated instance retrieval

目标：schema 和 instance 分层召回，减少事实图噪声。

流程：

```text
1. 先从问题召回相关 schema object/link。
2. 如果问题直接命中实例，反查实例类型，并补入其 schema。
3. 根据相关 schema 限制 instance 候选类型和可走关系。
4. 在被允许的 instance 子图上做图算法扩展。
5. 回填必要 schema 说明，形成 RELEVANT_SCHEMA。
```

注意：

- 不是固定“schema 找实例”。如果用户直接问 `built_inst_000002`，应先命中实例，再查 schema 约束。
- schema 可作为类型过滤器、关系过滤器和路径模板来源。

验收：

- 对实例精确问题，能定位实例并补充其对象类型和相关关系。
- 对类型问题，能先定位 schema，再在实例层召回对应类型。
- 子图中无关类型比例下降。

### 阶段 4：图算法扩展与重排

目标：用图相关性替换固定 2 跳 BFS。

推荐算法组合：

1. Personalized PageRank / Random Walk with Restart
   - seed 节点作为 restart 分布。
   - 在允许的 instance 子图上计算节点相关性。
   - 对高出度/高度数 hub 节点降权。

2. Shortest Path / Steiner-like Path
   - 当问题命中多个 seed 时，补全 seed 之间的最短路径。
   - 优先保留路径上的边和节点。

3. Meta-path constraint
   - 针对工业场景定义常见路径：
     - `事件 -> 工序 -> 设备`
     - `物料 -> 工序 -> 产物`
     - `缺陷 -> 检测 -> 返工`
     - `工单 -> 工序 -> 资源`
   - 按问题意图选择可扩展路径模板。

基础评分建议：

```text
node_score =
  0.40 * text_relevance
+ 0.30 * graph_relevance
+ 0.15 * type_relevance
+ 0.10 * relation_context_relevance
- 0.05 * hub_penalty
```

边评分建议：

```text
edge_score =
  0.35 * source_score
+ 0.35 * target_score
+ 0.20 * relation_match
+ 0.10 * path_bonus
```

验收：

- 相同问题下，PPR 子图比 2 跳 BFS 更小。
- 多 seed 问题能保留连接路径。
- 高度数 hub 不会挤占大部分 budget。

### 阶段 5：token budget 压缩

目标：按 token/字符预算组装证据，而不是按固定节点边数量。

压缩策略：

- schema:
  - 只保留相关对象类型、关系类型、关键属性。
  - 若 schema 过大，优先保留与 evidence 节点类型相关的 schema。
- nodes:
  - seed 节点必须保留。
  - 路径节点优先。
  - 高分节点优先。
  - 属性只保留关键字段，例如编码、名称、状态、时间、来源字段。
- edges:
  - seed 相关边优先。
  - shortest path 边优先。
  - 关系词命中的边优先。
- trace:
  - 控制为短文本，避免占用主要预算。

建议 prompt 格式：

```text
[QUESTION]
...

[RELEVANT_SCHEMA]
ObjectType:
  ...
RelationType:
  ...

[EVIDENCE_SUBGRAPH]
Nodes:
  id | type | label | key_attrs | score | reasons
Edges:
  source -> target | rel | score | reasons
Paths:
  A --rel--> B --rel--> C

[RETRIEVAL_TRACE]
- seed: ...
- expansion: ...
- budget: ...

[ANSWER_REQUIREMENT]
只能基于上述证据回答。若证据不足，说明缺少哪些节点、关系或属性。
输出保持 <think></think><answer></answer>。
```

验收：

- prompt 长度稳定小于配置预算。
- prompt 中不出现完整 `formatted_ontology.json`。
- 模型输出仍可被现有 `_parse_qa_response()` 解析，或新增解析器兼容。

### 阶段 6：前端与配置

配置建议：

```yaml
frontend_build:
  qa_context_mode: graph_rag

qa:
  max_prompt_chars: 24000
  max_schema_items: 80
  max_evidence_nodes: 80
  max_evidence_edges: 120
  max_paths: 12
  ppr_alpha: 0.85
  ppr_iterations: 30
  hub_penalty_enabled: true
  include_retrieval_trace: true
```

前端保持现有请求字段：

```json
{
  "question": "...",
  "graphView": "instance",
  "contextMode": "graph_rag"
}
```

可选增加调试字段：

- `debugRetrieval: true`
- 后端 response 中返回 `retrieval`，前端调试面板展示 seed、子图大小、prompt 字符数。

## 测试计划

新增或更新 `ontology_construct/tests/test_qa.py`：

- `test_graph_rag_does_not_send_full_graph`
- `test_graph_rag_exact_instance_seed`
- `test_graph_rag_schema_type_seed`
- `test_graph_rag_relation_intent_seed`
- `test_graph_rag_shortest_path_between_seeds`
- `test_graph_rag_prompt_budget`
- `test_graph_rag_fallback_when_no_seed`

建议增加一个小型固定图 fixture：

- schema: 工单、工序、设备、事件、缺陷。
- instance: 20-30 个节点，包含一两个 hub。
- 覆盖上游、下游、影响分析、状态查询、路径追踪。

## 迁移策略

1. 保留 `direct`，仅用于调试和小图。
2. 保留旧 `filtered`，用于回滚。
3. 新增 `graph_rag`，先在配置中灰度使用。
4. 通过日志记录每次问答的：
   - context mode
   - seed 数量
   - evidence nodes/edges
   - prompt chars
   - fallback 是否触发
5. 稳定后将 `frontend_build.qa_context_mode` 默认切为 `graph_rag`。

## 风险与注意事项

- 召回不足会导致模型无法回答，所以必须支持 evidence 不足时明确说明，而不是强行编造。
- PPR 对边方向的处理要按任务区分：上游/下游类问题应尊重方向；关联类问题可用无向图。
- schema 关系和 instance 关系的命名要统一映射，否则 relation intent 会丢失。
- 高度数节点需要降权，但不能完全剔除，因为某些 hub 在流程图里确实是关键节点。
- 不建议第一版就依赖外部 embedding 服务；先用规则 + lexical + 图算法可以更稳定落地。
