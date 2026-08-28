from __future__ import annotations

from typing import Any

# Relative import so this module is byte-identical when vendored into
# cot_generation (see docs/graph-qa-evidence-contract-v2.md §2.1 / SFT migration).
from .qa_evidence import (
    CONTRACT_VERSION,
    SECTION_ANSWER_REQUIREMENT,
    SECTION_CORE_EVIDENCE,
    SECTION_QUERY_PLAN,
    SECTION_QUESTION,
    SECTION_SUPPORTING_EDGES,
    SECTION_SUPPORTING_NODES,
    SECTION_SUPPORTING_SCHEMA,
    ChainHop,
    CoreEvidenceItem,
    EvidenceEdge,
    EvidenceNode,
    QAEvidence,
)

# Fixed, byte-frozen answer requirement text (§3.5).
_ANSWER_REQUIREMENT_TEXT = (
    "只能基于上述证据回答，不得引用证据中未出现的节点或关系。\n"
    "若证据不足以回答，请将 answer_status 设为 insufficient_evidence，"
    "并在 missing 中列出缺少的节点、关系或路径。\n"
    "输出格式：<think>推理步骤，引用证据中的节点 id</think><answer>JSON</answer>\n"
    'answer JSON 字段：answer_status("answerable"|"insufficient_evidence"), '
    "summary, path, edges, groups, missing。"
)

_CORE_INDENT = "      "  # 6 spaces for core-item content (§3.3)
_SUPPORT_INDENT = "  "  # 2 spaces for supporting lines / plan sub-items
# Priority attribute keys shown first on a node line (§3.3).
_ATTR_PRIORITY = ("实例编码", "对象编码", "状态", "时间", "source_field")
_ATTR_VALUE_LIMIT = 80


def render_graph_rag_prompt(
    *,
    question: str,
    retrieval: Any,
    max_prompt_chars: int,
    include_retrieval_trace: bool = True,
) -> str:
    lines: list[str] = [
        "[QUESTION]",
        question,
        "",
        "[RELEVANT_SCHEMA]",
    ]
    lines.extend(_schema_lines(retrieval))
    lines.extend(["", "[EVIDENCE_SUBGRAPH]", "Seeds:"])
    if retrieval.seeds:
        for node in retrieval.seeds:
            lines.append(_node_line(node))
    else:
        lines.append("  无直接命中 seed，以下证据为预算内候选子图。")

    lines.append("")
    lines.append("Nodes:")
    for node in retrieval.evidence_nodes:
        lines.append(_node_line(node))

    lines.append("")
    lines.append("Edges:")
    for edge in retrieval.evidence_edges:
        reasons = ",".join(edge.reasons)
        lines.append(
            f"  {edge.source} -> {edge.target} | {edge.rel} | "
            f"score={edge.score:.3f} | reason={reasons}"
        )

    lines.append("")
    lines.append("Paths:")
    if retrieval.evidence_paths:
        for path in retrieval.evidence_paths:
            lines.append("  " + " -> ".join(path))
    else:
        lines.append("  无显式 seed 间路径。")

    if include_retrieval_trace:
        lines.extend(["", "[RETRIEVAL_TRACE]"])
        for item in retrieval.trace:
            lines.append(f"- {item}")
        if retrieval.budget:
            budget_text = ", ".join(f"{k}={v}" for k, v in retrieval.budget.items())
            lines.append(f"- budget: {budget_text}")

    lines.extend(
        [
            "",
            "[ANSWER_REQUIREMENT]",
            "只能基于上述证据回答。若证据不足，请说明缺少哪些节点、关系或属性。",
            "输出保持 <think></think><answer></answer> 格式。",
        ]
    )
    return _fit_budget(lines, max_prompt_chars)


def _schema_lines(retrieval: Any) -> list[str]:
    lines: list[str] = ["ObjectType:"]
    if retrieval.relevant_schema_nodes:
        for node in retrieval.relevant_schema_nodes:
            desc = node.attrs.get("说明") or node.attrs.get("对象编码") or ""
            suffix = f" — {desc}" if desc else ""
            lines.append(f"  {node.label}: {node.type}{suffix}")
    else:
        lines.append("  无相关对象类型。")

    lines.append("")
    lines.append("RelationType:")
    if retrieval.relevant_schema_edges:
        for edge in retrieval.relevant_schema_edges:
            lines.append(f"  {edge.source} -> {edge.target}: {edge.rel}")
    else:
        lines.append("  无相关关系类型。")
    return lines


