# Graph QA Evidence Contract v2 子图裁剪重构施工文档

> 本文档取代 `docs/graph-qa-evidence-contract-construction.md`（ChatGPT 初版方案）。
> 与初版的差异决策见附录 A。本文档是唯一执行依据，执行时不需要参考初版。
>
> 目标读者：负责实施的 AI coding agent。所有路径、数据模型、行语法、验收标准均为规范性要求。

## 0. 实施状态（2026-07-04）

P1–P5 + 金标准回归（§11.2）已实现并全部通过（`uv run pytest` 203 passed）。新增模块：
`qa_evidence.py`、`qa_linking.py`、`qa_planner.py`、`qa_semantics.py`、`qa_assembly.py`、
`relation_semantics_default.yaml`；改造 `qa_prompt.py`（新增 `render_graph_qa_evidence_prompt`）、
`qa_retrieval.py`（新增 `retrieve_supporting_candidates`）、`qa.py`（新增 `_answer_graph_qa_evidence`
路由 + `answer_status` 解析）、`config.py`（QAConfig 新字段）。测试：`tests/test_qa_contract.py`、
`test_qa_planning.py`、`test_qa_assembly.py`、`test_qa_evidence_flow.py`、`test_qa_goldens.py`
（+ `tests/fixtures/golden_industrial.json`）。

事故问题验证：`激光干涉仪1坏了会影响0011号生产工单吗` 现产出 2 个锚点（原 68 seeds）、
CORE 含完整 ImpactChain `inst:411 ← inst:542 ← inst:538`、prompt 9029 字符（原 23878 且被截断）、
conformance 0 违规。

**P6 已执行（配置翻转，2026-07-05）**：`configs/default.yaml` 的 `qa.evidence_contract`
已设为 `graph_qa_evidence_v1`，线上/API（`load_config()` 路径）默认走新合同；删除该行即回滚。
`QAConfig` 的 pydantic 默认仍保留 `graph_rag_v1`（代码级保守默认，供直接构造/旧单测使用），
两者刻意分离：部署默认由 yaml 控制，代码默认保守。

原先"须等重训模型再翻默认"的前提已因 Q2/Q3 的容错解析器解除——解析器兼容通用模型输出
（新格式 / 旧 `{think,highlight,answer}` / 字典边 / 纯文本，`answer_status` 缺省 answerable），
故通用模型即可先用新合同并直接修好本文档 §1 的兜底事故（prompt 修复与模型无关）。重训模型是
后续提质（`answer_status` 纪律、聚合措辞、严格引用），非启用前提。

## 1. 背景与根因（为什么要重构）

### 1.1 事故复现结论

对 `outputs/api_jobs/job_b58e2654d68b4ebe819d73f96246af94`，问题
`激光干涉仪1坏了会影响0011号生产工单吗` 进入了兜底策略。复现结果：

1. **检索层其实找到了答案路径**。图中存在 2 跳路径：
   `inst:538`(0011号生产工单) —包含→ `inst:542`(0011号生产工单-定位精度激光检测) —执行于→ `inst:411`(激光干涉仪1)。
   `retrieve_graph_rag_context` 的 `evidence_paths` 包含 `[inst:90, inst:538, inst:542, inst:411]`，
   两条关键边都在 `evidence_edges` 中。
2. **prompt 渲染把路径截掉了**。渲染顺序为 Schema → Seeds → Nodes → Edges → Paths，
   `_fit_budget()` 从尾部截断。80 个节点行（每行含 `score=… | reason=…`）耗尽 24000 字符预算，
   **24 条证据边 0 条进入 prompt，Paths 整段丢失**。模型收到"只有节点、没有关系"的上下文，
   回答"无法确定"，命中 `_is_unanswerable` 正则，进入兜底。
3. **种子爆炸**是预算被打满的原因。问题文本包含"生产工单"子串，导致所有 `生产工单` 类型节点
   命中 `type_match`(0.62)；且 `_instance_graph` 给每个实例注入 `对象类型` 属性，
   又全部命中 `attribute_value`(0.7)。`seed_count=68`，噪声工单节点分数(1.488)高于
   真锚点"激光干涉仪1"(1.327)。
4. **方向语义缺失**。`_seed_paths` 对"影响"类问题做有向 BFS 且只按种子顺序单向搜。
   `执行于` 的图边方向（工序→资源）与影响传播方向（资源→工序）相反，
   路径能找到纯属 `inst:90`(订单，编码恰好是 0011)作为额外种子的运气。

### 1.2 结构性问题：训练/线上保障不对称

SFT 数据由 `ontology_model/cot_generation` 生成，它 vendor 了本仓库的
`qa_retrieval.py`/`qa_prompt.py`，但在其上叠加了线上没有的保障层
（`_ensure_anchor_seeds` 锚点强制注入、`_expand_seed_propagation` 按 relation_semantics
传播扩展、`check_coverage` 覆盖率校验）。模型是在"锚点必在、路径必在"的分布上训练的，
线上却提供可能被截断的上下文——这就是"理论上能答、线上进兜底"的根源。

### 1.3 重构目标

```text
证据合同一次定稳（graph_qa_evidence_v1）
检索算法允许自由迭代（不触发 SFT 数据重做）
线上与 SFT 共用同一实体链接、证据装配、渲染代码
合同不变量由回归门禁保证，而不是靠约定
```

## 2. 总体架构：三层分离

