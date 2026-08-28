"""Entity linking (EntityResolver, §5).

Links question mentions to instance nodes with longest-match shadowing and code
boundary rules, so a long instance mention ("0011号生产工单") suppresses the
shorter type substring ("生产工单") that used to cause seed explosion.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# Relative import so this module is byte-identical when vendored into cot_generation.
from .qa_evidence import EvidenceNode, LinkedEntity

# Kind priority for the anchor cap: exact label > exact code > bare id.
_KIND_PRIORITY = {"label": 0, "code": 1, "id": 2}

# A numeric/code prefix immediately before a known type name, e.g. "99号" in
# "99号喷漆室" — signals a specific instance the user expects to exist.
_NUMERIC_PREFIX_RE = re.compile(r"[0-9A-Za-z][0-9A-Za-z\-]*号?$")


@dataclass
class SchemaHint:
    type_name: str
    node_id: str


@dataclass
class LinkResult:
    anchors: list[LinkedEntity] = field(default_factory=list)
    unlinked_mentions: list[str] = field(default_factory=list)
    schema_hints: list[SchemaHint] = field(default_factory=list)


def resolve_entities(
    *,
    question: str,
    instance_graph: Any,
    schema_graph: Any,
    max_anchors: int = 6,
) -> LinkResult:
    inst_nodes = list(getattr(instance_graph, "nodes", []))
    schema_nodes = list(getattr(schema_graph, "nodes", []))

    # key string -> list of (node, kind) for instance nodes.
    inst_keys: dict[str, list[tuple[Any, str]]] = {}
    for node in inst_nodes:
        for key, kind in _node_keys(node):
            inst_keys.setdefault(key, []).append((node, kind))
    # schema type name -> schema node.
    schema_keys: dict[str, Any] = {}
    for node in schema_nodes:
        if len(str(node.label)) >= 2:
            schema_keys.setdefault(str(node.label), node)

    # Collect all occurrences, then keep maximal non-overlapping spans.
    occurrences: list[tuple[int, int, str]] = []
    for key in set(inst_keys) | set(schema_keys):
        for start, end in _find_spans(question, key):
            occurrences.append((start, end, key))
    occurrences.sort(key=lambda occ: (-(occ[1] - occ[0]), occ[0]))

    covered = [False] * len(question)
    selected: list[tuple[int, int, str]] = []
    for start, end, key in occurrences:
        if any(covered[start:end]):
            continue
        selected.append((start, end, key))
        for i in range(start, end):
            covered[i] = True
    selected.sort(key=lambda occ: occ[0])

    anchors: list[LinkedEntity] = []
    hints: list[SchemaHint] = []
    for start, end, key in selected:
        inst_matches = inst_keys.get(key)
        if inst_matches:
            anchors.extend(_build_anchors(key, inst_matches))
        elif key in schema_keys:
            node = schema_keys[key]
            hints.append(SchemaHint(type_name=str(node.label), node_id=str(node.id)))

    anchors = _cap_anchors(anchors, inst_keys, max_anchors)
    anchor_spans = [
        (start, end) for start, end, key in selected if key in inst_keys
    ]
    unlinked = _detect_unlinked(question, anchor_spans, list(schema_keys))
    return LinkResult(anchors=anchors, unlinked_mentions=unlinked, schema_hints=hints)


def _node_keys(node: Any) -> list[tuple[str, str]]:
    keys: list[tuple[str, str]] = []
    label = str(getattr(node, "label", "") or "")
    node_type = str(getattr(node, "type", "") or "")
    # Skip the label key for degenerate type-proxy nodes (instanceName equals the
    # object type, e.g. a node literally named "生产工单"); otherwise a bare type
    # word in the question would link this generic node as an anchor.
    if len(label) >= 2 and label != node_type:
        keys.append((label, "label"))
    attrs = getattr(node, "attrs", {}) or {}
    for key, value in attrs.items():
        key_text = str(key)
        if key_text in {"实例编码", "对象编码"} or key_text.casefold().endswith("code"):
            if value not in (None, ""):
                keys.append((str(value), "code"))
    node_id = str(getattr(node, "id", "") or "")
    if node_id:
        keys.append((node_id, "id"))
    return keys


def _build_anchors(key: str, matches: list[tuple[Any, str]]) -> list[LinkedEntity]:
    # Deduplicate by node id, keep a deterministic order, cap ambiguity at 3.
    seen: dict[str, tuple[Any, str]] = {}
    for node, kind in matches:
        node_id = str(node.id)
        if node_id not in seen or _KIND_PRIORITY[kind] < _KIND_PRIORITY[seen[node_id][1]]:
            seen[node_id] = (node, kind)
    ordered = sorted(seen.items(), key=lambda item: item[0])[:3]
    total = len(ordered)
    anchors: list[LinkedEntity] = []
    for rank, (_node_id, (node, _kind)) in enumerate(ordered, start=1):
        anchors.append(
            LinkedEntity(
                mention=key,
                node=_evidence_node(node, tier=1),
                ambiguous_rank=(rank, total) if total > 1 else None,
            )
        )
    return anchors


def _cap_anchors(
    anchors: list[LinkedEntity],
    inst_keys: dict[str, list[tuple[Any, str]]],
    max_anchors: int,
) -> list[LinkedEntity]:
    if len(anchors) <= max_anchors:
        return anchors

    def priority(entity: LinkedEntity) -> tuple[int, int]:
        kinds = [kind for _n, kind in inst_keys.get(entity.mention, [])]
        best = min((_KIND_PRIORITY[k] for k in kinds), default=2)
        return (best, -len(entity.mention))

    kept = sorted(anchors, key=priority)[:max_anchors]
    # Preserve question order among the survivors.
    order = {id(entity): index for index, entity in enumerate(anchors)}
    return sorted(kept, key=lambda entity: order[id(entity)])


def _evidence_node(node: Any, *, tier: int) -> EvidenceNode:
    return EvidenceNode(
        id=str(node.id),
        label=str(node.label),
        type=str(node.type),
        attrs=dict(getattr(node, "attrs", {}) or {}),
        tier=tier,
    )


def _find_spans(text: str, key: str) -> list[tuple[int, int]]:
    if not key:
        return []
    if key.isdigit() and len(key) < 4:
        return []
    needs_boundary = _needs_boundary(key)
    spans: list[tuple[int, int]] = []
    start = 0
    while True:
        idx = text.find(key, start)
        if idx < 0:
            break
        end = idx + len(key)
        if not needs_boundary or _boundary_ok(text, idx, end):
            spans.append((idx, end))
        start = idx + 1
    return spans


def _needs_boundary(key: str) -> bool:
    # Codes / ids are ascii and must match on token boundaries; CJK labels do not.
    return all(ch.isascii() for ch in key)


def _boundary_ok(text: str, start: int, end: int) -> bool:
    left_ok = start == 0 or not _is_word_char(text[start - 1])
    right_ok = end >= len(text) or not _is_word_char(text[end])
    return left_ok and right_ok


def _is_word_char(ch: str) -> bool:
    return ch.isascii() and (ch.isalnum() or ch == "_")


def _detect_unlinked(
    question: str, anchor_spans: list[tuple[int, int]], schema_type_names: list[str]
) -> list[str]:
    """Flag "{number}{号?}{known type}" fragments not covered by any anchor.

    Conservative: only fires when a numeric/code prefix sits directly before a
    known type name (a specific instance the user expects), never on plain words.
    """
    unlinked: list[str] = []
    for type_name in schema_type_names:
        if len(type_name) < 2:
            continue
        for match in re.finditer(re.escape(type_name), question):
            prefix = _NUMERIC_PREFIX_RE.search(question[: match.start()])
            if not prefix:
                continue
            start, end = prefix.start(), match.end()
            if any(start < a_end and a_start < end for a_start, a_end in anchor_spans):
                continue
            mention = question[start:end]
            if mention not in unlinked:
                unlinked.append(mention)
    return unlinked
