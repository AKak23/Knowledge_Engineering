# 概念优先本体构建技术文档

> **Ontology Construct — Concept-First (Schema-First) 本体构建方法论与技术实现**

---

## 目录

1. [设计理念与问题背景](#1-设计理念与问题背景)
2. [总体架构](#2-总体架构)
3. [构建模式对比](#3-构建模式对比)
4. [概念优先流水线详解](#4-概念优先流水线详解)
   - [4.1 阶段一：文本清洗与分块](#41-阶段一文本清洗与分块)
   - [4.2 阶段二：Chunk 候选概念抽取](#42-阶段二chunk-候选概念抽取)
   - [4.3 阶段三：全局概念归纳与聚类](#43-阶段三全局概念归纳与聚类)
   - [4.4 阶段四：概念评分与筛选](#44-阶段四概念评分与筛选)
   - [4.5 阶段五：概念关系构建](#45-阶段五概念关系构建)
   - [4.6 阶段六：实例层抽取](#46-阶段六实例层抽取)
   - [4.7 阶段七：输出格式化](#47-阶段七输出格式化)
5. [核心数据模型](#5-核心数据模型)
6. [聚类与去重算法](#6-聚类与去重算法)
7. [配置体系](#7-配置体系)
8. [质量评估报告](#8-质量评估报告)
9. [结构化数据本体构建](#9-结构化数据本体构建)
10. [API 接口说明](#10-api-接口说明)

---

## 1. 设计理念与问题背景

### 1.1 为什么需要「概念优先」

传统的信息抽取（IE）方法往往是**实体优先**（Entity-First）的：先抽取人名、地名、组织名等命名实体，再尝试建立实体间的关系。这种方法在处理长文本构建本体时有几个关键问题：

| 问题 | 表现 |
|------|------|
| **碎片化** | 抽取到的都是具体的张三、李四、3号生产线，难以形成可复用的知识结构 |
| **信息过载** | 一本 200 页的工业手册可能抽取上千个实体，用户难以识别哪些是重要的 |
| **缺乏抽象** | 缺乏对「生产线」这个类型本身的定义、属性、与其他类型的关系 |
| **实例冗余** | 具体实例在不同 chunk 中重复出现，归并困难 |

**概念优先**的核心理念是：**先确定有哪些概念类型（Class/Schema），再决定这些类型下有哪些具体实例**。这类似于面向对象编程中「先设计类，再实例化对象」。

### 1.2 设计原则

1. **从粗到细**：先抽取概括性概念候选 → 全局归纳与聚类 → 筛选 → 关系构建 → 实例抽取
2. **信息压缩**：把长文本中分散提到的同义表述压缩为统一的概念名称
3. **LLM 兜底 + 确定性增强**：LLM 做全局归纳，确定性算法做聚类、评分、去重；LLM 失败时自动降级
4. **可配置上限**：通过 `max_concepts` 硬限制最终概念数量，避免过拟合
5. **证据驱动**：每个概念和关系都必须携带文本证据，支持溯源

---

## 2. 总体架构

### 2.1 模块组织

```
ontology_construct/
├── src/ontology_construct/
│   ├── concept/              # ★ 概念优先核心模块
│   │   ├── extractor.py      #    Chunk 候选概念抽取器
│   │   ├── generalizer.py    #    概念归纳、聚类、筛选、关系构建
│   │   ├── models.py         #    概念候选、聚类、质量报告等数据模型
│   │   ├── parser.py         #    LLM JSON 响应解析器
│   │   └── prompts.py        #    提示词模板渲染
│   ├── input/                # 多格式文本读取（TXT/PDF/DOCX/LOG/JSON）
│   ├── preprocessor.py       # 文本清洗与分块
│   ├── models/               # 全局本体数据模型
│   │   └── ontology.py       #    Entity / Triple / OntologyInstance
│   ├── llm/                  # LLM 客户端（DeepSeek / Qwen / 九天等）
│   ├── embedding/            # Embedding 客户端（Qwen text-embedding-v4）
│   ├── merger/               # 实体归并与去重
│   ├── config.py             # YAML 配置加载与验证
│   ├── api.py                # FastAPI 服务
│   ├── cli.py                # CLI 命令行入口
│   ├── output/               # 格式化输出（formatted_ontology.json）
│   └── structured_builder.py # 结构化数据（PostgreSQL）本体构建
├── prompts/                  # LLM 提示词模板
│   ├── extract_concept_candidates.txt    # 候选概念抽取
│   ├── generalize_concepts.txt           # 全局概念归纳
│   ├── build_concept_relations.txt       # 概念关系构建
│   ├── extract_concept_instances.txt     # 概念实例抽取（per_concept 模式）
│   ├── extract_chunk_instances.txt       # Chunk 实例抽取（per_chunk 模式）
│   └── build_instance_relations.txt      # 实例关系构建
├── configs/
│   └── default.yaml          # 默认配置
└── tests/                    # 单元测试
```

### 2.2 两条构建流水线

```
                    ┌─────────────────────────┐
                    │   文本输入（多格式）      │
                    └───────────┬─────────────┘
                                │
                    ┌───────────▼─────────────┐
                    │   文本清洗与分块          │
                    └───────────┬─────────────┘
                                │
              ┌─────────────────┼─────────────────┐
              │                                   │
    ┌─────────▼─────────┐              ┌──────────▼──────────┐
    │  Standard 模式     │              │  Schema-First 模式   │
    │ （实体优先/Traditional）│          │ （概念优先，⭐推荐）  │
    └─────────┬─────────┘              └──────────┬──────────┘
              │                                   │
    ┌─────────▼─────────┐              ┌──────────▼──────────┐
    │ Chunk → 局部本体   │              │ Chunk → 候选概念     │
    │ (Entity+Triple+    │              │ (ConceptCandidate+  │
    │  Instance 全量抽取) │              │  RelationCandidate) │
    └─────────┬─────────┘              └──────────┬──────────┘
              │                                   │
    ┌─────────▼─────────┐              ┌──────────▼──────────┐
    │ 全局归并 + 去重    │              │ 全局概念归纳（LLM）   │
    │ (OntologyMerger +  │              │ + 确定性聚类          │
    │  EntityDeduplicator)│             │ + 评分筛选            │
    └─────────┬─────────┘              └──────────┬──────────┘
              │                                   │
              │                       ┌──────────▼──────────┐
              │                       │ 概念关系构建（LLM）   │
              │                       │ + 候选关系兜底        │
              │                       └──────────┬──────────┘
              │                                   │
              │                       ┌──────────▼──────────┐
              │                       │ 实例层抽取（LLM）     │
              │                       │ + 实例关系构建        │
              │                       └──────────┬──────────┘
              │                                   │
              └─────────────────┬─────────────────┘
                                │
                    ┌───────────▼─────────────┐
                    │   格式化输出              │
                    │   (formatted_ontology.json│
                    │    schema.json / graph.json)│
                    └─────────────────────────┘
```

---

## 3. 构建模式对比

| 维度 | Standard（实体优先） | Schema-First（概念优先） |
|------|---------------------|--------------------------|
| **抽取策略** | 每个 chunk 抽完整本体（实体+关系+实例） | 每个 chunk 只抽概念候选，再全局归纳 |
| **LLM 调用次数** | N 次（每 chunk 1 次） | N + 1~3 次（候选抽取 + 归纳 + 关系 + 实例）|
| **概念质量** | 取决于归并策略，易碎片化 | LLM 全局视角归纳，概念更稳定 |
| **关系完整性** | 局部关系需全局去重 | 全局关系构建 + 候选关系兜底 |
| **实例丰富度** | 每次抽取都附带实例 | 概念确定后按需抽取实例 |
| **适用场景** | 短文本、少量 chunk | 长文本、工业手册、知识图谱 |
| **容错性** | 单 chunk 失败不影响其他 | LLM 归纳失败自动降级到确定性 pipeline |

---

## 4. 概念优先流水线详解

### 4.1 阶段一：文本清洗与分块

#### 4.1.1 多格式支持

```python
# ontology_construct/input/text_reader.py
SUPPORTED_TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".pdf", ".docx", ".log", ".json", ".jsonl"}
```

- **TXT/MD**：多编码探测（UTF-8 / GB18030），BOM 识别
- **PDF**：基于 `pypdf` 提取文本，按页分页标记
- **DOCX**：直接解析 `word/document.xml`
- **LOG**：日志智能压缩——提取事件模板、日志级别、关键字段值分布
- **JSON/JSONL**：结构化 JSON 展开为文本

#### 4.1.2 分块策略

```yaml
# configs/default.yaml
chunk:
  size: 1500     # 每 chunk 最大 token 数
  overlap: 200   # chunk 间重叠 token 数
```

分块器（`TextChunker`）采用**段落优先**策略：尽可能在段落边界切分，避免在句子中间断开。重叠用于保证跨 chunk 的关键信息不被切断。

### 4.2 阶段二：Chunk 候选概念抽取

这是概念优先模式的第一步 LLM 调用，对每个 chunk 独立并发执行。

#### 4.2.1 抽取器

```python
# ontology_construct/concept/extractor.py
class ConceptCandidateExtractor:
    async def extract(self, chunk: Chunk) -> ChunkConceptCandidates:
        prompt = render_concept_candidate_prompt(chunk, self.config, self.supplemental_prompt)
        raw_response = await self.llm_client.complete(prompt)
        return parse_concept_candidates(raw_response, chunk.id, ...)
```

#### 4.2.2 LLM 提示词设计

提示词 `extract_concept_candidates.txt` 引导 LLM：

1. **只输出概括性概念**，不要输出具体实体
2. **每个 chunk 最多 N 个概念**（`max_candidates_per_chunk`，默认 10）
3. **关系谓词受控**：只能从预定义词表选择
4. **输出结构化 JSON**：包含 `concepts` 和 `relations` 两个数组
5. **引导抽象思维**：
   - ✅ 优先：抽象概念、对象类型、业务环节、状态类型、风险类型、资源类型
   - ❌ 避免：具体编号、姓名、单次事件、数值、日期、地点门牌

#### 4.2.3 输出模型

```python
# ontology_construct/concept/models.py
class ConceptCandidate(BaseModel):
    name: str                        # 概念名称
    definition: str = ""             # 一句话定义
    abstraction_level: str = "medium" # high / medium / low
    aliases: list[str] = []          # 同义词
    evidence: list[str] = []         # 文本证据句
    should_be_class: bool = True     # 是否应该作为类

class RelationCandidate(BaseModel):
    source: str       # 源概念名
    predicate: str    # 关系谓词（受控词表）
    target: str       # 目标概念名
    evidence: list[str] = []
    confidence: float = 1.0

class ChunkConceptCandidates(BaseModel):
    chunk_id: str
    concepts: list[ConceptCandidate]
    relations: list[RelationCandidate]
    instances: list[ConceptInstanceCandidate]  # 可选：per_chunk 模式下同时抽取实例
```

#### 4.2.4 并发控制

```python
# 使用 asyncio.Semaphore 控制并发
semaphore = asyncio.Semaphore(config.llm.concurrency)  # 默认 4
```

支持进度追踪（SQLite）和失败重试，已完成的 chunk 在断点续跑时自动跳过。

### 4.3 阶段三：全局概念归纳与聚类

#### 4.3.1 LLM 全局归纳（主路径）

所有 chunk 的候选概念汇总后，发送给 LLM 做全局归纳：

```python
# ontology_construct/concept/generalizer.py
class ConceptGeneralizer:
    async def generalize_with_artifacts(self, chunks):
        # 1. 构建候选概念 payload
        payload = self._candidate_payload(chunks)
        # 2. 调用 LLM 全局归纳
        prompt = render_concept_generalize_prompt(payload, self.config, ...)
        raw_response = await self.llm_client.complete(prompt)
        parsed = parse_concept_candidates(raw_response, "concept_global", ...)
        # 3. 从归纳结果构建最终本体
        return await self._artifacts_from_candidates(parsed)
```

提示词 `generalize_concepts.txt` 的核心指令：

- **合并**同义词、近义词、过细概念和局部表述
- **降格**具体实体、单次事件、编号、日期为证据
- **上限约束**：最终概念数不超过 `max_concepts`
- **保留原则**：证据少的概念仅当是重要上位概念或连接多个关系时才保留

#### 4.3.2 确定性聚类（兜底/增强路径）

当 LLM 归纳失败或未产生有效结果时，自动降级到确定性聚类 pipeline：

```python
async def _deterministic_artifacts(self, chunks, ...):
    # Step 1: 名称匹配聚类
    accumulators, name_to_key = self._cluster_candidates(chunks)
    # Step 2: Embedding 语义聚类（可选）
    accumulators, name_to_key, merge_count = await self._merge_semantic_accumulators(accumulators)
    # Step 3: 构建 Cluster 模型
    clusters_by_key = self._build_cluster_models(accumulators, relation_degree)
    # Step 4: 评分筛选
    selected_keys, rejected = self._select_clusters(clusters_by_key)
    # Step 5: 构建本体
    ontology = await self._ontology_from_cluster_selection(...)
```

#### 4.3.3 名称匹配聚类

```python
def _cluster_candidates(self, chunks):
    for chunk in chunks:
        for concept in chunk.concepts:
            key = self._concept_key(concept, name_to_key, accumulators)
            # 关键逻辑：归一化名称后查找
            # 1. 精确匹配：normalize_name(concept.name) in name_to_key
            # 2. 模糊匹配：_is_near_name() — 短名包含在长名中且长度差 ≤ 6 字符
            # 3. 否则：以 normalize_name(concept.name) 作为新 key
```

名称归一化函数：
```python
# ontology_construct/merger/normalizer.py
def normalize_name(name: str) -> str:
    normalized = unicodedata.normalize("NFKC", name)  # Unicode 正规化
    normalized = SPACE_RE.sub("", normalized)          # 去空格
    return normalized.casefold()                       # 小写折叠
```

#### 4.3.4 Embedding 语义聚类（可选增强）

```python
async def _merge_semantic_accumulators(self, accumulators):
    # 1. 为每个 accumulator 生成嵌入文本
    texts = [_embedding_text(accumulators[key]) for key in keys]
    # 2. 调用 embedding API（Qwen text-embedding-v4）
    vectors = await self.embedding_client.embed_texts(texts)
    # 3. 基于余弦相似度的 Union-Find 聚类
    for left, right in pairs:
        if cosine_similarity(vectors[left], vectors[right]) >= threshold:
            union(left, right)
    # 4. 合并同组 accumulator 的 aliases / definitions / evidence
```

嵌入文本的构建方式：
```python
def _embedding_text(accumulator: ConceptAccumulator) -> str:
    return (
        f"概念名称：{name}\n"
        f"别名：{aliases}\n"
        f"定义：{definitions}\n"
        f"证据：{evidence}\n"
        f"抽象层级：{abstraction_level}"
    )
```

这一步骤解决了**名称不同但语义相同**的概念合并（例如"生产设备"与"制造装备"）。

> 配置开关：`concept.use_embedding_clustering = true`，相似度阈值 `concept.embedding_similarity_threshold = 0.9`

### 4.4 阶段四：概念评分与筛选

#### 4.4.1 评分算法

每个概念候选经过聚类后，得到一个 `ConceptCluster`，系统对其进行多维评分：

```python
def _cluster_score(accumulator, relation_degree):
    score = 0.0
    score += min(len(accumulator.source_chunks), 8) * 3.0     # 跨 chunk 出现积分（最高 24）
    score += min(len(accumulator.evidence), 10) * 1.5          # 证据数量积分（最高 15）
    score += min(relation_degree, 8) * 2.0                     # 关系度积分（最高 16）
    score += min(len(accumulator.aliases), 6) * 0.5            # 别名数量积分（最高 3）
    if accumulator.abstraction_level == "high":   score += 2.0  # 高抽象层级加分
    elif accumulator.abstraction_level == "medium": score += 1.0
    if _looks_like_instance(accumulator.name):    score -= 4.0  # 疑似实例扣分
    return round(score, 3)
```

#### 4.4.2 评分维度说明

| 维度 | 权重 | 逻辑 |
|------|------|------|
| 跨 chunk 出现 | ×3.0 (max 8) | 在多个 chunk 中出现说明概念是跨段落的核心概念 |
| 证据数量 | ×1.5 (max 10) | 证据句越多，概念的可信度越高 |
| 关系度 | ×2.0 (max 8) | 参与的关系越多，概念在知识图谱中越中心 |
| 别名数量 | ×0.5 (max 6) | 多种表述方式说明是被广泛引用的概念 |
| 抽象层级 | +1.0~2.0 | high 层级加分，鼓励更抽象的概念 |
| 实例检测 | -4.0 | 名称含数字编号、公司后缀等，疑似具体实例而非概念 |

#### 4.4.3 筛选逻辑

```python
def _select_clusters(self, clusters_by_key):
    # 1. 按 score DESC → relation_degree DESC → source_chunks DESC 排序
    ranked = sorted(clusters_by_key.items(), key=...)

    # 2. 动态阈值：max(配置阈值, 最高分 × ratio)
    dynamic_threshold = max(
        self.config.selection_score_threshold,  # 默认 8.0
        round(top_score * self.config.selection_score_ratio, 3),  # 默认 0.8
    )

    # 3. 逐个筛选
    for key, cluster in ranked:
        # 硬性过滤：证据不足 + 无关系 → 拒绝
        # 评分过滤：< 配置阈值 或 < 动态阈值 → 拒绝
        # 数量过滤：>= max_concepts → 拒绝
        if reject_reasons:
            rejected.append(cluster)
        else:
            selected_keys.append(key)

    # 4. 兜底：至少保留最高分的一个概念
    if not selected_keys:
        selected_keys = [top_key]
```

#### 4.4.4 拒绝原因分类

| 拒绝原因 | 触发条件 |
|----------|----------|
| `证据不足且无关系` | evidence < min_evidence 且 source_chunks ≤ 1 且 relation_degree == 0 |
| `疑似具体实例` | 名称含数字编号、公司后缀、日期等 |
| `低于概念评分阈值` | score < `selection_score_threshold` |
| `低于动态概念评分阈值` | score < `dynamic_threshold` |
| `超过最大概念数` | 排名在 `max_concepts` 之后 |
| `低分概念兜底裁剪` | 所有概念都被拒绝时的兜底保留 |

### 4.5 阶段五：概念关系构建

#### 4.5.1 双重策略

关系构建采用 **LLM 受控构建 + 候选关系兜底** 的双重策略：

```python
async def _build_relations(self, chunks, selected_keys, ...):
    # Step 1: 从候选关系中提取涉及已选概念的候选关系（确定性）
    fallback_relations = self._candidate_relations_for_selected(chunks, ...)

    # Step 2: 如果不启用受控构建，直接使用候选关系
    if not controlled_relations:
        return fallback_triples, fallback_report

    # Step 3: 调用 LLM 做受控关系构建
    prompt = render_concept_relation_prompt(payload, config, ...)
    raw_response = await self.llm_client.complete(prompt)
    controlled_relations = parse_controlled_relations(raw_response, ...)

    # Step 4: LLM 失败或无结果 → 兜底使用候选关系
    if not controlled_relations:
        return fallback_triples, fallback_report
```

#### 4.5.2 受控关系构建

LLM 提示词 `build_concept_relations.txt` 的关键约束：

- **端点锁定**：source 和 target 必须是已确定的 `final_concepts.name`
- **不允许新增概念**
- **关系谓词受控**：只能从预定义词表选择
- **语义合并**：多个表达同一含义的候选关系合并为一条
- **自环删除**：删除 source == target 的自环关系
- **弱关系删除**：删除没有明确语义的弱关系

#### 4.5.3 候选关系提取（确定性）

```python
def _candidate_relations_for_selected(self, chunks, selected_keys, ...):
    for chunk in chunks:
        for relation in chunk.relations:
            # 1. 源和目标都必须是已选概念
            # 2. 排除自环
            # 3. 用 canonical_name 替换原始名
            # 4. 去重：同 (source, predicate, target) 合并 evidence，取 max confidence
```

### 4.6 阶段六：实例层抽取

概念层确定后，系统可以在每个概念下抽取真实实例。

#### 4.6.1 两种抽取模式

| 模式 | 策略 | 适用场景 |
|------|------|----------|
| `per_concept` | 按概念批次，从概念证据和源文本抽取实例 | 概念数量少，源文本不完整 |
| `per_chunk`（默认） | 回到原始 chunk，在完整文本上下文中抽取实例和链接 | 期望更密集的实例连接，减少孤立节点 |

#### 4.6.2 per_chunk 模式的优化

```python
# 关键优化：重用候选概念抽取阶段的实例数据（2N → N 折叠）
def _folded_instance_candidates(self, selected_keys, clusters_by_key):
    """如果候选抽取阶段已经提取了实例，直接重用，不需要额外 LLM 调用"""
    source = self._source_chunk_candidates
    # 将每个实例的 candidate-concept name 映射到最终的 canonical name
    for chunk in source:
        for instance in chunk.instances:
            canonical = name_to_canonical.get(normalize_name(instance.concept))
            if canonical:
                collected.append(instance.model_copy(update={"concept": canonical}))
    return collected
```

**折叠逻辑**：候选概念抽取时已经让 LLM 读了每个 chunk 的全文，如果同时抽取了实例（per_chunk 模式下），归纳阶段可以**直接重用**这些实例，只需将实例原来归属的概念候选名重映射到最终的归一化概念名，无需新一轮 LLM 调用。这就是「2N → N」的优化：候选抽取从 N 次减半。

#### 4.6.3 实例关系构建

实例关系构建也采用 **LLM 受控 + 启发式兜底** 策略：

```python
async def _build_instance_relations(self, instances, concept_relations, focus_names):
    # Step 1: 生成候选实例对
    pair_selection = self._select_instance_relation_pairs(instances, concept_relations, focus_names)

    # Step 2: 每对调用 LLM 确认关系
    for batch in batches(candidate_pairs):
        raw_response = await self.llm_client.complete(prompt)
        relations = parse_instance_relations(raw_response, ...)

    # Step 3: 启发式兜底（高分对直接作为关系）
    fallback_relations = self._fallback_relations_from_pairs(candidate_pairs)
```

实例对的候选生成考虑四个信号：
1. **概念层关系**：两个实例的概念类型之间有概念层关系（权重 +2.0）
2. **证据共现**：两个实例在同一证据句中共同提及（每句 +2.0）
3. **Chunk 共现**：两个实例出现在同一个 chunk 的原始文本中（每 chunk +2.5）
4. **名称互引**：实例名在对方的证据中出现（+1.0~1.5）

#### 4.6.4 孤立节点最小化

系统通过以下机制避免实例成为孤立节点：

- **per_chunk 模式**：在 chunk 全文上下文中同时抽取实例和链接，共现实例天然有链接
- **focus_names 机制**：per_chunk 模式中已通过内联链接连通的实例，跳过关系构建阶段
- **min_relation_pairs_per_instance**：保证每个实例至少有最低数量的候选关系对

### 4.7 阶段七：输出格式化

#### 4.7.1 输出文件结构

```
outputs/run_001/
├── chunks.jsonl              # 分块结果
├── concept_candidates/       # 每个 chunk 的候选概念 JSON
│   ├── chunk_001.json
│   └── ...
├── concept_global.json       # 全局本体（内部格式）
├── concept_clusters.json     # 概念聚类详情
├── concept_selected.json     # 选中的概念（含拒绝列表）
├── concept_relations.json    # 关系构建报告（含模式标记）
├── concept_instances.json    # 实例抽取报告
├── ontology_quality.json     # 质量评估报告
├── global.json               # GlobalOntology 内部格式
├── ontology.json             # 同 global.json，对外暴露
├── formatted_ontology.json   # ⭐ 标准输出（供前端消费）
├── schema.json               # 规范 schema（类层）
├── graph.json                # 规范 graph（实例层）
└── progress.sqlite           # 进度数据库
```

#### 4.7.2 formatted_ontology.json 结构

这是前端 3D 图谱展示和问答的入口数据：

| 顶层字段 | 说明 |
|----------|------|
| `ontology` | 元数据：`ontologyName`, `ontologyDesc`, `profile`, `createdAt`, `updatedAt` |
| `objects` | 对象类型表，含 `objectId`, `objectName`, `objectType`, `attributes`(含 `dataType`, `sampleValue`), `aliases` |
| `links` | 对象类型之间的链接 schema，含 `linkId`, `linkName`, `sourceObjectId`, `targetObjectId`, `predicateCode` |
| `actions` | 行动定义（类层） |
| `instances` | 具体实例，`instanceId`, `instanceName`, `classId`, `attributeValues`(含 `attributeId`), `linkValues`(含 `linkId`, `targetInstanceId`) |
| `actionInvocations` | 行动调用，引用 `actionId`，`inputInstances`/`outputInstances` 指向实例 ID |

#### 4.7.3 类与实例的语义区分

```
objects[*].attributes[*]  ←→  「类的属性定义」(金额是什么、dataType 是什么)
instances[*].attributeValues[*] ←→ 「实例的具体属性值」(金额 = 12345, 并通过 attributeId 回指 schema)

links[*]  ←→  「类之间存在某种关系」
instances[*].linkValues[*] ←→ 「这个实例与那个实例有这条关系」(并通过 linkId 回指 schema)
```

---

## 5. 核心数据模型

### 5.1 概念层模型

```python
# 候选概念（每个 chunk 的输出）
ConceptCandidate:
    name: str              # "生产线"
    definition: str        # "完成特定产品组装流程的工位序列"
    abstraction_level: str # "high"
    aliases: list[str]     # ["产线", "生产流水线"]
    evidence: list[str]    # ["第3章介绍了生产线布局..."]
    should_be_class: bool  # True

# 概念聚类（多个候选概念合并的结果）
ConceptCluster:
    cluster_id: str          # "cluster_000001"
    canonical_name: str      # "生产线"
    aliases: list[str]       # 去重后的别名列表
    merged_names: list[str]  # 所有被合并的名称
    definitions: list[str]   # 合并的定义
    evidence: list[str]      # 合并的证据
    source_chunks: list[str] # 出现在哪些 chunk
    abstraction_level: str   # 取最高
    relation_degree: int     # 参与的关系数
    score: float             # 聚类评分
    keep_reasons: list[str]  # 保留原因
    reject_reasons: list[str]# 拒绝原因
```

### 5.2 本体层模型

```python
# 最终本体实体（概念类）
Entity:
    id: str              # "c000001"
    name: str            # "生产线"
    entity_type: str     # "Concept"
    aliases: list[str]
    attributes: dict     # {"说明": "...", "抽象层级": "high", "证据数量": 5, "关系度": 3, "评分": 22.5}
    source_chunks: list[str]

# 概念间关系
Triple:
    subject_id: str      # "c000001"
    predicate: str       # "包含"
    object_kind: str     # "entity"
    object_id: str       # "c000002"
    confidence: float    # 0.95
    source_chunks: list[str]

# 实例
OntologyInstance:
    id: str              # "real_inst_000001"
    name: str            # "A3总装线"
    class_id: str        # "c000001"
    attribute_values: list[InstanceAttributeValue]
    link_values: list[InstanceLinkValue]
    generated: bool      # False = 真实抽取, True = 生成

# 全局本体
GlobalOntology:
    entities: dict[str, Entity]  # id → Entity
    triples: list[Triple]
    instances: list[OntologyInstance]
    name_to_id: dict[str, str]   # name/alias → id
```

---

## 6. 聚类与去重算法

### 6.1 名称匹配聚类

核心函数：`_cluster_candidates()` + `_concept_key()`

```
输入: 所有 chunk 的 ConceptCandidate 列表
输出: dict[key, ConceptAccumulator]

算法:
1. 遍历每个 chunk 的每个候选概念
2. 对每个概念，判断它是否属于已有聚类:
   a. 归一化名称精确匹配 name_to_key
   b. 别名精确匹配 name_to_key
   c. 模糊匹配 (_is_near_name): 短串包含在长串中且长度差 ≤ 6
3. 匹配成功 → 合并到已有 accumulator (累积 aliases/definitions/evidence)
4. 匹配失败 → 以 normalize_name(name) 创建新 accumulator

合并规则:
- aliases: 去重合并（保持首次出现顺序）
- definitions: 保留所有不同定义
- evidence: 去重合并
- abstraction_level: 取最高 (high > medium > low)
- order: 保留首次出现顺序
```

### 6.2 Embedding 语义聚类

核心函数：`_merge_semantic_accumulators()`

```
输入: dict[key, ConceptAccumulator]
输出: dict[key, ConceptAccumulator] (合并后的)

算法:
1. 构建每个 accumulator 的嵌入文本
2. 调用 embedding API 获取向量
3. Union-Find 聚类 (基于余弦相似度 ≥ threshold)
4. 合并同组 accumulator

合并规则（与名称匹配相同，额外添加）:
- semantic_merged: True (标记为语义合并)
```

### 6.3 关系度计算

```python
def _relation_degree_by_key(self, chunks, name_to_key):
    """统计每个概念参与的关系数量（作为评分因子）"""
    degree: dict[str, int] = {}
    for chunk in chunks:
        for relation in chunk.relations:
            source_key = name_to_key.get(normalize_name(relation.source))
            target_key = name_to_key.get(normalize_name(relation.target))
            if source_key and target_key and source_key != target_key:
                degree[source_key] += 1
                degree[target_key] += 1
    return degree
```

---

## 7. 配置体系

### 7.1 完整配置结构

```yaml
# configs/default.yaml
chunk:
  size: 1500
  overlap: 200

llm:
  backend: "deepseek"
  base_url: "https://api.deepseek.com"
  model: "deepseek-v4-pro"
  api_key_env: "DEEPSEEK_API_KEY"
  temperature: 0.1
  max_tokens: 4000
  timeout_seconds: 60
  max_retries: 3
  concurrency: 4            # 候选概念抽取并发数

concept:
  # ── 候选抽取 ──
  max_candidates_per_chunk: 10
  candidate_prompt: "prompts/extract_concept_candidates.txt"

  # ── 全局归纳 ──
  generalize_prompt: "prompts/generalize_concepts.txt"

  # ── 筛选 ──
  max_concepts: 50
  min_evidence: 1
  selection_score_threshold: 8.0
  selection_score_ratio: 0.8
  abstraction_level: "high"

  # ── 语义聚类 ──
  use_embedding_clustering: false
  embedding_similarity_threshold: 0.9

  # ── 关系构建 ──
  relation_vocab: ["包含","属于","导致","依赖","触发","产生","影响","约束","输入","输出","关联"]
  relation_prompt: "prompts/build_concept_relations.txt"
  controlled_relation_build: true

  # ── 旧实例抽取 ──
  # 默认关闭；schema-first 最终实例层由独立 instance_builder 接管。
  extract_real_instances: false
  instance_extraction_mode: "per_chunk"    # per_chunk | per_concept
  instance_chunk_batch_size: 3             # 每次 LLM 调用的 chunk 批次数
  instance_llm_concurrency: 4
  max_instances_per_concept: 3
  extract_instance_relations: false
  instance_relation_batch_size: 30
  max_instance_relation_pairs: 800
  min_relation_pairs_per_instance: 3
  instance_source_char_limit: 4000
  instance_relation_source_char_limit: 1000

merge:
  embedding_backend: "qwen"
  embedding_model: "text-embedding-v4"
  similarity_threshold: 0.92

ontology_profiles:
  default:
    ontology_name: "通用本体"
    prompt: "prompts/extract_ontology.txt"
  business_process:
    ontology_name: "业务流程本体"
    domain_prompt: "prompts/industrial_process_background.txt"
```

### 7.2 关键配置参数说明

| 参数 | 默认值 | 作用 |
|------|--------|------|
| `max_concepts` | 50 | 最终概念数量的硬上限 |
| `max_candidates_per_chunk` | 10 | 每个 chunk 抽取的最大候选概念数 |
| `selection_score_threshold` | 8.0 | 概念评分硬阈值 |
| `selection_score_ratio` | 0.8 | 动态阈值 = max(8.0, 最高分 × 0.8) |
| `min_evidence` | 1 | 低连通度概念的最小证据数 |
| `abstraction_level` | "high" | 偏好的概念抽象程度 |
| `controlled_relation_build` | true | 是否启用 LLM 受控关系构建 |
| `use_embedding_clustering` | false | 是否启用 embedding 语义聚类 |
| `instance_extraction_mode` | "per_chunk" | 实例抽取模式 |

---

## 8. 质量评估报告

每次构建都会生成 `ontology_quality.json`，包含以下指标：

```python
class ConceptQualityReport:
    # ── 输入侧 ──
    candidate_concept_count: int   # 候选概念总数（所有 chunk 之和）
    candidate_relation_count: int  # 候选关系总数

    # ── 聚类侧 ──
    cluster_count: int             # 聚类后概念数
    semantic_cluster_merge_count: int  # embedding 语义合并数

    # ── 筛选侧 ──
    selected_concept_count: int    # 最终选中概念数
    rejected_concept_count: int    # 被拒绝概念数
    rejection_reason_counts: dict  # 各拒绝原因计数

    # ── 关系侧 ──
    final_relation_count: int      # 最终关系数
    isolated_concept_count: int    # 孤立概念数（没有任何关系）
    average_relation_degree: float # 平均关系度
    relation_predicate_distribution: dict  # 关系谓词分布

    # ── 实例侧 ──
    instance_count: int            # 实例总数
    instance_link_count: int       # 实例链接总数
    isolated_instance_count: int   # 孤立实例数
    isolated_instance_ratio: float # 孤立实例比例

    # ── 配置侧 ──
    max_concepts: int              # 配置的最大概念数
    min_evidence: int              # 配置的最小证据数
    selection_score_threshold: float
    dynamic_score_threshold: float # 实际使用的动态阈值
```

---

## 9. 结构化数据本体构建

除长文本外，系统还支持从 PostgreSQL 结构化数据构建本体（实验性功能，v1）。

### 9.1 构建路径 (Hybrid)

```
PostgreSQL → 元信息读取 → 数据库画像 → 候选生成 → Embedding 语义召回 → LLM Mapping → 本体
```

### 9.2 证据来源

| 证据类型 | 置信度 | 说明 |
|----------|--------|------|
| `foreign_key` | 0.98 | 数据库外键约束 |
| `meta_relation_map` | 0.95 | 元数据显式关系映射 |
| `information_schema` | 0.95 | information_schema 表结构 |
| `table_description` | 0.90 | 表注释/描述 |
| `value_overlap` | 0.76 | 跨表值域重叠推断 |
| `field_name_heuristic` | 0.72 | 字段命名模式推断 (`_id`, `_code`) |
| `embedding` | — | embedding 类合并与关系召回 |
| `llm` | — | LLM 辅助 schema mapping 和润色 |

### 9.3 类层与实例层

- **类层**：表 → Entity (class)，列 → attributes，外键/关系 → Triple (schema 关系)
- **实例层**：行 → OntologyInstance，行内值 → InstanceAttributeValue，外键值 → InstanceLinkValue
- 实例按采样限额生成，LLM 不逐行生成实例

---

## 10. API 接口说明

### 10.1 核心接口

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/ontology/jobs` | 创建异步构建任务（上传文件） |
| `POST` | `/ontology/jobs/import` | 导入已有本体 JSON/JSONL |
| `POST` | `/ontology/structured-configs` | 上传结构化数据 YAML 配置 |
| `GET` | `/ontology/jobs` | 列出所有任务 |
| `GET` | `/ontology/jobs/{jobId}/status` | 查询任务状态和 chunk 进度 |
| `GET` | `/ontology/jobs/{jobId}/result` | 获取 `formatted_ontology.json` |
| `GET` | `/ontology/jobs/{jobId}/canonical` | 获取规范 schema + graph |
| `GET` | `/ontology/jobs/{jobId}/graph` | 获取图可视化数据 |
| `POST` | `/ontology/jobs/{jobId}/question` | 基于本体的问答 |
| `GET` | `/ontology/profiles` | 获取可用本体配置文件和构建默认值 |

### 10.2 创建任务的表单参数

| 参数 | 类型 | 说明 |
|------|------|------|
| `files` / `file` | UploadFile | 上传文件（支持多文件） |
| `profile` | str | 本体配置文件名（default / business_process 等） |
| `backend` | str | LLM 后端（deepseek / qwen / jiutian 等） |
| `construction_mode` | str | "standard" 或 "schema-first"（默认） |
| `max_concepts` | int | 最终概念数上限 |
| `min_concept_evidence` | int | 最低证据数 |
| `abstraction_level` | str | high / medium / low |
| `use_embedding` | bool | 是否启用 embedding 语义聚类 |
| `extract_real_instances` | bool | 是否启用旧 concept pipeline 真实实例抽取；默认关闭，最终实例层由 `instance_builder` 生成 |
| `drop_instances` | bool | 是否丢弃实例层数据 |
| `supplemental_prompt` | str | 补充提示词（领域建模背景等） |
| `instance_count` | int | 生成的模拟实例数 |

---

## 附录 A：关系谓词受控词表

```python
relation_vocab = [
    "包含",    # has_part / contains
    "属于",    # is_a / instance_of
    "导致",    # causes
    "依赖",    # depends_on
    "触发",    # triggers
    "产生",    # produces
    "影响",    # affects / influences
    "约束",    # constrains
    "输入",    # input_to
    "输出",    # output_from
    "关联",    # related_to（通用兜底）
]
```

LLM 输出的关系谓词会被自动归一化到词表中最相近的项：如果谓词不在词表中，查找词表中有包含关系的项，否则兜底为"关联"。

## 附录 B：抽象层级语义

| 层级 | 含义 | 示例 |
|------|------|------|
| `high` | 高度抽象的上位概念 | "生产系统"、"质量管理"、"供应链" |
| `medium` | 中层概念 | "生产线"、"质检流程"、"物料配送" |
| `low` | 接近实例层的具体类型 | "A类焊接工序"、"冲压模具"、"来料检验单" |

评分时 high 获得 +2.0，medium 获得 +1.0 的加成。

## 附录 C：实例名称检测规则

```python
def _looks_like_instance(name: str) -> bool:
    # 数字数量 ≥ 2 → 疑似实例
    if sum(c.isdigit() for c in name) >= 2: return True

    # 含实例标记词 → 疑似实例
    markers = ("有限公司", "股份", "先生", "女士", "第", "号", "年", "月", "日")
    return any(marker in name for marker in markers)
```

被判定为实例的概念在评分时会被扣除 4.0 分。

## 附录 D：错误恢复与降级策略

概念优先 pipeline 具有多层降级保护：

```
LLM 全局归纳
  │
  ├─ 成功 + 有概念 ──→ 正常流程
  │
  └─ 失败 / 无概念
       │
       └─→ 确定性聚类 pipeline
            │
            ├─ 名称匹配聚类
            ├─ Embedding 语义聚类（可选）
            ├─ 评分筛选
            │
            └─ 关系构建
                 │
                 ├─ LLM 受控构建 ──→ 成功 → 使用
                 │
                 └─ 失败 / 无结果
                      │
                      └─→ 候选关系兜底
```

实例关系构建的降级：
```
实例对 → LLM 确认
  │
  ├─ 成功 → 使用 LLM 确认的关系
  ├─ 部分失败 → 混合使用（LLM + 启发式兜底）
  └─ 全部失败 → 纯启发式兜底（高分对直接作为关系）
```

---

> **文档版本**: 1.0  
> **生成日期**: 2026-06-26  
> **适用代码分支**: main  
> **维护者**: Ontology Construct Team