def _node_line(node: Any) -> str:
    attrs = _compact_attrs(node.attrs)
    attr_text = f" | attrs={attrs}" if attrs else ""
    reasons = ",".join(node.reasons)
    return (
        f"  {node.id} | {node.type} | {node.label}{attr_text} | "
        f"score={node.score:.3f} | reason={reasons}"
    )


def _compact_attrs(attrs: dict[str, Any], *, limit: int = 6) -> str:
    parts: list[str] = []
    priority = ("实例编码", "对象编码", "状态", "时间", "source_field")
    keys = [key for key in priority if key in attrs]
    keys.extend(key for key in attrs if key not in keys)
    for key in keys[:limit]:
        value = attrs.get(key)
        if value in (None, ""):
            continue
        text = str(value)
        if len(text) > 80:
            text = text[:77] + "..."
        parts.append(f"{key}={text}")
    return ", ".join(parts)


def _fit_budget(lines: list[str], max_prompt_chars: int) -> str:
    text = "\n".join(lines)
    if len(text) <= max_prompt_chars:
        return text
    kept: list[str] = []
    for line in lines:
        if len("\n".join([*kept, line])) > max_prompt_chars - 120:
            break
        kept.append(line)
    kept.append("[TRUNCATED_BY_PROMPT_BUDGET]")
    return "\n".join(kept)


# --------------------------------------------------------------------------- #
# graph_qa_evidence_v1 renderer (§3, §9)                                       #
# --------------------------------------------------------------------------- #


def render_graph_qa_evidence_prompt(evidence: QAEvidence, cfg: Any) -> str:
    """Render a QAEvidence into a graph_qa_evidence_v1 prompt.

    Pure function: same evidence + cfg -> byte-identical prompt (§3.6). Required
    sections (QUESTION/QUERY_PLAN/CORE_EVIDENCE/ANSWER_REQUIREMENT) are never
    truncated; only supporting sections shrink under budget pressure (§9.2).
    """
    max_chars = int(getattr(cfg, "max_prompt_chars", 24000))

    required_head = [
        SECTION_QUESTION,
        evidence.question.strip(),
        "",
        *_render_query_plan(evidence),
        "",
        SECTION_CORE_EVIDENCE,
        *_render_core(evidence, cfg),
    ]
    answer_tail = ["", SECTION_ANSWER_REQUIREMENT, _ANSWER_REQUIREMENT_TEXT]

    fixed_len = len("\n".join([*required_head, *answer_tail]))
    # Character budget available to all supporting sections combined.
    support_budget = max(0, max_chars - fixed_len - 2)

    schema_block, schema_dropped, schema_keep = _render_supporting_schema(
        evidence, int(getattr(cfg, "budget_schema_chars", 3000))
    )
    edges_block, edges_dropped, edges_keep = _render_supporting_edges(
        evidence, int(getattr(cfg, "budget_edges_chars", 4500))
    )
    nodes_block, nodes_dropped, nodes_keep = _render_supporting_nodes(
        evidence, int(getattr(cfg, "budget_nodes_chars", 4500))
    )

    blocks = [
        _SupportBlock(SECTION_SUPPORTING_SCHEMA, schema_block, schema_dropped, schema_keep),
        _SupportBlock(SECTION_SUPPORTING_EDGES, edges_block, edges_dropped, edges_keep),
        _SupportBlock(SECTION_SUPPORTING_NODES, nodes_block, nodes_dropped, nodes_keep),
    ]

    # Global second pass: if the combined supporting text still overflows, trim
    # from NODES -> EDGES -> SCHEMA (§9.2 step 2). tier-0 lines are marked
    # undroppable by the per-section renderers and were placed first.
    _enforce_global_budget(blocks, support_budget)

    parts: list[str] = list(required_head)
    for block in blocks:
        if block.body:
            parts.extend(["", block.header, *block.body])
    parts.extend(answer_tail)
    return "\n".join(parts)


class _SupportBlock:
    __slots__ = ("header", "body", "dropped", "mandatory_count")

    def __init__(
        self, header: str, body: list[str], dropped: int, mandatory_count: int = 0
    ) -> None:
        self.header = header
        self.body = body
        self.dropped = dropped
        # Leading mandatory (tier-0 / structural) lines that must never be cut.
        self.mandatory_count = mandatory_count