```text
Question
  ├─ QueryPlanner        意图 + 模式判定（软调节，不做硬路由）
  ├─ EntityResolver      实体链接（最长匹配 + 遮蔽），产出锚点
  ├─ 召回层（可变）       PPR / 词法 / 未来的 embedding —— 只填充 supporting 区
  ├─ 保障层（合同不变量） 锚点必达、路径必达、聚合、结构上限 —— 产出 CORE_EVIDENCE
  ├─ 渲染层（冻结）       确定性渲染 graph_qa_evidence_v1 prompt
  └─ LLM → Parser（answer_status 结构化）→ 兜底仅处理解析失败
```

三层的变更政策：

| 层 | 内容 | 变更政策 |
|---|---|---|
| 渲染层（合同） | section 名称与顺序、行语法、排序规则、answer JSON 字段 | 冻结。变更即 bump `graph_qa_evidence_v2`，需重生成 SFT 数据 |
| 保障层（不变量） | I1–I6（见 §7）、结构上限的语义 | 冻结语义，上限数值可通过 QAConfig 调整 |
| 召回层 | seed 打分、PPR 参数、supporting 排序打分、未来 embedding | 自由迭代，仅需通过金标准回归（§11.3） |

### 2.1 模块布局

```text
ontology_construct/src/ontology_construct/
  qa.py                    改造：answer_question 按 evidence_contract 路由
  qa_planner.py            新增：意图识别 + 模式判定
  qa_linking.py            新增：EntityResolver 实体链接
  qa_semantics.py          新增：relation semantic map 加载与传播图构建
  qa_assembly.py           新增：保障层，装配 QAEvidence
  qa_evidence.py           新增：合同数据模型 + conformance validator
  qa_retrieval.py          改造：降级为召回层，只产出 supporting 候选
  qa_prompt.py             保留旧渲染（graph_rag_v1 回滚用），新增 render_graph_qa_evidence_prompt
  relation_semantics_default.yaml   新增：方向语义默认表（数据文件，随包分发）
```

**兼容性要求**：`context_mode` 取值不变（`filtered` / `direct` / `graph_rag`）；
`graph_rag` 内部由 `QAConfig.evidence_contract` 决定走新旧合同。
`direct`、`filtered` 两条链路及其测试不得改动。

## 3. Evidence Contract：graph_qa_evidence_v1

### 3.1 Prompt 固定结构

section 名称、顺序、必选性全部冻结：

```text
[QUESTION]            必选，永不截断
[QUERY_PLAN]          必选，永不截断
[CORE_EVIDENCE]       必选，永不截断（由结构上限保证体积，见 §7.5）
[SUPPORTING_SCHEMA]   可选，可截断
[SUPPORTING_EDGES]    可选，可截断（注意：边在节点之前）
[SUPPORTING_NODES]    可选，可截断
[ANSWER_REQUIREMENT]  必选，永不截断，内容固定
```

明确排除（与初版方案的差异）：
- **没有 `[ANSWERABLE_CHECK]` section**。其中事实性内容（链接成功/失败的实体）并入
  `[QUERY_PLAN]`；结论性字段（`core_evidence_found` 等）彻底删除——那是标签泄漏，
  模型会学捷径而不是读证据。"证据够不够"由模型在 `<think>` 中自行判断。
- **没有 `[RETRIEVAL_TRACE]` section**。检索过程信息放 API 响应元数据（§10.3），
  任何情况下不进 prompt（训练与线上分布必须一致，不允许 dev 开关改变 prompt 内容）。
- **prompt 中任何位置不得出现 score、reason、retrieval_policy 等打分器内部信息**。
  这些字段保留在 pydantic 模型中供调试，渲染器一律不打印。

### 3.2 QUERY_PLAN 语法

```text
[QUERY_PLAN]
contract_version=graph_qa_evidence_v1
mode=connectivity
intent=impact
linked_entities:
  - 激光干涉仪1 -> inst:411 | 制造资源 | 激光干涉仪1
  - 0011号生产工单 -> inst:538 | 生产工单 | 0011号生产工单
unlinked_mentions:
  - （无）
```

- `linked_entities` 每行：`- {问题中的原文 mention} -> {id} | {type} | {label}`，
  按 mention 在问题中出现的位置排序。歧义 mention（一个 mention 链接到多个节点）
  逐节点各占一行，行尾追加 `（歧义 k/n）`。
- `unlinked_mentions`：疑似实体但未链接成功的 mention（§5.4），没有则输出一行 `- （无）`。

### 3.3 CORE_EVIDENCE 行语法

核心证据由编号的证据项组成。**节点行统一语法**（全文档唯一节点表示法）：

```text
{id} | {type} | {label} | {k}={v}, {k}={v}
```

属性规则：最多 `max_attrs_per_node`(默认 6) 个；优先序 `实例编码, 对象编码, 状态, 时间, source_field`，
其余按原始插入序；值超 80 字符截为 77+`...`；空值跳过；没有属性时省略最后一段。

**边行统一语法**：`{source_id} -[{rel}]-> {target_id}`，方向永远是图中原始方向。

证据项类型与渲染格式：

