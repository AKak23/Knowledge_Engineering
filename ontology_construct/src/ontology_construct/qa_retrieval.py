from __future__ import annotations

import math
import re
from collections import Counter, deque
from typing import Any, Literal

from pydantic import BaseModel, Field

# Relative import so this module is byte-identical when vendored into cot_generation.
from .qa_evidence import EvidenceEdge, EvidenceNode
from .qa_schema_guidance import (
    build_schema_guidance,
    edge_is_schema_aligned,
)

QueryIntent = Literal["upstream", "downstream", "path", "attribute", "relation", "general"]


class RetrievedNode(BaseModel):
    id: str
    label: str
    type: str
    attrs: dict[str, Any] = Field(default_factory=dict)
    score: float = 0.0
    reasons: list[str] = Field(default_factory=list)


class RetrievedEdge(BaseModel):
    source: str
    target: str
    rel: str
    score: float = 0.0
    reasons: list[str] = Field(default_factory=list)


class RetrievalResult(BaseModel):
    intent: QueryIntent = "general"
    seeds: list[RetrievedNode] = Field(default_factory=list)
    relevant_schema_nodes: list[RetrievedNode] = Field(default_factory=list)
    relevant_schema_edges: list[RetrievedEdge] = Field(default_factory=list)
    evidence_nodes: list[RetrievedNode] = Field(default_factory=list)
    evidence_edges: list[RetrievedEdge] = Field(default_factory=list)
    evidence_paths: list[list[str]] = Field(default_factory=list)
    trace: list[str] = Field(default_factory=list)
    budget: dict[str, Any] = Field(default_factory=dict)


_TOKEN_RE = re.compile(r"[\w\u4e00-\u9fff]+")
_UPSTREAM_TERMS = ("上游", "来源", "前置", "输入", "依赖", "谁导致", "谁影响")
_DOWNSTREAM_TERMS = ("下游", "后续", "影响", "导致", "输出", "流转", "取消", "失效")
_PATH_TERMS = ("路径", "链路", "如何到达", "怎么到", "经过")
_ATTRIBUTE_TERMS = ("状态", "属性", "多少", "数量", "时间", "什么时候", "什么状态")
_RELATION_TERMS = ("关系", "关联", "连接", "属于", "包含", "执行", "处理")


