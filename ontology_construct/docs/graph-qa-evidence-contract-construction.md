# Graph QA Evidence Contract 子图裁剪设计施工文档

> **［已废弃］** 本文档为初版方案，已被 `docs/graph-qa-evidence-contract-v2.md` 取代，
> 差异决策见 v2 附录 A。请勿按本文档施工。

## 1. 背景与目标

当前问答链路已经从 `direct` 全量上下文切到 `graph_rag`，但现有 `graph_rag` 仍然是一个统一裁剪器：

```text
question -> seed 召回 -> schema gate -> PPR/路径 -> Seeds/Nodes/Edges/Paths prompt
```

这能解决全量 JSON 溢出问题，但面对真实用户问题仍有风险：

- 属性类、关系类、路径类、影响分析类问题需要不同证据结构。
- seed 过多时，`Nodes` 会挤掉真正关键的 `Edges` / `Paths`。
- 图谱原始边方向不等于业务推理方向，尤其是影响分析。
- 线上 prompt 一旦改变，SFT 样本格式也必须同步，否则会产生 train-inference mismatch。

本设计将子图裁剪从“一个检索函数”升级为稳定的 **Graph QA Evidence Contract**。

核心目标：

```text
证据协议一次定稳
检索算法允许迭代
线上推理和 SFT 样本共用同一 evidence renderer
```

后续可以持续优化 seed、PPR、Graph RAG、embedding rerank、路径搜索，但只要输出同一个 evidence contract，就不需要每次算法调整都重新定义训练格式。

## 2. 设计边界

### 2.1 本轮必须稳定的内容

以下内容属于模型输入输出协议，后续应谨慎变更：

- prompt section 名称。
- `QUERY_PLAN` 字段语义。
- `ANSWERABLE_CHECK` 字段语义。
- `CORE_EVIDENCE` 基本证据类型。
- `ANSWER_REQUIREMENT`。
- `<think></think><answer></answer>` 输出格式。
- answer JSON 的基础字段。

### 2.2 后续可迭代的内容

以下内容属于证据生成算法，可以在不改变 contract 的前提下持续优化：

- seed 召回算法。
- 中文分词、编号识别、实体归一化。
- PPR / Random Walk 参数。
- shortest path / k-shortest paths。
- embedding / reranker / GraphRAG。
- relation semantic map。
- token budget 分配。
- supporting nodes/edges 排序。

### 2.3 是否需要重新 SFT 的判断

```text
改 evidence contract -> 需要重构样本并增量训练/重训
改 retrieval algorithm -> 通常只需要评估，不一定训练
```

本施工文档定义的是 `graph_qa_evidence_v1` contract。后续训练数据应围绕该 contract 累积。

## 3. 总体架构

目标链路：

```text
Question
  -> QueryPlanner
  -> EntityResolver
  -> EvidenceRetriever
  -> EvidenceBuilder
  -> PromptRenderer
  -> LLM
  -> Parser / Fallback
```

模块建议：

```text
ontology_construct/src/ontology_construct/
  qa.py                  # 对外主入口，保持 API 兼容
  qa_planner.py          # 新增：问题类型识别和检索计划
  qa_evidence.py         # 新增：稳定 evidence 数据结构
  qa_retrieval.py        # 改造：底层召回、PPR、路径、图算法
  qa_prompt.py           # 改造：渲染 graph_qa_evidence_v1 prompt
```

`qa.py` 只负责调度：

```text
formatted ontology -> schema/instance graph
 -> plan_question()
 -> retrieve_for_plan()
 -> build_qa_evidence()
 -> render_graph_qa_evidence_prompt()
 -> llm
 -> parse / fallback
```

## 4. Evidence Contract v1

### 4.1 Prompt 固定结构

`graph_qa_evidence_v1` prompt 固定为：