```text
[CORE_EVIDENCE]
(1) TargetNode:
      inst:411 | 制造资源 | 激光干涉仪1 | source_field=资源
(2) TargetNode:
      inst:538 | 生产工单 | 0011号生产工单 | 实例编码=0011_3
(3) ImpactChain(cause=inst:411, affected=inst:538, hops=2):
      inst:411 | 制造资源 | 激光干涉仪1
      <-[执行于]- inst:542 | 加工工序 | 0011号生产工单-定位精度激光检测（GB/T
      <-[包含]- inst:538 | 生产工单 | 0011号生产工单
      note: 列表顺序为影响传播方向（inst:411 → inst:542 → inst:538）；
            箭头表示图中原始边方向，`<-[r]-` 表示下行节点有一条指向上行节点的 r 边。
(4) RelationSet(anchor=inst:538):
      inst:538 -[包含]-> inst:539 | 加工工序 | 0011号生产工单-基体几何精度检验（GB/T
      inst:538 -[包含]-> inst:541 | 加工工序 | 0011号生产工单-几何精度检验（GB/T
      inst:90 -[包含]-> inst:538 | 生产订单 | 0011号机床订单
(5) AggregateSummary(root=inst:411, view=impact, depth<=4):
      受影响 加工工序 x16（示例: inst:410, inst:490, inst:496, inst:502, inst:508）
      受影响 生产工单 x16（示例: inst:249, inst:439, inst:492, inst:498, inst:504）
(6) SameTypeCandidates(type=检测器具, total=52, top=10):
      inst:898 | 检测器具 | 激光干涉仪（型号XL-80）
      inst:1034 | 检测器具 | 激光干涉仪（Renishaw XL-80）
(7) AttributeTable(inst:538):
      实例编码=0011_3
      对象类型=生产工单
(8) SchemaDefinition(生产工单):
      说明: …（schema 对象的 description）
      相关关系: 生产工单 -[包含]-> 加工工序; 生产订单 -[生成]-> 生产工单
(9) CandidatePath(from=inst:X, to=inst:Y, path_type=directed):
      inst:X | … 
      -[流转到]-> inst:Z | …
      -[流转到]-> inst:Y | …
```

链式项（ImpactChain / TraceChain / CandidatePath）的每一跳：

- 原始边由上行节点指向下行节点：`-[rel]-> {下行节点行}`
- 原始边由下行节点指向上行节点：`<-[rel]- {下行节点行}`
- 列表顺序 = 业务推理方向（影响传播 / 追溯 / 路径行进方向）。

`TraceChain` 与 `ImpactChain` 语法相同，仅项名与 note 措辞不同（追溯方向）。
空核心证据时输出固定行：`（未找到核心证据项；参考 QUERY_PLAN 中未链接的实体与下方 supporting 区）`。

### 3.4 SUPPORTING 区语法

```text
[SUPPORTING_SCHEMA]
ObjectType:
  生产工单: …description…
RelationType:
  生产工单 -[包含]-> 加工工序

[SUPPORTING_EDGES]
  inst:538 -[包含]-> inst:543
  …

[SUPPORTING_NODES]
  inst:543 | 加工工序 | 0011号生产工单-整机包装入库-工序实例33 | 实例编码=0011_33
  …
```

截断时在该 section 末尾追加一行确定性标记：`  …（超出预算，已省略 {N} 行）`。
不允许截断半行。

### 3.5 ANSWER_REQUIREMENT（固定文本，逐字冻结）

```text
[ANSWER_REQUIREMENT]
只能基于上述证据回答，不得引用证据中未出现的节点或关系。
若证据不足以回答，请将 answer_status 设为 insufficient_evidence，并在 missing 中列出缺少的节点、关系或路径。
输出格式：<think>推理步骤，引用证据中的节点 id</think><answer>JSON</answer>
answer JSON 字段：answer_status("answerable"|"insufficient_evidence"), summary, path, edges, groups, missing。
```

### 3.6 确定性渲染规则（合同的一部分）

同一份 `QAEvidence` 必须渲染出字节相同的 prompt：

1. 核心证据项排序：TargetNode（按锚点在问题中的出现序）→ AttributeTable → ImpactChain/TraceChain/CandidatePath
   （按 (锚点对出现序, 跳数, 途径 id 串) 排序）→ RelationSet → AggregateSummary → SameTypeCandidates → SchemaDefinition。
2. supporting nodes 按 `(tier, id)` 排序、supporting edges 按 `(tier, source, target, rel)` 排序；
   tier 定义见 §8.2。召回打分只决定"谁入选"，不决定渲染顺序。
3. 不打印任何浮点数。
4. 属性顺序、截断规则按 §3.3 固定。
5. `render(evidence)` 必须是纯函数（无随机、无时间戳、无字典乱序——遍历一律用显式排序）。

### 3.7 输出合同（answer JSON v1）

沿用现有 `<think></think><answer></answer>` 外壳，answer JSON 在现有字段上**新增两个字段**：

```json
{
  "answer_status": "answerable",
  "summary": "会影响。0011号生产工单包含定位精度激光检测工序（inst:542），该工序执行于激光干涉仪1。",
  "path": ["inst:411", "inst:542", "inst:538"],
  "edges": [["inst:538", "inst:542", "包含"], ["inst:542", "inst:411", "执行于"]],
  "groups": [],
  "missing": []
}
```

```json
{
  "answer_status": "insufficient_evidence",
  "summary": "证据不足：图中没有 0011号生产工单 的负责人属性或人员关联关系。",
  "path": [], "edges": [], "groups": [],
  "missing": ["0011号生产工单 的负责人属性", "生产工单与人员之间的关系边"]
}
```