def retrieve_graph_rag_context(
    *,
    question: str,
    schema_graph: Any,
    instance_graph: Any,
    qa_config: Any,
) -> RetrievalResult:
    intent = detect_intent(question)
    working_graph = instance_graph if getattr(instance_graph, "nodes", []) else schema_graph
    schema_nodes = list(getattr(schema_graph, "nodes", []))
    schema_edges = list(getattr(schema_graph, "edges", []))
    graph_nodes = list(getattr(working_graph, "nodes", []))
    graph_edges = list(getattr(working_graph, "edges", []))

    seed_scores = _seed_scores(graph_nodes, graph_edges, question)
    schema_seed_scores = _seed_scores(schema_nodes, schema_edges, question)
    if not seed_scores and schema_seed_scores and working_graph is not schema_graph:
        schema_types = {_node_by_id(schema_nodes)[node_id].label for node_id in schema_seed_scores}
        for node in graph_nodes:
            if node.type in schema_types or node.label in schema_types:
                _add_score(seed_scores, node.id, 0.55, "schema_type_seed")

    schema_result = _select_schema(
        schema_nodes=schema_nodes,
        schema_edges=schema_edges,
        graph_nodes=graph_nodes,
        seed_scores=seed_scores,
        schema_seed_scores=schema_seed_scores,
        max_items=int(qa_config.max_schema_items),
    )
    allowed_types = {node.label for node in schema_result[0]} | {node.type for node in schema_result[0]}
    allowed_rels = {edge.rel for edge in schema_result[1]}

    ranked_nodes = _rank_nodes(
        nodes=graph_nodes,
        edges=graph_edges,
        seed_scores=seed_scores,
        allowed_types=allowed_types,
        allowed_rels=allowed_rels,
        intent=intent,
        qa_config=qa_config,
    )
    paths = _seed_paths(graph_edges, list(seed_scores), intent, int(qa_config.max_paths))
    path_node_ids = {node_id for path in paths for node_id in path}
    for node_id in path_node_ids:
        if node_id in ranked_nodes:
            ranked_nodes[node_id].score += 0.25
            _append_reason(ranked_nodes[node_id].reasons, "path")

    selected_nodes = _select_nodes(ranked_nodes, seed_scores, int(qa_config.max_evidence_nodes))
    selected_ids = {node.id for node in selected_nodes}
    selected_edges = _select_edges(
        edges=graph_edges,
        node_scores={node.id: node.score for node in selected_nodes},
        selected_ids=selected_ids,
        seed_ids=set(seed_scores),
        allowed_rels=allowed_rels,
        path_edges=_path_edge_keys(paths),
        intent=intent,
        max_edges=int(qa_config.max_evidence_edges),
    )

    seeds = [
        ranked_nodes[node_id]
        for node_id in seed_scores
        if node_id in ranked_nodes
    ][: max(1, min(len(seed_scores), int(qa_config.max_evidence_nodes)))]
    trace = [
        f"intent={intent}",
        f"seed_count={len(seed_scores)}",
        f"schema_items={len(schema_result[0]) + len(schema_result[1])}",
        f"evidence_nodes={len(selected_nodes)}",
        f"evidence_edges={len(selected_edges)}",
    ]
    if not seed_scores:
        trace.append("fallback=top_ranked_budgeted_context")

    return RetrievalResult(
        intent=intent,
        seeds=seeds,
        relevant_schema_nodes=[_as_retrieved_node(node, 1.0, ["schema"]) for node in schema_result[0]],
        relevant_schema_edges=[_as_retrieved_edge(edge, 1.0, ["schema"]) for edge in schema_result[1]],
        evidence_nodes=selected_nodes,
        evidence_edges=selected_edges,
        evidence_paths=paths,
        trace=trace,
        budget={
            "max_prompt_chars": int(qa_config.max_prompt_chars),
            "max_schema_items": int(qa_config.max_schema_items),
            "max_evidence_nodes": int(qa_config.max_evidence_nodes),
            "max_evidence_edges": int(qa_config.max_evidence_edges),
            "max_paths": int(qa_config.max_paths),
        },
    )


def detect_intent(question: str) -> QueryIntent:
    text = question.casefold()
    if any(term in text for term in _UPSTREAM_TERMS):
        return "upstream"
    if any(term in text for term in _DOWNSTREAM_TERMS):
        return "downstream"
    if any(term in text for term in _PATH_TERMS):
        return "path"
    if any(term in text for term in _ATTRIBUTE_TERMS):
        return "attribute"
    if any(term in text for term in _RELATION_TERMS):
        return "relation"
    return "general"


def _seed_scores(nodes: list[Any], edges: list[Any], question: str) -> dict[str, tuple[float, list[str]]]:
    normalized = question.casefold()
    terms = _terms(question)
    relation_terms = _matched_relation_terms(edges, terms, normalized)
    scores: dict[str, tuple[float, list[str]]] = {}
    for node in nodes:
        fields = _node_fields(node)
        field_text = " ".join(fields).casefold()
        if node.id.casefold() in normalized:
            _add_score(scores, node.id, 1.0, "exact_id")
        if node.label and node.label.casefold() in normalized:
            _add_score(scores, node.id, 0.95, "exact_label")
        for key, value in node.attrs.items():
            if value in (None, ""):
                continue
            key_text = str(key)
            value_text = str(value)
            if value_text.casefold() in normalized and (
                key_text in {"实例编码", "对象编码"} or key_text.casefold().endswith("code")
            ):
                _add_score(scores, node.id, 0.92, "exact_code")
            elif len(value_text) >= 2 and value_text.casefold() in normalized:
                _add_score(scores, node.id, 0.7, "attribute_value")
        if node.type and node.type.casefold() in normalized:
            _add_score(scores, node.id, 0.62, "type_match")
        overlap = sum(1 for term in terms if term in field_text)
        if overlap:
            _add_score(scores, node.id, min(0.5, 0.16 * overlap), "lexical")

    if relation_terms:
        for edge in edges:
            if any(term in edge.rel.casefold() for term in relation_terms):
                _add_score(scores, edge.source, 0.35, "relation_source")
                _add_score(scores, edge.target, 0.35, "relation_target")
    return scores