```text
[QUESTION]
...

[QUERY_PLAN]
contract_version=graph_qa_evidence_v1
task_type=...
intent=...
retrieval_policy=...
primary_entities=...

[ANSWERABLE_CHECK]
required_entities_found=true/false
core_evidence_found=true/false
missing_entities=...
missing_relations=...
missing_paths=...

[CORE_EVIDENCE]
...

[SUPPORTING_SCHEMA]
ObjectType:
...
RelationType:
...

[SUPPORTING_NODES]
...

[SUPPORTING_EDGES]
...

[RETRIEVAL_TRACE]
...

[ANSWER_REQUIREMENT]
只能基于上述证据回答。
如果 ANSWERABLE_CHECK 显示证据不足，必须说明缺少哪些节点、关系或路径。
不得引用未出现在 CORE_EVIDENCE/SUPPORTING_* 中的节点或边。
输出保持 <think></think><answer></answer> 格式。
```

硬性要求：

- `[CORE_EVIDENCE]` 永不截断。
- `[ANSWER_REQUIREMENT]` 永不截断。
- `[QUERY_PLAN]` 和 `[ANSWERABLE_CHECK]` 永不截断。
- `[SUPPORTING_NODES]` 和 `[SUPPORTING_EDGES]` 可截断。
- 如果发生截断，只能截断 supporting 区域，并在 `[RETRIEVAL_TRACE]` 标记。

### 4.2 数据模型

建议新增 `qa_evidence.py`：

```python
from typing import Any, Literal
from pydantic import BaseModel, Field

QueryTaskType = Literal[
    "entity_attribute",
    "schema_explain",
    "relation_lookup",
    "path_query",
    "impact_analysis",
    "upstream_trace",
    "downstream_trace",
    "general",
]

EvidenceItemType = Literal[
    "target_node",
    "attribute_table",
    "schema_definition",
    "relation_set",
    "candidate_path",
    "impact_chain",
    "trace_chain",
    "insufficient_evidence",
]

class EvidenceNode(BaseModel):
    id: str
    label: str
    type: str
    attrs: dict[str, Any] = Field(default_factory=dict)
    score: float = 0.0
    reasons: list[str] = Field(default_factory=list)

class EvidenceEdge(BaseModel):
    source: str
    target: str
    rel: str
    score: float = 0.0
    reasons: list[str] = Field(default_factory=list)
    semantic_direction: str | None = None

class EvidencePath(BaseModel):
    path_type: Literal["directed", "undirected", "impact_reverse", "dependency", "trace"]
    nodes: list[EvidenceNode]
    edges: list[EvidenceEdge]
    explanation: str = ""

class QueryPlan(BaseModel):
    contract_version: str = "graph_qa_evidence_v1"
    task_type: QueryTaskType
    intent: str = "general"
    retrieval_policy: str
    primary_entity_hints: list[str] = Field(default_factory=list)
    relation_hints: list[str] = Field(default_factory=list)

class AnswerableCheck(BaseModel):
    required_entities_found: bool = False
    core_evidence_found: bool = False
    missing_entities: list[str] = Field(default_factory=list)
    missing_relations: list[str] = Field(default_factory=list)
    missing_paths: list[str] = Field(default_factory=list)

class CoreEvidenceItem(BaseModel):
    item_type: EvidenceItemType
    title: str
    nodes: list[EvidenceNode] = Field(default_factory=list)
    edges: list[EvidenceEdge] = Field(default_factory=list)
    paths: list[EvidencePath] = Field(default_factory=list)
    facts: list[str] = Field(default_factory=list)
    explanation: str = ""

class QAEvidence(BaseModel):
    contract_version: str = "graph_qa_evidence_v1"
    query_plan: QueryPlan
    answerable_check: AnswerableCheck
    primary_entities: list[EvidenceNode] = Field(default_factory=list)
    core_evidence: list[CoreEvidenceItem] = Field(default_factory=list)
    supporting_schema_nodes: list[EvidenceNode] = Field(default_factory=list)
    supporting_schema_edges: list[EvidenceEdge] = Field(default_factory=list)
    supporting_nodes: list[EvidenceNode] = Field(default_factory=list)
    supporting_edges: list[EvidenceEdge] = Field(default_factory=list)
    retrieval_trace: dict[str, Any] = Field(default_factory=dict)
```

## 5. 问题类型与检索计划