- `path`/`edges`/`groups`/`highlight` 语义与现有 `QAResponse` 一致（id 基准），**前端零改动**。
- 解析器：`QAResponse.answer` 增加 `status: Literal["answerable","insufficient_evidence"] = "answerable"`
  与 `missing: list[str] = []`；旧格式回复（无 answer_status）按 `answerable` 解析。

### 3.8 行为变更：insufficient_evidence 不再触发兜底

现状：模型说"无法确定"→ 正则 `_is_unanswerable` → `_fallback_answer` 生成启发式答案。
新合同下：

- `answer_status == "insufficient_evidence"` 是**一等合法答案**：直接返回给用户
  （summary + missing 列表 + 锚点高亮），不进兜底。
- `_fallback_answer` 只处理 `parse_failed` / `empty_response`。
- `_is_unanswerable` 正则仅在旧合同（graph_rag_v1）路径上保留。

## 4. QueryPlanner（qa_planner.py）

**核心原则：模式由锚点数量（客观事实）主导，意图关键词只做软调节，不做 8 类硬路由。**

### 4.1 模式判定

```python
QAMode = Literal["connectivity", "local", "schema", "general"]

def resolve_mode(anchors: list[LinkedEntity], intent: QAIntent, question: str) -> QAMode:
    if len(anchors) >= 2:
        return "connectivity"
    if len(anchors) == 1:
        return "local"
    if intent == "schema" or _has_schema_words(question):   # 什么是/定义/概念/类型
        return "schema"
    return "general"
```

### 4.2 意图词表

```python
QAIntent = Literal["impact", "trace", "path", "relation", "attribute", "replace", "schema", "general"]

_INTENT_TERMS: dict[str, tuple[str, ...]] = {
    "impact":  ("坏了", "故障", "异常", "失效", "停机", "报警", "会影响", "是否影响", "影响哪些", "影响到", "波及"),
    "trace":   ("来源", "上游", "前置", "根因", "谁导致", "追溯", "哪里来"),
    "path":    ("路径", "链路", "经过哪些", "怎么到", "如何到达", "流程", "步骤"),
    "replace": ("替代", "备用", "换成", "还有哪些同类", "可以用什么"),
    "attribute": ("状态", "属性", "编码", "数量", "时间", "是多少", "什么状态"),
    "relation": ("包含哪些", "用了哪些", "属于哪个", "关联哪些", "执行于", "处理了哪些", "有哪些"),
    "schema":  ("什么是", "定义", "概念", "解释一下"),
}
# 判定优先级：impact > trace > path > replace > relation > attribute > schema > general
```

### 4.3 意图对装配的软调节（唯一作用方式）

| intent | 传播图方向（§6） | 装配动作 |
|---|---|---|
| impact | impact 视图 | 锚点对 → ImpactChain；单锚点 → impact BFS + AggregateSummary |
| trace | impact 视图反向 | TraceChain / 聚合 |
| path | 原始方向优先，无向兜底 | CandidatePath（k≤3） |
| replace | — | SameTypeCandidates + 锚点 RelationSet |
| relation | — | RelationSet 加倍配额 |
| attribute | — | AttributeTable + 少量 RelationSet 消歧 |
| schema | — | SchemaDefinition + ≤3 个实例示例 |
| general | 无向 | 锚点邻域 + 预算内 supporting |

**分类错误的代价必须是"多带证据"而非"缺证据"**：connectivity 模式下无论 intent 是什么，
锚点对之间的路径搜索（I2）都要执行；intent 只决定优先渲染成哪种链式项。

## 5. EntityResolver（qa_linking.py）

输入：question、instance graph、schema graph。输出：`list[LinkedEntity]`（锚点）、
`list[str]`（unlinked mentions）、`list[SchemaHint]`（类型词提示）。

### 5.1 匹配候选与最长匹配遮蔽

1. 为每个实例节点收集匹配键：`label`、`实例编码`/`对象编码` 属性值、裸 id（`inst:411` 与 `411` 不算）。
   为每个 schema 节点收集：`label`（类型名）。
2. 在问题文本上做**全量子串匹配**（Aho-Corasick 或逐键 `str.find`，键数 ~千级性能无虞），
   得到 (span, node, 键类型) 候选集合。
3. **保留极大不重叠 span**：按 (长度降序, 起点升序) 贪心选取；被选中 span 覆盖的区间内，
   其余更短的候选一律丢弃（遮蔽规则——"0011号生产工单" 命中 inst:538 后，
   其内部的 "0011"、"生产工单" 不再产生任何实例种子或类型种子）。
4. span 落到 schema 类型名且未被更长实例 mention 遮蔽 → 产出 `SchemaHint`（供 schema gate 和
   SUPPORTING_SCHEMA 选择），**绝不展开为该类型的全部实例**。

### 5.2 编号匹配约束

- 纯数字键长度 < 4 不参与匹配（防 "1"、"01"、"11"）。
- 编码匹配要求边界：span 两侧字符不能是 `[0-9A-Za-z_]`（"0011" 不得命中 "0011_32" 的内部）。
- label 长度 < 2 不参与匹配。

### 5.3 歧义与上限

- 一个 span 命中多个节点（同名）：全部保留为锚点，最多 3 个，标注歧义序号。
- 锚点总数上限 `max_anchors`(默认 6)；超限时按 (键类型优先级 label==span 全等 > 编码全等 > 其他,
  span 长度降序) 保留。