def _rank_nodes(
    *,
    nodes: list[Any],
    edges: list[Any],
    seed_scores: dict[str, tuple[float, list[str]]],
    allowed_types: set[str],
    allowed_rels: set[str],
    intent: QueryIntent,
    qa_config: Any,
) -> dict[str, RetrievedNode]:
    ppr = _personalized_pagerank(
        node_ids=[node.id for node in nodes],
        edges=edges,
        seed_weights={node_id: score for node_id, (score, _reasons) in seed_scores.items()},
        alpha=float(qa_config.ppr_alpha),
        iterations=int(qa_config.ppr_iterations),
        directed=intent in {"upstream", "downstream", "path"},
        reverse=intent == "upstream",
    )
    degree = _degree(edges)
    ranked: dict[str, RetrievedNode] = {}
    fallback_score = 0.01
    for index, node in enumerate(nodes):
        seed_score, seed_reasons = seed_scores.get(node.id, (0.0, []))
        schema_bonus = 0.18 if node.type in allowed_types or node.label in allowed_types else 0.0
        graph_score = ppr.get(node.id, 0.0)
        hub_penalty = 0.0
        if bool(qa_config.hub_penalty_enabled):
            hub_penalty = min(0.15, math.log1p(degree.get(node.id, 0)) / 50)
        score = seed_score + graph_score + schema_bonus + fallback_score / (index + 1) - hub_penalty
        reasons = list(seed_reasons)
        if graph_score > 0:
            reasons.append("ppr")
        if schema_bonus:
            reasons.append("schema_gate")
        if hub_penalty:
            reasons.append("hub_penalty")
        if not reasons:
            reasons.append("fallback_rank")
        ranked[node.id] = _as_retrieved_node(node, score, reasons)

    for edge in edges:
        if edge.rel not in allowed_rels and not (
            edge.source in seed_scores or edge.target in seed_scores
        ):
            continue
        if intent == "upstream":
            source, target = edge.target, edge.source
        else:
            source, target = edge.source, edge.target
        if source in seed_scores and target in ranked:
            ranked[target].score += 0.28
            _append_reason(ranked[target].reasons, f"{intent}_neighbor")
    return ranked


