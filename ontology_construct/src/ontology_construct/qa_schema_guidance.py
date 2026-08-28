from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SchemaGuidance:
    related_types: frozenset[str]
    aligned_triples: frozenset[tuple[str, str, str]]


EMPTY_SCHEMA_GUIDANCE = SchemaGuidance(
    related_types=frozenset(),
    aligned_triples=frozenset(),
)


def build_schema_guidance(
    schema_graph: Any | None,
    *,
    anchor_types: set[str] | frozenset[str],
    enabled: bool = True,
) -> SchemaGuidance:
    if not enabled or schema_graph is None:
        return EMPTY_SCHEMA_GUIDANCE

    schema_nodes = list(getattr(schema_graph, "nodes", []) or [])
    schema_edges = list(getattr(schema_graph, "edges", []) or [])
    if not schema_nodes or not schema_edges:
        return EMPTY_SCHEMA_GUIDANCE

    label_by_id = {str(node.id): str(node.label) for node in schema_nodes}
    anchor_type_set = {str(type_name) for type_name in anchor_types if type_name}
    related_types = set(anchor_type_set)
    aligned_triples: set[tuple[str, str, str]] = set()

    for edge in schema_edges:
        source_type = label_by_id.get(str(edge.source))
        target_type = label_by_id.get(str(edge.target))
        rel = str(edge.rel)
        if not source_type or not target_type or not rel:
            continue
        aligned_triples.add((source_type, rel, target_type))
        if source_type in anchor_type_set:
            related_types.add(target_type)
        if target_type in anchor_type_set:
            related_types.add(source_type)

    return SchemaGuidance(
        related_types=frozenset(related_types),
        aligned_triples=frozenset(aligned_triples),
    )


def edge_is_schema_aligned(
    source_type: str,
    rel: str,
    target_type: str,
    guidance: SchemaGuidance,
    *,
    allow_reverse: bool = False,
) -> bool:
    triple = (str(source_type), str(rel), str(target_type))
    if triple in guidance.aligned_triples:
        return True
    if allow_reverse and (triple[2], triple[1], triple[0]) in guidance.aligned_triples:
        return True
    return False
