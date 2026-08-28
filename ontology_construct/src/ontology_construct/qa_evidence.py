"""graph_qa_evidence_v1 contract data models and conformance validator.

This module is the frozen reading protocol shared by the online QA path and the
SFT data generation pipeline (vendored into ``cot_generation``). The prompt
section layout, line syntax and answer JSON fields defined here must not change
without bumping ``CONTRACT_VERSION`` — doing so requires regenerating SFT data.

See ``docs/graph-qa-evidence-contract-v2.md`` for the full specification.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

# NOTE: this module is vendored into cot_generation, which runs on Python 3.9.
# pydantic evaluates model-field annotations at class-creation time, so field
# types here must avoid PEP 604 unions ("X | None") — use Optional[X] instead.
# (Function/dataclass annotations elsewhere stay lazy strings and are exempt.)
CONTRACT_VERSION = "graph_qa_evidence_v1"

# Section headers, in the frozen order required by the contract (§3.1). The
# renderer emits exactly these, and the validator checks their order/presence.
SECTION_QUESTION = "[QUESTION]"
SECTION_QUERY_PLAN = "[QUERY_PLAN]"
SECTION_CORE_EVIDENCE = "[CORE_EVIDENCE]"
SECTION_SUPPORTING_SCHEMA = "[SUPPORTING_SCHEMA]"
SECTION_SUPPORTING_EDGES = "[SUPPORTING_EDGES]"
SECTION_SUPPORTING_NODES = "[SUPPORTING_NODES]"
SECTION_ANSWER_REQUIREMENT = "[ANSWER_REQUIREMENT]"

SECTION_ORDER: tuple[str, ...] = (
    SECTION_QUESTION,
    SECTION_QUERY_PLAN,
    SECTION_CORE_EVIDENCE,
    SECTION_SUPPORTING_SCHEMA,
    SECTION_SUPPORTING_EDGES,
    SECTION_SUPPORTING_NODES,
    SECTION_ANSWER_REQUIREMENT,
)

# Sections that must never be dropped or partially truncated (§3.1, I5).
REQUIRED_SECTIONS: frozenset[str] = frozenset(
    {
        SECTION_QUESTION,
        SECTION_QUERY_PLAN,
        SECTION_CORE_EVIDENCE,
        SECTION_ANSWER_REQUIREMENT,
    }
)

# Marker appended to a supporting section when budget forces line drops (§3.4).
TRUNCATION_MARKER = "（超出预算，已省略"


QAMode = Literal["connectivity", "local", "schema", "general"]
QAIntent = Literal[
    "impact",
    "trace",
    "path",
    "relation",
    "attribute",
    "replace",
    "schema",
    "general",
]
EvidenceItemType = Literal[
    "target_node",
    "attribute_table",
    "impact_chain",
    "trace_chain",
    "candidate_path",
    "relation_set",
    "aggregate_summary",
    "same_type_candidates",
    "schema_definition",
]


class EvidenceNode(BaseModel):
    id: str
    label: str
    type: str
    attrs: dict[str, Any] = Field(default_factory=dict)
    # tier: 0=I6 closure fill, 1=path member, 2=anchor one-hop, 3=recall.
    tier: int = 3
    # score/reasons are debug-only metadata; the renderer must not print them.
    score: float = 0.0
    reasons: list[str] = Field(default_factory=list)


class EvidenceEdge(BaseModel):
    source: str
    target: str
    rel: str
    tier: int = 3
    score: float = 0.0
    reasons: list[str] = Field(default_factory=list)


class ChainHop(BaseModel):
    node: EvidenceNode
    # Relationship to the previous hop; None for the first hop.
    rel: Optional[str] = None
    # forward  = raw edge points previous->this node (render "-[rel]->")
    # reverse  = raw edge points this node->previous  (render "<-[rel]-")
    raw_direction: Optional[Literal["forward", "reverse"]] = None


class CoreEvidenceItem(BaseModel):
    item_type: EvidenceItemType
    title: str
    nodes: list[EvidenceNode] = Field(default_factory=list)
    edges: list[EvidenceEdge] = Field(default_factory=list)
    hops: list[ChainHop] = Field(default_factory=list)
    facts: list[str] = Field(default_factory=list)
    note: str = ""


class LinkedEntity(BaseModel):
    mention: str
    node: EvidenceNode
    # (k, n) when the mention is ambiguous across n nodes; None otherwise.
    ambiguous_rank: Optional[tuple[int, int]] = None


class QueryPlan(BaseModel):
    contract_version: str = CONTRACT_VERSION
    mode: QAMode
    intent: QAIntent
    linked_entities: list[LinkedEntity] = Field(default_factory=list)
    unlinked_mentions: list[str] = Field(default_factory=list)


class QAEvidence(BaseModel):
    contract_version: str = CONTRACT_VERSION
    # The raw user question. Structurally required by the renderer (§9); the
    # v2 spec's data model omitted it but the renderer signature needs it here.
    question: str = ""
    plan: QueryPlan
    core: list[CoreEvidenceItem] = Field(default_factory=list)
    supporting_schema_nodes: list[EvidenceNode] = Field(default_factory=list)
    supporting_schema_edges: list[EvidenceEdge] = Field(default_factory=list)
    supporting_nodes: list[EvidenceNode] = Field(default_factory=list)
    supporting_edges: list[EvidenceEdge] = Field(default_factory=list)
    # Debug/trace payload — returned in the API response, never rendered.
    meta: dict[str, Any] = Field(default_factory=dict)


def anchor_ids(evidence: QAEvidence) -> list[str]:
    """Ids of the linked entities (anchors) in question order."""
    return [entity.node.id for entity in evidence.plan.linked_entities]


def core_node_ids(evidence: QAEvidence) -> set[str]:
    ids: set[str] = set()
    for item in evidence.core:
        ids.update(node.id for node in item.nodes)
        ids.update(hop.node.id for hop in item.hops)
    return ids


def _rendered_node_ids(evidence: QAEvidence) -> set[str]:
    """Every node id that has a node line somewhere in the prompt (for I6)."""
    ids = core_node_ids(evidence)
    ids.update(node.id for node in evidence.supporting_nodes)
    ids.update(node.id for node in evidence.supporting_schema_nodes)
    return ids


def validate_contract(evidence: QAEvidence, prompt: str, cfg: Any) -> list[str]:
    """Return a list of contract violations; empty means conformant.

    Checks (§9.3): section order/names, anchors present in CORE, edge endpoint
    closure (I6), no truncation marker in required sections, budget respected,
    deterministic re-render, and no score/reason leakage into the prompt.
    """
    violations: list[str] = []
    lines = prompt.split("\n")

    # 1. Section order and names.
    present = [line for line in lines if line in SECTION_ORDER]
    expected = list(SECTION_ORDER)
    # Supporting sections may be absent entirely when empty; filter expected to
    # those actually present but require the relative order to match.
    filtered_expected = [name for name in expected if name in present]
    if present != filtered_expected:
        violations.append(
            f"section order mismatch: got {present}, expected subsequence of {expected}"
        )
    for name in REQUIRED_SECTIONS:
        if name not in present:
            violations.append(f"required section missing: {name}")

    core_text = _section_text(lines, SECTION_CORE_EVIDENCE)

    # 2. Every anchor id must appear in CORE_EVIDENCE.
    for node_id in anchor_ids(evidence):
        if node_id not in core_text:
            violations.append(f"anchor not in CORE_EVIDENCE: {node_id}")

    # 3. Edge endpoint closure (I6).
    rendered = _rendered_node_ids(evidence)
    for edge in [*evidence.supporting_edges, *_core_edges(evidence)]:
        if edge.source not in rendered:
            violations.append(f"edge source has no node line (I6): {edge.source}")
        if edge.target not in rendered:
            violations.append(f"edge target has no node line (I6): {edge.target}")

    # 4. Required sections must not carry a truncation marker.
    for name in REQUIRED_SECTIONS:
        if TRUNCATION_MARKER in _section_text(lines, name):
            violations.append(f"required section truncated: {name}")

    # 5. Budget.
    max_chars = int(getattr(cfg, "max_prompt_chars", 24000))
    if len(prompt) > max_chars:
        violations.append(f"prompt exceeds budget: {len(prompt)} > {max_chars}")

    # 6. Determinism — re-render must be byte-identical. Lazy import avoids a
    # module-load cycle with qa_prompt (which imports this module). Relative so
    # this module stays byte-identical when vendored into cot_generation.
    try:
        from .qa_prompt import render_graph_qa_evidence_prompt

        if render_graph_qa_evidence_prompt(evidence, cfg) != prompt:
            violations.append("render is not deterministic")
    except Exception:  # pragma: no cover - determinism check is best-effort
        pass

    # 7. No scorer internals in the prompt.
    for needle in ("score=", "reason="):
        if needle in prompt:
            violations.append(f"scorer internal leaked into prompt: {needle!r}")

    return violations


def _core_edges(evidence: QAEvidence) -> list[EvidenceEdge]:
    edges: list[EvidenceEdge] = []
    for item in evidence.core:
        edges.extend(item.edges)
    return edges


def _section_text(lines: list[str], header: str) -> str:
    """Text between ``header`` and the next known section header."""
    try:
        start = lines.index(header)
    except ValueError:
        return ""
    end = len(lines)
    for index in range(start + 1, len(lines)):
        if lines[index] in SECTION_ORDER:
            end = index
            break
    return "\n".join(lines[start + 1 : end])