### 5.1 问题类型

第一版固定支持：

| task_type | 典型问题 | 核心证据 |
| --- | --- | --- |
| `entity_attribute` | `0011号生产工单状态是什么` | target node + attribute table |
| `schema_explain` | `什么是质量门控点` | schema definition + schema relations |
| `relation_lookup` | `0011号生产工单用了哪些资源` | relation set |
| `path_query` | `A 到 B 经过哪些步骤` | candidate paths |
| `impact_analysis` | `激光干涉仪1坏了会影响0011号生产工单吗` | impact chain |
| `upstream_trace` | `这个缺陷来源于哪里` | trace chain |
| `downstream_trace` | `这个工单后续影响哪些节点` | trace chain |
| `general` | 无法明确分类的问题 | budgeted mixed evidence |

### 5.2 QueryPlanner 规则

新增 `qa_planner.py`。

规则第一版用关键词即可，后续可替换为轻量分类模型。

优先级建议：

```text
impact_analysis > path_query > entity_attribute > relation_lookup > upstream/downstream_trace > schema_explain > general
```

关键词示例：

```text
impact_analysis:
  坏了, 故障, 异常, 失效, 停机, 报警, 会影响, 是否影响, 影响哪些

path_query:
  经过哪些步骤, 路径, 链路, 从...到..., 怎么到达, 流程

entity_attribute:
  状态, 属性, 时间, 编码, 数量, 是多少, 什么状态

relation_lookup:
  用了哪些, 包含哪些, 属于哪个, 执行于, 处理了哪些, 关联哪些

upstream_trace:
  来源, 上游, 前置, 依赖, 谁导致, 根因

downstream_trace:
  下游, 后续, 输出, 流转, 影响范围

schema_explain:
  什么是, 定义, 概念, 类型, 关系类型
```

## 6. Seed 召回与降噪

### 6.1 Seed 分级

当前 seed 过多的根因是弱匹配直接进入 prompt。必须将 seed 分级：

```python
SeedTier = Literal[
    "exact_entity",
    "exact_id",
    "exact_code",
    "schema_type",
    "relation_term",
    "attribute_value",
    "weak_lexical",
]
```

保留策略：

- `exact_entity` 永远进入 primary entities。
- `exact_id` 永远进入 primary entities。
- `exact_code` 只有完整编码命中才进入 primary entities。
- `schema_type` 只作为 schema gate，不直接展开所有实例。
- `relation_term` 只用于关系过滤和边加分。
- `attribute_value` 需要长度和唯一性过滤。
- `weak_lexical` 只参与 rerank，不进入 prompt seeds。

### 6.2 编号匹配规则

必须避免 `1`、`001`、`11` 这类短值召回大量节点。

建议规则：

- 长度小于 3 的纯数字不作为 `exact_code`。
- 问题中出现 `0011号生产工单` 时，优先识别完整实体短语。
- 编码值必须满足边界匹配，不能让 `0011` 命中 `0001_11` 这类无关编码。
- 对 `0011号生产工单` 这类模式，生成实体候选 phrase：
  - `0011号生产工单`
  - `0011`
  - `生产工单`
  其中只有完整 phrase 是强 seed，后两者是弱约束。

### 6.3 Seed 数量预算

默认预算：

```text
primary_entities <= 6
candidate_seeds <= 12
weak seeds 不直接渲染
```

如果强 seed 超过预算，按以下顺序保留：

```text
exact_entity > exact_id > exact_code > anchor param > unique attribute > type constrained match
```

## 7. Relation Semantic Map

图谱原始边方向是建模方向，业务推理方向需要单独定义。

新增 relation semantic map：