- **锚点是合同保障对象，不参与召回层打分竞争**。

### 5.4 unlinked mention 检测（保守启发式）

对问题做正则扫描：`[0-9A-Za-z\-]{2,}号?[一-鿿]{0,8}` 与引号包裹片段；
凡与已链接 span 不重叠、且包含数字或字母的候选，列入 `unlinked_mentions`。
宁缺毋滥：检测不到就输出 `（无）`，不允许把普通词误报进去。

## 6. Relation Semantic Map（qa_semantics.py）

**方向语义是数据不是代码。** 单一数据源在本仓库，cot_generation 通过 vendor 同步消费同一份文件。

### 6.1 数据文件 `relation_semantics_default.yaml`

```yaml
# traversal 语义：impact 视图下，沿一条 rel 边允许的影响传播穿行方向
#   forward  = 沿边方向传播（source 异常波及 target）
#   reverse  = 逆边方向传播（target 异常波及 source）
#   both     = 双向
#   exclude  = 不参与传播（但仍可出现在 RelationSet / supporting 中）
version: 1
default_traversal: exclude          # 未列出的关系默认不传播（保守）
rules:
  - pattern: "^流转到"    ; traversal: forward   # 上游批次异常 → 下游工位
  - pattern: "^触发"      ; traversal: forward
  - pattern: "^生成"      ; traversal: forward
  - pattern: "^产出"      ; traversal: forward
  - pattern: "^前置于"    ; traversal: forward
  - pattern: "^包含"      ; traversal: both      # 子异常→父受影响；父取消→子取消（含 包含工序）
  - pattern: "^属于"      ; traversal: both
  - pattern: "^处理"      ; traversal: both      # 工序异常→产物；来料异常→工序
  - pattern: "^执行于"    ; traversal: reverse   # 资源异常 → 使用它的工序
  - pattern: "^使用"      ; traversal: reverse
  - pattern: "^消耗"      ; traversal: reverse
  - pattern: "^要求"      ; traversal: reverse
  - pattern: "^来源于"    ; traversal: reverse
  - pattern: "^语义关联"  ; traversal: exclude
  - pattern: "^关联"      ; traversal: exclude   # 事件挂靠边，语义太弱，不做传播载体
  - pattern: "^同义"      ; traversal: exclude
  - pattern: "^同批"      ; traversal: exclude
```

实现注意：YAML 中用标准 map 列表（`- pattern: "..."` + `traversal: ...` 两键），
上面分号写法仅为文档紧凑。

### 6.2 加载优先级

```text
1. formatted_ontology 的 link 若带 impactDirection 字段（未来由本体构建阶段产出）→ 优先
2. job 目录下 relation_semantics.yaml（领域覆写，可选）
3. 包内 relation_semantics_default.yaml（兜底）
```

### 6.3 传播图 API

```python
def build_impact_digraph(edges: list[GraphEdge], policy: SemanticPolicy) -> dict[str, list[tuple[str, str]]]:
    """返回邻接表：node_id -> [(next_id, rel)]。
    forward 边加 source→target；reverse 边加 target→source；both 两条都加；exclude 跳过。
    trace 视图 = impact 视图整体反向。"""
```

## 7. 保障层（qa_assembly.py）—— 合同不变量

输入：anchors、plan、instance/schema graph、semantic policy、召回层的 supporting 候选。
输出：`QAEvidence`。以下不变量 **I1–I6 是合同的一部分**：

- **I1 锚点必达**：每个锚点生成一个 `TargetNode` 项（attribute intent 时升级为 `AttributeTable`）。
- **I2 路径必达**：connectivity 模式下，对每个锚点对（按问题出现序两两组合，最多
  `max_anchor_pairs`(默认 6) 对）：
  1. 在 intent 对应的语义传播图上做 BFS，`path_max_depth`(默认 6)，每对最多
     `max_paths_per_pair`(默认 3) 条（用逐层 BFS 收集等长最短路，超出取 id 字典序小者）；
  2. 语义图上无路 → 原始有向图重试 → 仍无路 → 无向图重试，链式项标注 `path_type`；
  3. 找到的路径完整进入 CORE（所有途径节点+边），**任何预算压力下不得丢弃第一条路径**；
  4. 全部无路 → 不造项；QUERY_PLAN 已含两锚点，模型自行推断"图上不连通"。
- **I3 邻域必达**：每个锚点的一跳边生成 `RelationSet`，每锚点上限
  `max_relation_lines_per_anchor`(默认 12，relation intent 时 24)；同 rel 同邻居类型的边超限时
  合并为聚合行：`inst:411 <-[执行于]- 加工工序 x16（示例: inst:410, inst:490, inst:496, inst:502, inst:508）`。
- **I4 扇出聚合**：impact/trace intent 且只有一个锚点（"影响哪些"）：在传播图上 BFS
  `impact_bfs_depth`(默认 4)，命中节点按类型分组生成 `AggregateSummary`
  （每组：计数 + 前 `aggregate_example_count`(默认 5) 个 id 示例），并对每组渲染
  `max_full_chains`(默认 5) 条完整链。