def _render_query_plan(evidence: QAEvidence) -> list[str]:
    plan = evidence.plan
    lines = [
        SECTION_QUERY_PLAN,
        f"contract_version={plan.contract_version or CONTRACT_VERSION}",
        f"mode={plan.mode}",
        f"intent={plan.intent}",
        "linked_entities:",
    ]
    if plan.linked_entities:
        for entity in plan.linked_entities:
            suffix = ""
            if entity.ambiguous_rank is not None:
                k, n = entity.ambiguous_rank
                suffix = f"（歧义 {k}/{n}）"
            node = entity.node
            lines.append(
                f"{_SUPPORT_INDENT}- {entity.mention} -> "
                f"{node.id} | {node.type} | {node.label}{suffix}"
            )
    else:
        lines.append(f"{_SUPPORT_INDENT}- （无）")
    lines.append("unlinked_mentions:")
    if plan.unlinked_mentions:
        for mention in plan.unlinked_mentions:
            lines.append(f"{_SUPPORT_INDENT}- {mention}")
    else:
        lines.append(f"{_SUPPORT_INDENT}- （无）")
    return lines


def _render_core(evidence: QAEvidence, cfg: Any) -> list[str]:
    if not evidence.core:
        return [
            f"{_CORE_INDENT}（未找到核心证据项；参考 QUERY_PLAN 中未链接的实体与下方 supporting 区）"
        ]
    lines: list[str] = []
    for index, item in enumerate(evidence.core, start=1):
        lines.extend(_render_core_item(index, item, cfg))
    return lines


def _render_core_item(index: int, item: CoreEvidenceItem, cfg: Any) -> list[str]:
    header = f"({index}) {item.title}:"
    body: list[str] = []
    if item.item_type in {"impact_chain", "trace_chain", "candidate_path"}:
        body.extend(_render_hops(item.hops, cfg))
    elif item.item_type == "relation_set":
        body.extend(_render_relation_set(item, cfg))
    elif item.item_type in {"target_node", "same_type_candidates"}:
        body.extend(f"{_CORE_INDENT}{_node_repr(node, cfg)}" for node in item.nodes)
    else:  # attribute_table, aggregate_summary, schema_definition
        body.extend(f"{_CORE_INDENT}{fact}" for fact in item.facts)
    if item.note:
        body.append(f"{_CORE_INDENT}note: {_one_line(item.note)}")
    return [header, *body]


def _render_hops(hops: list[ChainHop], cfg: Any) -> list[str]:
    lines: list[str] = []
    for position, hop in enumerate(hops):
        node_line = _node_repr(hop.node, cfg)
        if position == 0 or hop.rel is None:
            lines.append(f"{_CORE_INDENT}{node_line}")
        elif hop.raw_direction == "reverse":
            lines.append(f"{_CORE_INDENT}<-[{hop.rel}]- {node_line}")
        else:  # forward or unspecified
            lines.append(f"{_CORE_INDENT}-[{hop.rel}]-> {node_line}")
    return lines


def _render_relation_set(item: CoreEvidenceItem, cfg: Any) -> list[str]:
    # Convention (§ assembly): item.nodes[0] is the anchor, the rest neighbours.
    lines: list[str] = []
    anchor_id = item.nodes[0].id if item.nodes else ""
    node_by_id = {node.id: node for node in item.nodes}
    for edge in item.edges:
        neighbor_id = edge.target if edge.source == anchor_id else edge.source
        neighbor = node_by_id.get(neighbor_id)
        suffix = f" | {neighbor.type} | {neighbor.label}" if neighbor else ""
        lines.append(
            f"{_CORE_INDENT}{edge.source} -[{edge.rel}]-> {edge.target}{suffix}"
        )
    # Rolled-up aggregate lines (I3) are carried as pre-formed facts.
    lines.extend(f"{_CORE_INDENT}{fact}" for fact in item.facts)
    return lines


def _render_supporting_schema(
    evidence: QAEvidence, budget: int
) -> tuple[list[str], int, int]:
    if not evidence.supporting_schema_nodes and not evidence.supporting_schema_edges:
        return [], 0, 0
    label_by_id = {node.id: node.label for node in evidence.supporting_schema_nodes}
    optional: list[str] = ["ObjectType:"]
    for node in sorted(evidence.supporting_schema_nodes, key=lambda n: (n.label, n.id)):
        desc = node.attrs.get("说明") or node.attrs.get("对象编码") or ""
        optional.append(f"{_SUPPORT_INDENT}{node.label}: {desc}".rstrip())
    optional.append("RelationType:")
    for edge in sorted(
        evidence.supporting_schema_edges, key=lambda e: (e.source, e.target, e.rel)
    ):
        src = label_by_id.get(edge.source, edge.source)
        tgt = label_by_id.get(edge.target, edge.target)
        optional.append(f"{_SUPPORT_INDENT}{src} -[{edge.rel}]-> {tgt}")
    kept, dropped = _fit_lines([], optional, budget)
    return kept, dropped, 0