```python
RELATION_SEMANTICS = {
    "包含": {
        "structural": "parent_to_child",
        "impact": "child_to_parent",
        "trace_downstream": "parent_to_child",
        "trace_upstream": "child_to_parent",
    },
    "属于": {
        "structural": "child_to_parent",
        "impact": "child_to_parent",
        "trace_downstream": "child_to_parent",
        "trace_upstream": "parent_to_child",
    },
    "执行于": {
        "structural": "process_to_resource",
        "impact": "resource_to_process",
        "trace_downstream": "process_to_resource",
        "trace_upstream": "resource_to_process",
    },
    "使用": {
        "structural": "process_to_resource",
        "impact": "resource_to_process",
        "trace_downstream": "process_to_resource",
        "trace_upstream": "resource_to_process",
    },
    "处理": {
        "structural": "process_to_material",
        "impact": "material_to_process",
        "trace_downstream": "process_to_material",
        "trace_upstream": "material_to_process",
    },
    "来源于": {
        "structural": "target_to_source",
        "impact": "source_to_target",
        "trace_downstream": "source_to_target",
        "trace_upstream": "target_to_source",
    },
    "影响": {
        "structural": "source_to_target",
        "impact": "source_to_target",
        "trace_downstream": "source_to_target",
        "trace_upstream": "target_to_source",
    },
}
```

第一版可用字符串包含匹配，例如 `rel.startswith("包含")`、`"执行于" in rel`。

## 8. 各问题类型施工细节

### 8.1 entity_attribute

目标：回答节点属性、状态、时间、编号。

检索策略：

- 找一个或少量强 seed。
- 不跑大范围 PPR。
- 取目标节点完整关键属性。
- 补充最多 5 条一跳关系用于消歧。

`CORE_EVIDENCE`：

```text
TargetNode:
  inst:538 | 生产工单 | 0011号生产工单

AttributeTable:
  实例编码=0011_3
  状态=...
  时间=...
```

### 8.2 schema_explain

目标：解释本体概念或关系类型。

检索策略：

- 优先召回 schema node / schema edge。
- instance 只作为可选例子，最多 3 个。
- 不发送大规模实例子图。

`CORE_EVIDENCE`：

```text
SchemaDefinition:
  质量门控点: ...
RelatedRelationTypes:
  ...
```

### 8.3 relation_lookup

目标：查询某实体关联对象。

检索策略：

- 识别目标实体。
- 识别关系词或目标类型。
- 按 schema relation 和 relation semantic map 扩展。
- 只保留相关关系集合。

`CORE_EVIDENCE`：

```text
RelationSet:
  inst:538 --包含--> inst:542
  inst:542 --执行于--> inst:411
```

### 8.4 path_query

目标：回答 A 到 B 经过哪些步骤。

检索策略：

- 至少两个强 seed。
- 先跑 directed shortest path。
- 再跑 undirected shortest path 作为候选。
- 可扩展 k-shortest paths，默认 `k=3`。
- 路径节点和边必须进入 `CORE_EVIDENCE`。

`CORE_EVIDENCE`：

```text
CandidatePath(path_type=directed):
  A --rel1--> B --rel2--> C
```

如果 directed 无路径但 undirected 有路径：

```text
CandidatePath(path_type=undirected):
  A --rel?-- B --rel?-- C
Explanation:
  原始图中未找到符合方向的路径，但存在无向连通路径。
```

### 8.5 impact_analysis

目标：回答某节点异常是否影响另一节点，或影响哪些节点。

这是重点类型，不能按普通 downstream 处理。

检索策略：

1. 识别 cause entity，例如设备、资源、缺陷、事件、工序。
2. 识别 affected candidate，例如工单、订单、工序、物料。
3. 按 relation semantic map 构造 impact propagation graph。
4. 从 cause entity 做 BFS / shortest path。
5. 若存在到 affected candidate 的 impact path，则生成 impact chain。
6. 若用户问“影响哪些”，返回 top affected chains。

示例：

原图结构：

```text
inst:538 0011号生产工单 --包含--> inst:542 定位精度激光检测
inst:542 定位精度激光检测 --执行于--> inst:411 激光干涉仪1
```

用户问题：

```text
激光干涉仪1坏了会影响0011号生产工单吗
```

业务影响链应渲染为：