- **I5 结构上限先于字符预算**：CORE 的体积由结构上限控制——链 ≤`max_chains`(默认 8) 条、
  每链 ≤`path_max_depth`+2 个节点、RelationSet 行数按 I3、聚合组 ≤10。
  按默认值最坏情况 CORE ≈ 6–7k 字符 < `budget_core_chars`(9000)，因此 **CORE 永不发生字符截断**；
  实现中加断言：渲染后 CORE 超 `budget_core_chars` 视为 bug（抛异常，由测试兜住，线上 log error）。
- **I6 引用闭包**：CORE 与 supporting 中出现的每条边，其两个端点必须在 CORE 或
  SUPPORTING_NODES 中有节点行。装配完成后做闭包补齐：缺失端点以 tier=0 追加进 supporting nodes
  （不受预算裁剪影响）。

### 7.1 QAEvidence 数据模型（qa_evidence.py）

```python
from typing import Any, Literal
from pydantic import BaseModel, Field

QAMode = Literal["connectivity", "local", "schema", "general"]
QAIntent = Literal["impact", "trace", "path", "relation", "attribute", "replace", "schema", "general"]
EvidenceItemType = Literal[
    "target_node", "attribute_table", "impact_chain", "trace_chain",
    "candidate_path", "relation_set", "aggregate_summary",
    "same_type_candidates", "schema_definition",
]

class EvidenceNode(BaseModel):
    id: str
    label: str
    type: str
    attrs: dict[str, Any] = Field(default_factory=dict)
    tier: int = 3                      # 0=闭包补齐 1=path成员 2=锚点一跳 3=召回
    score: float = 0.0                 # 仅调试，渲染器不得使用
    reasons: list[str] = Field(default_factory=list)   # 仅调试

class EvidenceEdge(BaseModel):
    source: str
    target: str
    rel: str
    tier: int = 3
    score: float = 0.0
    reasons: list[str] = Field(default_factory=list)

class ChainHop(BaseModel):
    node: EvidenceNode
    rel: str | None = None             # 与上一跳之间的关系；首跳为 None
    raw_direction: Literal["forward", "reverse"] | None = None  # forward=上行节点→本节点

class CoreEvidenceItem(BaseModel):
    item_type: EvidenceItemType
    title: str                         # 渲染在项头括号里的参数串
    nodes: list[EvidenceNode] = Field(default_factory=list)
    edges: list[EvidenceEdge] = Field(default_factory=list)
    hops: list[ChainHop] = Field(default_factory=list)      # 链式项使用
    facts: list[str] = Field(default_factory=list)          # 聚合行/属性行/schema 行
    note: str = ""

class LinkedEntity(BaseModel):
    mention: str
    node: EvidenceNode
    ambiguous_rank: tuple[int, int] | None = None   # (k, n) 或 None

class QueryPlan(BaseModel):
    contract_version: str = "graph_qa_evidence_v1"
    mode: QAMode
    intent: QAIntent
    linked_entities: list[LinkedEntity] = Field(default_factory=list)
    unlinked_mentions: list[str] = Field(default_factory=list)

class QAEvidence(BaseModel):
    contract_version: str = "graph_qa_evidence_v1"
    plan: QueryPlan
    core: list[CoreEvidenceItem] = Field(default_factory=list)
    supporting_schema_nodes: list[EvidenceNode] = Field(default_factory=list)
    supporting_schema_edges: list[EvidenceEdge] = Field(default_factory=list)
    supporting_nodes: list[EvidenceNode] = Field(default_factory=list)
    supporting_edges: list[EvidenceEdge] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)      # trace、预算统计，只进 API 不进 prompt
```

## 8. 召回层改造（qa_retrieval.py）

召回层唯一职责：为 supporting 区提供候选并排序入选。改造点：

1. **锚点相关逻辑移出**：种子中的 exact_label/exact_code 匹配逻辑由 EntityResolver 接管；
   `retrieve_graph_rag_context` 改造为 `retrieve_supporting_candidates(anchors, plan, graphs, cfg)`，
   以锚点为 PPR 重启分布。
2. **降噪三条硬规则**：
   - `对象类型` 属性不参与任何文本匹配打分（与 node.type 重复计分是种子爆炸主因）；
   - 已被锚点 span 遮蔽的文本不再产生 type_match / attribute_value 分；
   - 类型词命中只做 schema gate，不给该类型全部实例加分。
3. tier 赋值（§8.2）后按 (tier, 分数) 选入 supporting，入选后排序交给渲染层的确定性规则。
4. 旧 `retrieve_graph_rag_context` 函数保留（graph_rag_v1 回滚路径调用），不再演进。

### 8.2 supporting tier 定义

```text
tier 0：I6 闭包补齐节点（不可被预算裁掉）
tier 1：核心链路成员的额外邻接边/节点（路径上下文）
tier 2：锚点一跳邻居中未进 CORE 的部分
tier 3：PPR/词法召回的其余候选
```

## 9. 预算与渲染（qa_prompt.py::render_graph_qa_evidence_prompt）

### 9.1 分区预算（QAConfig 新字段，默认总预算 24000 不变）

```python
budget_plan_chars: int = 1500          # QUESTION + QUERY_PLAN
budget_core_chars: int = 9000          # 结构上限保证达不到，见 I5
budget_schema_chars: int = 3000
budget_edges_chars: int = 4500
budget_nodes_chars: int = 4500
# ANSWER_REQUIREMENT 固定 ~300 字符，其余为安全余量
```

### 9.2 裁剪阶梯（确定性）