def _render_supporting_edges(
    evidence: QAEvidence, budget: int
) -> tuple[list[str], int, int]:
    ordered = sorted(
        evidence.supporting_edges, key=lambda e: (e.tier, e.source, e.target, e.rel)
    )
    mandatory = [_ev_edge_line(edge) for edge in ordered if edge.tier == 0]
    optional = [_ev_edge_line(edge) for edge in ordered if edge.tier != 0]
    kept, dropped = _fit_lines(mandatory, optional, budget)
    return kept, dropped, len(mandatory)


def _render_supporting_nodes(
    evidence: QAEvidence, budget: int
) -> tuple[list[str], int, int]:
    ordered = sorted(evidence.supporting_nodes, key=lambda n: (n.tier, n.id))
    mandatory = [_ev_node_line(node, None) for node in ordered if node.tier == 0]
    optional = [_ev_node_line(node, None) for node in ordered if node.tier != 0]
    kept, dropped = _fit_lines(mandatory, optional, budget)
    return kept, dropped, len(mandatory)


def _fit_lines(
    mandatory: list[str], optional: list[str], budget: int
) -> tuple[list[str], int]:
    """Keep all mandatory lines, then add optional lines until budget is hit.

    Returns (kept_lines, dropped_count). A truncation marker is appended when
    any optional line is dropped. Mandatory lines are always kept, even past
    budget (tier-0 / I6 closure must never be cut).
    """
    kept = list(mandatory)
    used = sum(len(line) + 1 for line in kept)
    dropped = 0
    for line in optional:
        cost = len(line) + 1
        if used + cost > budget:
            dropped += 1
            continue
        kept.append(line)
        used += cost
    if dropped:
        kept.append(f"{_SUPPORT_INDENT}…（超出预算，已省略 {dropped} 行）")
    return kept, dropped


def _enforce_global_budget(blocks: list[_SupportBlock], budget: int) -> None:
    def total() -> int:
        return sum(
            len("\n".join(["", block.header, *block.body])) for block in blocks if block.body
        )

    # Trim from the last block backwards (NODES -> EDGES -> SCHEMA).
    for block in reversed(blocks):
        while total() > budget and _has_droppable(block):
            _drop_last_optional(block)


def _has_droppable(block: _SupportBlock) -> bool:
    body = block.body
    if not body:
        return False
    # A trailing marker line plus at least one non-mandatory optional line.
    droppable = [line for line in body if not _is_marker(line)]
    return len(droppable) > block.mandatory_count


def _drop_last_optional(block: _SupportBlock) -> None:
    body = block.body
    marker_present = bool(body) and _is_marker(body[-1])
    core = body[:-1] if marker_present else list(body)
    # Never drop below the mandatory prefix.
    if len(core) <= block.mandatory_count:
        return
    core.pop()
    block.dropped += 1
    block.body = [*core, f"{_SUPPORT_INDENT}…（超出预算，已省略 {block.dropped} 行）"]


def _is_marker(line: str) -> bool:
    return "超出预算，已省略" in line


def _ev_node_line(node: EvidenceNode, cfg: Any) -> str:
    return f"{_SUPPORT_INDENT}{_node_repr(node, cfg)}"


def _node_repr(node: EvidenceNode, cfg: Any) -> str:
    limit = int(getattr(cfg, "max_attrs_per_node", 6)) if cfg is not None else 6
    attrs = _core_attrs(node.attrs, limit)
    base = f"{node.id} | {node.type} | {node.label}"
    return f"{base} | {attrs}" if attrs else base


def _ev_edge_line(edge: EvidenceEdge) -> str:
    return f"{_SUPPORT_INDENT}{edge.source} -[{edge.rel}]-> {edge.target}"


def _core_attrs(attrs: dict[str, Any], limit: int) -> str:
    keys = [key for key in _ATTR_PRIORITY if key in attrs]
    keys.extend(key for key in attrs if key not in keys)
    parts: list[str] = []
    for key in keys:
        if len(parts) >= limit:
            break
        value = attrs.get(key)
        if value in (None, ""):
            continue
        text = str(value)
        if len(text) > _ATTR_VALUE_LIMIT:
            text = text[: _ATTR_VALUE_LIMIT - 3] + "..."
        parts.append(f"{key}={text}")
    return ", ".join(parts)


def _one_line(text: str) -> str:
    return " ".join(str(text).split())
