"""QueryPlanner (§4).

Mode is decided by anchor cardinality (an objective fact); intent keywords only
softly steer assembly. A wrong intent must at worst bring extra evidence, never
drop the path (see qa_assembly I2).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Relative imports so this module is byte-identical when vendored into cot_generation.
from .qa_evidence import (
    EvidenceNode,
    LinkedEntity,
    QAIntent,
    QAMode,
    QueryPlan,
)
from .qa_linking import LinkResult, SchemaHint, resolve_entities

# Intent keyword table, checked in priority order (§4.2). Ordering rationale:
#   replace first — "替代/备用/换成/同类" are specific asks that outrank the failure
#     context word "坏了" (which also appears in replace questions).
#   trace before impact — failure words (坏了/毁坏/故障) are direction-neutral; a
#     cause-asking phrase ("原因/为什么/根因") must win over them so "X坏了是什么原因"
#     traces upstream instead of enumerating downstream impact.
#   trace/impact before path — so an incidental "步骤/流程" does not hijack a
#     cause/impact question ("某步骤出问题是哪里的原因" -> trace, not path).
_INTENT_TERMS: tuple[tuple[QAIntent, tuple[str, ...]], ...] = (
    ("replace", ("替代", "替换", "备用", "换成", "同类", "可以用什么", "有没有别的")),
    ("trace", ("来源", "上游", "前置", "根因", "起因", "谁导致", "什么导致", "追溯", "哪里来", "原因", "为什么", "什么原因", "造成", "症结", "问题出在")),
    ("impact", ("坏了", "毁坏", "损坏", "坏掉", "报废", "故障", "异常", "失效", "停机", "报警", "会影响", "是否影响", "影响哪些", "影响到", "影响下游", "影响后续", "影响范围", "如何影响", "怎么影响", "有什么影响", "受影响", "波及")),
    ("path", ("路径", "链路", "经过哪些", "怎么到", "如何到达", "流程", "步骤")),
    ("attribute", ("状态", "属性", "编码", "数量", "时间", "是多少", "什么状态")),
    ("relation", ("包含哪些", "用了哪些", "属于哪个", "关联哪些", "执行于", "处理了哪些", "有哪些")),
    ("schema", ("什么是", "定义", "概念", "解释一下")),
)
_SCHEMA_WORDS = ("什么是", "定义", "概念", "解释一下", "类型", "关系类型")

# Direct-neighbor / degree questions ("有多少个直接上游…下游…", "直接相关的对象有哪些")
# must read raw one-hop edges (RelationSet), not the semantic fan-out that trace/
# impact would build — the trace view follows the reversed semantic digraph and can
# hide direct in-edges entirely. Checked before the keyword table because "上游/下游"
# would otherwise route these to trace. Guarded to plain direct-neighbor asks: any
# failure or cause wording keeps the question on the trace/impact path.
_DIRECT_WORD = "直接"
_NEIGHBOR_CUES = ("上游", "下游", "相关的对象", "关联的对象", "关联了", "邻居")
_COUNT_ENUM_CUES = ("多少", "几个", "哪些")
_CAUSAL_FAILURE_CUES = (
    "坏了", "毁坏", "损坏", "坏掉", "报废", "故障", "异常", "失效", "停机", "报警",
    "影响", "波及", "根因", "起因", "原因", "为什么", "导致", "造成", "追溯", "症结", "问题出在",
)


def _is_direct_neighbor_question(text: str) -> bool:
    return (
        _DIRECT_WORD in text
        and any(cue in text for cue in _NEIGHBOR_CUES)
        and any(cue in text for cue in _COUNT_ENUM_CUES)
        and not any(cue in text for cue in _CAUSAL_FAILURE_CUES)
    )


@dataclass
class PlanResult:
    plan: QueryPlan
    schema_hints: list[SchemaHint] = field(default_factory=list)

    @property
    def anchors(self) -> list[LinkedEntity]:
        return self.plan.linked_entities


def detect_intent(question: str) -> QAIntent:
    text = question.casefold()
    if _is_direct_neighbor_question(text):
        return "relation"
    for intent, terms in _INTENT_TERMS:
        if any(term in text for term in terms):
            return intent
    return "general"


def resolve_mode(anchors: list[LinkedEntity], intent: QAIntent, question: str) -> QAMode:
    distinct = {entity.node.id for entity in anchors}
    if len(distinct) >= 2:
        return "connectivity"
    if len(distinct) == 1:
        return "local"
    if intent == "schema" or any(word in question for word in _SCHEMA_WORDS):
        return "schema"
    return "general"


def plan_question(
    *,
    question: str,
    instance_graph: Any,
    schema_graph: Any,
    cfg: Any,
    forced_anchors: list[str] | None = None,
) -> PlanResult:
    link = resolve_entities(
        question=question,
        instance_graph=instance_graph,
        schema_graph=schema_graph,
        max_anchors=int(getattr(cfg, "max_anchors", 6)),
    )
    if forced_anchors:
        link = _apply_forced_anchors(link, forced_anchors, instance_graph)

    intent = detect_intent(question)
    mode = resolve_mode(link.anchors, intent, question)
    plan = QueryPlan(
        mode=mode,
        intent=intent,
        linked_entities=link.anchors,
        unlinked_mentions=link.unlinked_mentions,
    )
    return PlanResult(plan=plan, schema_hints=link.schema_hints)


def _apply_forced_anchors(
    link: LinkResult, forced_anchors: list[str], instance_graph: Any
) -> LinkResult:
    existing = {entity.node.id for entity in link.anchors}
    node_by_id = {str(node.id): node for node in getattr(instance_graph, "nodes", [])}
    for anchor_id in forced_anchors:
        anchor_id = str(anchor_id)
        if anchor_id in existing:
            continue
        node = node_by_id.get(anchor_id)
        if node is None:
            continue
        link.anchors.append(
            LinkedEntity(
                mention=str(node.label),
                node=EvidenceNode(
                    id=str(node.id),
                    label=str(node.label),
                    type=str(node.type),
                    attrs=dict(getattr(node, "attrs", {}) or {}),
                    tier=1,
                ),
            )
        )
        existing.add(anchor_id)
    return link