```text
1. 各 supporting 区独立按行装填至各自预算，超出部分整行丢弃并计数（tier 升序保留，tier 内按排序键）
2. 若总长仍超 max_prompt_chars：按 SUPPORTING_NODES → SUPPORTING_EDGES → SUPPORTING_SCHEMA
   逆序继续整行削减（tier 0 行除外）
3. QUESTION / QUERY_PLAN / CORE_EVIDENCE / ANSWER_REQUIREMENT 永不参与削减
4. 每个被削减的 section 末尾追加 `  …（超出预算，已省略 {N} 行）`
```

### 9.3 Conformance validator（qa_evidence.py）

```python
def validate_contract(evidence: QAEvidence, prompt: str, cfg: QAConfig) -> list[str]:
    """返回违规列表，空列表为合规。检查：
    1. section 顺序与名称与 §3.1 完全一致
    2. 每个锚点 id 出现在 CORE_EVIDENCE 文本中
    3. 所有边端点满足 I6 闭包
    4. 必选 section 中不含截断标记
    5. len(prompt) <= max_prompt_chars
    6. 二次渲染 byte 相同（determinism）
    7. prompt 中不含 "score=" / "reason=" 字样
    """
```

调用位置：全部单测；线上 `answer_question` 中以 log warning 方式运行（不阻断请求）。

## 10. qa.py 接入与配置

### 10.1 QAConfig 新增字段

```python
evidence_contract: Literal["graph_rag_v1", "graph_qa_evidence_v1"] = "graph_qa_evidence_v1"
max_anchors: int = 6
max_anchor_pairs: int = 6
max_paths_per_pair: int = 3
path_max_depth: int = 6
impact_bfs_depth: int = 4
max_chains: int = 8
max_full_chains: int = 5
max_relation_lines_per_anchor: int = 12
aggregate_example_count: int = 5
max_attrs_per_node: int = 6
max_same_type_candidates: int = 10
supporting_node_limit: int = 60
supporting_edge_limit: int = 80
budget_plan_chars: int = 1500
budget_core_chars: int = 9000
budget_schema_chars: int = 3000
budget_edges_chars: int = 4500
budget_nodes_chars: int = 4500
```

（保留现有全部字段；`include_retrieval_trace` 语义改为"是否在 API meta 中返回 trace"，
不再影响 prompt。）

### 10.2 answer_question 路由

```python
elif context_mode == "graph_rag":
    if qa_settings.evidence_contract == "graph_qa_evidence_v1":
        plan    = plan_question(question, anchors, ...)          # qa_planner + qa_linking
        support = retrieve_supporting_candidates(...)            # qa_retrieval（召回层）
        evidence = assemble_evidence(plan, support, graphs, policy, qa_settings)  # qa_assembly
        prompt  = render_graph_qa_evidence_prompt(evidence, qa_settings)
    else:
        …（现有 graph_rag_v1 路径原样保留）
```

### 10.3 API 响应元数据

`QAResponse` 增加 `meta: dict[str, Any]`（默认空）：contract_version、mode、intent、
linked_entities、unlinked_mentions、core item 统计、supporting 削减行数、conformance 告警、
召回 trace。前端可忽略；SFT 生成与调试依赖它。

## 11. 测试与验收

### 11.1 单元测试（tests/test_qa_contract.py 等，新文件）

```text
test_linker_longest_match_shadowing        # "0011号生产工单" 链接 538 后，"生产工单"/"0011" 不产生种子
test_linker_short_numeric_rejected         # "1"/"001" 不成为锚点
test_linker_code_boundary                  # "0011" 不命中 "0011_32" 内部
test_planner_mode_by_anchor_cardinality    # 2 锚点→connectivity；1→local；0+概念词→schema
test_planner_intent_impact_priority        # "坏了…会影响" → impact
test_semantics_impact_digraph_direction    # 执行于 reverse、包含 both、语义关联 exclude
test_assembly_I2_path_on_impact_digraph    # 411/538 锚点对产出 2 跳 ImpactChain
test_assembly_I3_relation_rollup           # 超过 12 行的一跳边聚合为计数行
test_assembly_I4_fanout_aggregate          # 单锚点 impact → AggregateSummary 按类型分组
test_assembly_I6_edge_endpoint_closure
test_render_deterministic                  # 两次渲染 byte 相同
test_render_no_score_in_prompt
test_render_core_never_truncated           # 构造超预算场景，CORE 完整、仅 supporting 截断
test_render_section_order_frozen
test_render_edges_before_nodes
test_parser_answer_status                  # 新旧两种 answer JSON 都能解析
test_insufficient_evidence_not_fallback    # answer_status=insufficient 不触发 _fallback_answer
test_legacy_graph_rag_v1_still_works       # evidence_contract 切回旧值，旧行为不变
```

现有 `tests/test_qa.py` 中 direct/filtered/fallback 相关测试必须全部保持通过。

### 11.2 金标准回归（tests/test_qa_goldens.py + tests/fixtures/golden_industrial.json）

fixture：从 job_b58e2654… 抽取以 inst:90/410/411/490/496/502/508/538–543/91/92 为中心的
最小子图（含全部相关 schema objects/links），加 20 个 0001–0016 号工单作干扰节点，
存为静态 JSON（formatted_ontology 结构）。金标准用例：