```text
ImpactChain:
  cause_entity:
    inst:411 | 制造资源 | 激光干涉仪1

  affected_candidate:
    inst:538 | 生产工单 | 0011号生产工单

  dependency_chain:
    inst:411 | 激光干涉仪1
    <- 执行于 -
    inst:542 | 0011号生产工单-定位精度激光检测
    <- 包含 -
    inst:538 | 0011号生产工单

  reasoning_hint:
    目标工单包含使用该资源的检测工序，因此资源异常会影响该工单相关检测步骤。
```

注意：这里的箭头表达的是“影响传播方向”，不是简单复用原始边方向。

### 8.6 upstream_trace / downstream_trace

目标：来源追溯或后续影响范围。

检索策略：

- 使用 relation semantic map，而不是简单使用原始边方向。
- 默认深度 4，支持配置。
- 对 hub 节点降权。
- 输出 trace chain，而不是大节点列表。

## 9. Token Budget 策略

### 9.1 分区预算

默认 `max_prompt_chars=24000` 时建议：

```text
QUESTION + QUERY_PLAN + ANSWERABLE_CHECK: <= 2,000
CORE_EVIDENCE: <= 8,000，永不截断
SUPPORTING_SCHEMA: <= 4,000
SUPPORTING_EDGES: <= 4,000
SUPPORTING_NODES: <= 4,000
RETRIEVAL_TRACE + ANSWER_REQUIREMENT: <= 2,000
```

预算不足时裁剪顺序：

```text
弱 supporting nodes
低分 supporting edges
schema 描述长文本
retrieval trace 详细项
```

禁止裁剪：

```text
QUESTION
QUERY_PLAN
ANSWERABLE_CHECK
CORE_EVIDENCE
ANSWER_REQUIREMENT
```

### 9.2 Prompt renderer 要求

当前 `_fit_budget()` 是按行从头保留，会导致 `Nodes` 挤掉 `Edges/Paths`。必须改成分区预算。

实现方式：

```text
render required sections
render core evidence
render supporting schema with local budget
render supporting edges with local budget
render supporting nodes with local budget
render trace and answer requirement
join sections
if still over budget, only压缩 supporting sections
```

## 10. Fallback 策略

fallback 不承担完整业务推理，只做保守降级。

当前已改造为：

- 传递失败原因：`parse_failed`、`empty_response`、`model_unanswerable`。
- 限制最多 10 个节点、10 条边。
- 命中多个节点时做候选最短路径检查。
- `answer.raw` 存 JSON 调试信息。

后续可追加 retrieval 失败原因：

```text
retrieval_no_seed
retrieval_no_core_evidence
retrieval_no_path
prompt_truncated_supporting
prompt_truncated_core_evidence
```

其中 `prompt_truncated_core_evidence` 应视为严重错误，正常实现中不应出现。

## 11. SFT 对齐要求

线上和 SFT 必须共用同一个 evidence contract。

SFT 侧当前链路在：

```text
/Users/shuaichen/Documents/data_intelligence/ontology_model/jiutian_sft
/Users/shuaichen/Documents/data_intelligence/ontology_model/cot_generation
```

关键点：

- `jiutian_sft` 是训练外壳。
- 工业样本的 Graph RAG input 主要由 `cot_generation/src/cot_gen/graph_rag/input_builder.py` 构造。
- 当前 `cot_generation/src/cot_gen/graph_rag/vendor/qa_prompt.py` 仍是旧 `Seeds/Nodes/Edges/Paths` 格式。

SFT 同步策略：

```text
graph_rag_v1        保留少量兼容样本
graph_qa_evidence_v1  作为新主训练格式
legacy             仅少量保留，避免遗忘
```

训练样本 input 必须使用同一 section：

```text
[QUESTION]
[QUERY_PLAN]
[ANSWERABLE_CHECK]
[CORE_EVIDENCE]
[SUPPORTING_SCHEMA]
[SUPPORTING_NODES]
[SUPPORTING_EDGES]
[RETRIEVAL_TRACE]
[ANSWER_REQUIREMENT]
```

answer 建议结构：

```json
{
  "answer_status": "answerable",
  "conclusion": "...",
  "evidence": ["..."],
  "highlight_nodes": ["..."],
  "highlight_edges": [["source", "target"]]
}
```