def _select_schema(
    *,
    schema_nodes: list[Any],
    schema_edges: list[Any],
    graph_nodes: list[Any],
    seed_scores: dict[str, tuple[float, list[str]]],
    schema_seed_scores: dict[str, tuple[float, list[str]]],
    max_items: int,
) -> tuple[list[Any], list[Any]]:
    schema_by_label = {node.label: node for node in schema_nodes}
    graph_by_id = _node_by_id(graph_nodes)
    selected_node_ids = set(schema_seed_scores)
    for node_id in seed_scores:
        node = graph_by_id.get(node_id)
        if node and node.type in schema_by_label:
            selected_node_ids.add(schema_by_label[node.type].id)

    selected_edges: list[Any] = []
    if selected_node_ids:
        for edge in schema_edges:
            if edge.source in selected_node_ids or edge.target in selected_node_ids:
                selected_edges.append(edge)
                selected_node_ids.add(edge.source)
                selected_node_ids.add(edge.target)
    else:
        selected_edges = schema_edges[: max_items // 2]
        for edge in selected_edges:
            selected_node_ids.add(edge.source)
            selected_node_ids.add(edge.target)

    schema_by_id = _node_by_id(schema_nodes)
    selected_nodes = [schema_by_id[node_id] for node_id in selected_node_ids if node_id in schema_by_id]
    item_budget = max_items
    return selected_nodes[:item_budget], selected_edges[: max(0, item_budget - min(len(selected_nodes), item_budget))]


def _select_nodes(
    ranked_nodes: dict[str, RetrievedNode],
    seed_scores: dict[str, tuple[float, list[str]]],
    max_nodes: int,
) -> list[RetrievedNode]:
    ordered = sorted(ranked_nodes.values(), key=lambda node: (-node.score, node.id))
    seeds = [ranked_nodes[node_id] for node_id in seed_scores if node_id in ranked_nodes]
    selected: list[RetrievedNode] = []
    seen: set[str] = set()
    for node in [*seeds, *ordered]:
        if node.id in seen:
            continue
        selected.append(node)
        seen.add(node.id)
        if len(selected) >= max_nodes:
            break
    return selected


def _select_edges(
    *,
    edges: list[Any],
    node_scores: dict[str, float],
    selected_ids: set[str],
    seed_ids: set[str],
    allowed_rels: set[str],
    path_edges: set[tuple[str, str]],
    intent: QueryIntent,
    max_edges: int,
) -> list[RetrievedEdge]:
    candidates: list[RetrievedEdge] = []
    for edge in edges:
        if edge.source not in selected_ids or edge.target not in selected_ids:
            continue
        reasons: list[str] = []
        score = (node_scores.get(edge.source, 0.0) + node_scores.get(edge.target, 0.0)) / 2
        if edge.rel in allowed_rels:
            score += 0.2
            reasons.append("schema_relation")
        if edge.source in seed_ids or edge.target in seed_ids:
            score += 0.25
            reasons.append("seed_neighbor")
        if (edge.source, edge.target) in path_edges:
            score += 0.25
            reasons.append("path")
        if intent in {"upstream", "downstream"}:
            reasons.append(intent)
        if not reasons and not (edge.source in seed_ids or edge.target in seed_ids):
            continue
        candidates.append(_as_retrieved_edge(edge, score, reasons or ["selected"]))
    return sorted(candidates, key=lambda edge: (-edge.score, edge.source, edge.target))[:max_edges]


def _personalized_pagerank(
    *,
    node_ids: list[str],
    edges: list[Any],
    seed_weights: dict[str, float],
    alpha: float,
    iterations: int,
    directed: bool,
    reverse: bool,
) -> dict[str, float]:
    if not node_ids:
        return {}
    if not seed_weights:
        return {node_id: 1 / len(node_ids) for node_id in node_ids}
    total_seed = sum(seed_weights.values()) or 1.0
    restart = {node_id: seed_weights.get(node_id, 0.0) / total_seed for node_id in node_ids}
    rank = dict(restart)
    adjacency: dict[str, list[str]] = {node_id: [] for node_id in node_ids}
    for edge in edges:
        source, target = (edge.target, edge.source) if reverse else (edge.source, edge.target)
        adjacency.setdefault(source, []).append(target)
        if not directed:
            adjacency.setdefault(target, []).append(source)
    for _ in range(iterations):
        next_rank = {node_id: (1 - alpha) * restart.get(node_id, 0.0) for node_id in node_ids}
        for node_id, value in rank.items():
            targets = adjacency.get(node_id) or []
            if not targets:
                continue
            share = alpha * value / len(targets)
            for target in targets:
                next_rank[target] = next_rank.get(target, 0.0) + share
        rank = next_rank
    return rank


def _seed_paths(edges: list[Any], seed_ids: list[str], intent: QueryIntent, max_paths: int) -> list[list[str]]:
    if max_paths <= 0 or len(seed_ids) < 2:
        return []
    paths: list[list[str]] = []
    for index, source in enumerate(seed_ids):
        for target in seed_ids[index + 1 :]:
            path = _shortest_path(edges, source, target, directed=intent in {"upstream", "downstream", "path"})
            if path:
                paths.append(path)
                if len(paths) >= max_paths:
                    return paths
    return paths


def _shortest_path(edges: list[Any], source: str, target: str, *, directed: bool, max_depth: int = 4) -> list[str]:
    adjacency: dict[str, list[str]] = {}
    for edge in edges:
        adjacency.setdefault(edge.source, []).append(edge.target)
        if not directed:
            adjacency.setdefault(edge.target, []).append(edge.source)
    queue: deque[tuple[str, list[str]]] = deque([(source, [source])])
    seen = {source}
    while queue:
        node_id, path = queue.popleft()
        if len(path) > max_depth + 1:
            continue
        for next_id in adjacency.get(node_id, []):
            if next_id in seen:
                continue
            next_path = [*path, next_id]
            if next_id == target:
                return next_path
            seen.add(next_id)
            queue.append((next_id, next_path))
    return []


def _path_edge_keys(paths: list[list[str]]) -> set[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()
    for path in paths:
        for source, target in zip(path, path[1:]):
            keys.add((source, target))
    return keys


def _matched_relation_terms(edges: list[Any], terms: list[str], normalized_question: str) -> set[str]:
    rel_texts = {edge.rel.casefold() for edge in edges if edge.rel}
    matched = {term for term in terms if any(term in rel for rel in rel_texts)}
    for rel in rel_texts:
        if rel and rel in normalized_question:
            matched.add(rel)
    return matched


def _terms(text: str) -> list[str]:
    return [
        term.casefold()
        for term in _TOKEN_RE.findall(text)
        if len(term.strip()) >= 2
    ]


def _node_fields(node: Any) -> list[str]:
    return [
        str(node.id),
        str(node.label),
        str(node.type),
        *[str(value) for value in node.attrs.values() if value is not None],
    ]


def _node_by_id(nodes: list[Any]) -> dict[str, Any]:
    return {node.id: node for node in nodes}


def _degree(edges: list[Any]) -> dict[str, int]:
    degree: Counter[str] = Counter()
    for edge in edges:
        degree[edge.source] += 1
        degree[edge.target] += 1
    return dict(degree)


def _add_score(scores: dict[str, tuple[float, list[str]]], node_id: str, value: float, reason: str) -> None:
    current, reasons = scores.get(node_id, (0.0, []))
    if reason not in reasons:
        reasons.append(reason)
    scores[node_id] = (current + value, reasons)


def _append_reason(reasons: list[str], reason: str) -> None:
    if reason not in reasons:
        reasons.append(reason)


def _as_retrieved_node(node: Any, score: float, reasons: list[str]) -> RetrievedNode:
    return RetrievedNode(
        id=str(node.id),
        label=str(node.label),
        type=str(node.type),
        attrs=dict(node.attrs),
        score=round(float(score), 6),
        reasons=list(dict.fromkeys(reasons)),
    )


def _as_retrieved_edge(edge: Any, score: float, reasons: list[str]) -> RetrievedEdge:
    return RetrievedEdge(
        source=str(edge.source),
        target=str(edge.target),
        rel=str(edge.rel),
        score=round(float(score), 6),
        reasons=list(dict.fromkeys(reasons)),
    )


# --------------------------------------------------------------------------- #
# graph_qa_evidence_v1 recall layer (§8)                                       #
#                                                                              #
# Fills only the SUPPORTING region. Seeded strictly on the resolved anchors,   #
# so the old seed-explosion path (weak type/attr lexical matches) cannot occur #
# here by construction: the three denoising rules (§8) are satisfied because   #
# this recall does no lexical attribute/type scoring at all.                   #
# --------------------------------------------------------------------------- #


def retrieve_supporting_candidates(
    *,
    anchor_ids: list[str],
    instance_graph: Any,
    schema_graph: Any | None = None,
    qa_config: Any,
) -> tuple[list[EvidenceNode], list[EvidenceEdge]]:
    nodes = list(getattr(instance_graph, "nodes", []))
    edges = list(getattr(instance_graph, "edges", []))
    node_by_id = {str(node.id): node for node in nodes}
    anchors = [a for a in anchor_ids if a in node_by_id]
    if not anchors:
        return [], []

    node_limit = int(getattr(qa_config, "supporting_node_limit", 60))
    edge_limit = int(getattr(qa_config, "supporting_edge_limit", 80))
    type_bonus = float(getattr(qa_config, "schema_type_bonus", 0.18))
    relation_bonus = float(getattr(qa_config, "schema_relation_bonus", 0.22))
    schema_guidance = build_schema_guidance(
        schema_graph,
        anchor_types={str(node_by_id[anchor].type) for anchor in anchors},
        enabled=bool(getattr(qa_config, "schema_guided_pruning", True)),
    )

    ppr = _personalized_pagerank(
        node_ids=[str(node.id) for node in nodes],
        edges=edges,
        seed_weights={anchor: 1.0 for anchor in anchors},
        alpha=float(getattr(qa_config, "ppr_alpha", 0.85)),
        iterations=int(getattr(qa_config, "ppr_iterations", 30)),
        directed=False,
        reverse=False,
    )
    aligned_edge_keys: set[tuple[str, str, str]] = set()
    aligned_incident: set[str] = set()
    for edge in edges:
        src, tgt, rel = str(edge.source), str(edge.target), str(edge.rel)
        if _instance_edge_schema_aligned(src, tgt, rel, node_by_id, schema_guidance):
            aligned_edge_keys.add((src, tgt, rel))
            aligned_incident.add(src)
            aligned_incident.add(tgt)

    weighted_scores: dict[str, float] = {}
    node_reasons: dict[str, list[str]] = {}
    for node_id, score in ppr.items():
        weighted = float(score)
        reasons = ["ppr"]
        node = node_by_id.get(node_id)
        if node is not None and str(node.type) in schema_guidance.related_types:
            weighted += type_bonus
            reasons.append("schema_type")
        if node_id in aligned_incident:
            weighted += relation_bonus / 2
            reasons.append("schema_relation")
        weighted_scores[node_id] = weighted
        node_reasons[node_id] = reasons

    anchor_set = set(anchors)
    ranked = sorted(
        (nid for nid in ppr if nid not in anchor_set and nid in node_by_id),
        key=lambda nid: (-weighted_scores.get(nid, 0.0), nid),
    )[:node_limit]
    ranked_set = set(ranked)

    supporting_nodes = [
        EvidenceNode(
            id=str(node_by_id[nid].id),
            label=str(node_by_id[nid].label),
            type=str(node_by_id[nid].type),
            attrs=dict(getattr(node_by_id[nid], "attrs", {}) or {}),
            tier=3,
            score=round(float(weighted_scores.get(nid, 0.0)), 6),
            reasons=node_reasons.get(nid, ["ppr"]),
        )
        for nid in ranked
    ]

    visible = anchor_set | ranked_set
    edge_candidates: list[tuple[bool, float, EvidenceEdge]] = []
    for edge in edges:
        src, tgt = str(edge.source), str(edge.target)
        if src in visible and tgt in visible:
            rel = str(edge.rel)
            aligned = (src, tgt, rel) in aligned_edge_keys
            score = (
                weighted_scores.get(src, 0.0) + weighted_scores.get(tgt, 0.0)
            ) / 2
            if aligned:
                score += relation_bonus
            edge_candidates.append(
                (
                    aligned,
                    score,
                    EvidenceEdge(source=src, target=tgt, rel=rel, tier=3),
                )
            )
    supporting_edges = [
        edge
        for _aligned, _score, edge in sorted(
            edge_candidates,
            key=lambda item: (
                0 if item[0] else 1,
                -item[1],
                item[2].source,
                item[2].target,
                item[2].rel,
            ),
        )[:edge_limit]
    ]
    return supporting_nodes, supporting_edges


def _instance_edge_schema_aligned(
    source_id: str,
    target_id: str,
    rel: str,
    node_by_id: dict[str, Any],
    schema_guidance: Any,
) -> bool:
    source = node_by_id.get(source_id)
    target = node_by_id.get(target_id)
    if source is None or target is None:
        return False
    return edge_is_schema_aligned(
        str(source.type),
        rel,
        str(target.type),
        schema_guidance,
    )