| 问题 | 断言 |
|---|---|
| 激光干涉仪1坏了会影响0011号生产工单吗 | 锚点=inst:411,inst:538；CORE 含 ImpactChain 途径 542；两条边 538→542、542→411 在 prompt 中 |
| 激光干涉仪1坏了会影响哪些生产工单 | 单锚点；CORE 含 AggregateSummary（受影响生产工单计数≥2）+ 完整示例链 |
| 0011号生产工单包含哪些工序 | RelationSet 含 539/541/542/91/543 五条 包含 边 |
| 从0011号机床订单到激光干涉仪1的链路是什么 | CandidatePath 90→538→542→411 |
| 99号喷漆室坏了会影响什么 | 无锚点命中"99号喷漆室"；unlinked_mentions 含之；CORE 无捏造链 |

每条断言基于 `QAEvidence` 与渲染后 prompt 文本，不调用 LLM。
另加一个 `@pytest.mark.slow` 集成测试：若 `outputs/api_jobs/job_b58e2654…` 存在，
对真实全图跑第一条用例并断言相同结论（目录不存在则 skip）。

### 11.3 门禁定位

金标准回归 = 召回层的变更门禁。后续任何 qa_retrieval 调参，跑
`uv run pytest tests/test_qa_goldens.py tests/test_qa_contract.py` 通过即可上线，
**不需要重新生成 SFT 数据**。

### 11.4 验证命令

```bash
cd ontology_construct
uv run pytest tests/test_qa_contract.py tests/test_qa_goldens.py tests/test_qa.py tests/test_config.py
uv run pytest   # 全量
```

## 12. 实施阶段（按序执行，每阶段结束运行验收）

- **P1 合同骨架**：qa_evidence.py（数据模型 + validator）、qa_prompt.py 新渲染函数。
  验收：mock QAEvidence 渲染出 §3 全部 section；determinism / no-score / core-never-truncated 测试通过。
- **P2 实体链接与规划**：qa_linking.py、qa_planner.py。
  验收：linker/planner 单测通过；事故问题只产出 411/538 两个锚点。
- **P3 语义图与装配**：relation_semantics_default.yaml、qa_semantics.py、qa_assembly.py。
  验收：I1–I6 单测通过；金标准前四条对 fixture 通过。
- **P4 召回层改造**：qa_retrieval.py 降级为 supporting 填充 + 三条降噪规则。
  验收：金标准全部通过；seed 爆炸回归测试（68 seeds 场景下 supporting 不含 score 泄漏、CORE 不受影响）。
- **P5 接入与解析**：qa.py 路由、QAConfig 字段、parser answer_status、insufficient 不兜底。
  验收：`test_qa.py` 全量 + 新增路由测试；API 形态不变。
- **P6 灰度与默认切换**：evidence_contract 默认值改为新合同；对真实 job 手工跑 §11.2 五个问题，
  人工检查 prompt 与回答；确认后提交。

## 13. 禁止事项（对 AI coding 的硬约束）

1. 不得修改 §3.1 的 section 名称/顺序，不得在 prompt 中输出 score/reason/trace/浮点数。
2. 不得让 CORE_EVIDENCE / QUERY_PLAN / ANSWER_REQUIREMENT 参与任何字符截断。
3. 不得删除或改动 `direct`、`filtered`、graph_rag_v1 旧路径及其测试。
4. 不得在渲染器中引入随机性、时间戳或依赖 dict 无序遍历。
5. 不得把 relation 方向语义硬编码进 Python 逻辑——必须走 qa_semantics 数据加载。
6. 不得改动前端已消费的 QAResponse 既有字段语义（highlight/path/edges/groups）。
7. 遇到本文档未覆盖的设计空白：选择"多带证据、不丢证据"的一侧，并在 PR 描述中列出该决策。

## 附录 A：与初版方案（ChatGPT 版）的差异决策记录

| 初版 | 本版 | 原因 |
|---|---|---|
| 8 类 task_type 硬路由，各类独立检索策略 | 统一装配 + 锚点数量定模式 + intent 软调节 | 分类器是最弱组件，硬路由使分类错误变成证据错误；软调节下错误只导致多带证据 |
| [ANSWERABLE_CHECK] 含 core_evidence_found 等结论字段 | 删除；事实性内容并入 QUERY_PLAN | 结论字段=标签泄漏，模型学捷径且线上判错会连带拒答 |
| [RETRIEVAL_TRACE] 在 prompt 内 | 移至 API meta | 打分器内部信息进 prompt 会把训练数据耦合到检索实现 |
| 节点行含 score/reason | 禁止 | 调一个权重→全部样本输入字符串改变 |
| RELATION_SEMANTICS 硬编码 dict | 数据文件 + 三级加载（本体产出 > job 覆写 > 默认） | 与 cot_generation 已有 yaml 双源必漂移；数据化才能跨领域 |
| core 超预算 → 拒答 | 结构上限 + 聚合项，使超预算不可能发生 | hub 扇出问题应聚合作答而非拒答 |
| supporting Nodes 在 Edges 前 | Edges 在 Nodes 前 | 本次事故即节点属性文本挤掉关系边；边的单字符信息密度更高 |
| 无回归门禁 | 金标准 coverage 门禁 + conformance validator | "改召回不重训"必须由机制保证而非约定 |
| 保留旧格式兼容样本混训 | 不混训（见 SFT 迁移文档） | 旧分布就是事故分布；回滚靠旧 checkpoint 而非双格式模型 |