证据不足：

```json
{
  "answer_status": "insufficient_evidence",
  "missing_nodes": [],
  "missing_relations": [],
  "missing_paths": [],
  "explanation": "当前证据不足，无法确定答案。"
}
```

## 12. 实施步骤

### 阶段 0：保留现有改动并补文档

- 保留当前 fallback 改造。
- 新增本施工文档。
- 不立即改变线上默认行为。

### 阶段 1：实现 contract 数据结构

新增：

```text
qa_planner.py
qa_evidence.py
```

改造：

```text
qa_prompt.py
```

验收：

- 可以用 mock evidence 渲染完整 `graph_qa_evidence_v1` prompt。
- 必需 section 在 prompt 中顺序稳定。
- budget 截断不会裁掉 `CORE_EVIDENCE`。

### 阶段 2：实现 seed 分级和问题分类

改造 `qa_retrieval.py`：

- 增加 `SeedCandidate`。
- 增加 seed tier。
- 限制 primary seed 数量。
- 增加短数字/弱匹配降噪。

新增 `qa_planner.py`：

- 支持 8 类 task_type。
- 输出 `QueryPlan`。

验收：

- `激光干涉仪1坏了会影响0011号生产工单吗` 只把 `激光干涉仪1` 和 `0011号生产工单` 作为 primary entities。
- 不再把 `0001` 到 `0050` 大量生产工单塞进 primary seeds。

### 阶段 3：实现 path 和 impact core evidence

新增或改造：

```text
build_candidate_paths()
build_impact_chains()
build_trace_chains()
```

实现 relation semantic map。

验收：

- 对 `激光干涉仪1坏了会影响0011号生产工单吗`，`CORE_EVIDENCE` 包含：

```text
inst:411 激光干涉仪1
<- 执行于 -
inst:542 0011号生产工单-定位精度激光检测
<- 包含 -
inst:538 0011号生产工单
```

- prompt 中即使达到预算，也必须保留上述 impact chain。

### 阶段 4：接入 answer_question

`qa.py` 中：

```text
if context_mode == "graph_rag":
    plan = plan_question(...)
    retrieval = retrieve_for_plan(...)
    evidence = build_qa_evidence(...)
    prompt = render_graph_qa_evidence_prompt(...)
```

保留：

- `direct`
- `filtered`
- 旧解析逻辑
- fallback 逻辑

验收：

- API 形态不变。
- 前端无需改动。
- 回答失败时 `answer.raw` 能看到 fallback reason。

### 阶段 5：同步 SFT 生成逻辑

在 `ontology_model/cot_generation`：

- 同步 `qa_planner.py`、`qa_evidence.py`、`qa_prompt.py`。
- `input_builder.py` 改为生成 `graph_qa_evidence_v1`。
- `trace_augmentor.py` 增加 evidence contract 相关 reasoning step。
- `negative_sampler.py` 增加 `missing_core_path`、`direction_trap` 等负样本。

在 `jiutian_sft`：

- 默认工业样本格式改为 `graph_qa_evidence_v1`。
- `health_report` 增加：
  - `train_by_contract_version`
  - `train_by_qa_task_type`
  - `avg_core_evidence_count`
  - `prompt_truncated_core_evidence_count`

## 13. 测试计划

### 13.1 本项目单元测试

新增测试：

- `test_plan_impact_analysis`
- `test_plan_path_query`
- `test_seed_tier_exact_entity_priority`
- `test_seed_short_numeric_not_primary`
- `test_prompt_keeps_core_evidence_under_budget`
- `test_prompt_truncates_supporting_nodes_first`
- `test_impact_chain_reverse_dependency`
- `test_path_query_keeps_candidate_path`
- `test_schema_explain_does_not_include_full_instances`
- `test_answer_requirement_always_present`

更新测试：

- 默认 `graph_rag` 仍可用。
- `direct` 仍发送完整图。
- fallback 测试继续通过。

验证命令：

```bash
cd ontology_construct
uv run pytest tests/test_qa.py tests/test_config.py
uv run pytest
```

### 13.2 SFT 生成测试

在 `ontology_model` 中新增或更新测试：

- graph_qa_evidence_v1 input section 完整。
- 样本 input 不包含完整 `instances` JSON。
- impact_analysis 样本包含 `ImpactChain`。
- path_query 样本包含 `CandidatePath`。
- negative sample 输出 `answer_status=insufficient_evidence`。
- output 中引用的节点都存在于 input evidence。

建议命令：

```bash
cd /Users/shuaichen/Documents/data_intelligence/ontology_model
PYTHONPATH="$PWD/jiutian_sft/src:$PWD/cot_generation/src:$PWD/training/src" \
python -m pytest jiutian_sft/tests cot_generation/tests
```

## 14. 验收用例

### 14.1 影响分析

问题：

```text
激光干涉仪1坏了会影响0011号生产工单吗
```

期望：

- task_type=`impact_analysis`
- primary entities 包含：
  - `inst:411 激光干涉仪1`
  - `inst:538 0011号生产工单`
- `CORE_EVIDENCE` 包含 impact chain。
- prompt 不因节点过多截断掉 impact chain。
- 模型应回答“会影响”，并说明影响的是定位精度激光检测相关工序。

### 14.2 路径查询

问题：

```text
A 到 B 经过哪些步骤
```

期望：

- task_type=`path_query`
- `CORE_EVIDENCE` 包含 candidate paths。
- supporting nodes 不挤占 path budget。

### 14.3 属性查询

问题：

```text
0011号生产工单状态是什么
```

期望：

- task_type=`entity_attribute`
- `CORE_EVIDENCE` 是 target node 和 attribute table。
- 不进行大规模路径扩展。

### 14.4 概念解释

问题：

```text
什么是质量门控点
```

期望：

- task_type=`schema_explain`
- 主要证据来自 schema。
- 不发送大量实例。

## 15. 迁移策略

推荐采用双轨：

```text
graph_rag         当前线上兼容模式
graph_qa_evidence 当前新 contract 模式
```

如果不想新增 API mode，也可以让 `graph_rag` 内部切到新 contract，但保留配置开关：

```yaml
qa:
  evidence_contract: graph_qa_evidence_v1
  retrieval_algorithm: rules_ppr_path_v1
```

灰度步骤：

1. 本地测试通过。
2. 示例 job 回归通过。
3. 线上保留 `direct` 和 `filtered` 回滚。
4. 生成 SFT 小样本人工审查。
5. 开始增量 SFT。
6. 评估模型对 `graph_qa_evidence_v1` 的遵循能力。
7. 默认切换。

## 16. 风险与应对

### 风险 1：问题分类错误

应对：

- planner 输出 `task_type=general` 兜底。
- 对高风险关键词设高优先级，如“坏了/故障/会影响”优先 impact。
- `RETRIEVAL_TRACE` 记录分类原因。

### 风险 2：强 seed 误召回

应对：

- seed tier。
- 短数字降权。
- 完整实体短语优先。
- primary seed 数量限制。

### 风险 3：业务方向错误

应对：

- relation semantic map。
- impact_analysis 独立路径。
- direction_trap 负样本训练。

### 风险 4：prompt 仍被截断

应对：

- 分区预算。
- core evidence 永不截断。
- supporting 区域可截断。
- 若 core evidence 超预算，返回 `insufficient_evidence` 或 `core_evidence_too_large`，不要发送半截核心证据。

### 风险 5：SFT 与线上漂移

应对：

- 线上和训练共用 renderer。
- 样本 metadata 写入 `contract_version`。
- health report 统计 contract 分布。

## 17. 最终结论

本次子图裁剪不应继续以“调 PPR / 调节点数”的方式零散修改，而应先固定长期稳定的 `graph_qa_evidence_v1`。

后续工程边界：

```text
QAEvidence contract 是模型阅读协议
retrieval algorithm 是证据生成器
SFT 样本训练同一阅读协议
```

只要该边界稳定，后续可以不断使用新图谱、新问题、新算法生成 SFT 样本，让模型能力沿同一套输入输出契约持续累积。
