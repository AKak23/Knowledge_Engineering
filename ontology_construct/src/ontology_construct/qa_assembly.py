"""Guarantee layer (§7): assemble a QAEvidence enforcing invariants I1–I6.

The recall layer decides *which* supporting candidates exist; this layer decides
what MUST be present regardless of recall — anchors, connecting paths, one-hop
neighbourhoods, fan-out aggregates and edge-endpoint closure.
"""

from __future__ import annotations

from collections import defaultdict, deque
from typing import Any

# Relative imports so this module is byte-identical when vendored into cot_generation.
from .qa_evidence import (
    ChainHop,
    CoreEvidenceItem,
    EvidenceEdge,
    EvidenceNode,
    LinkedEntity,
    QAEvidence,
    QueryPlan,
)
from .qa_planner import PlanResult
from .qa_schema_guidance import (
    EMPTY_SCHEMA_GUIDANCE,
    SchemaGuidance,
    build_schema_guidance,
    edge_is_schema_aligned,
)
from .qa_semantics import SemanticPolicy, build_impact_digraph

# Group sizes at/below this are shown as individual RelationSet edges; larger
# groups are rolled up into a counted fact line (I3).
_ROLLUP_MIN = 3


def assemble_evidence(
    *,
    question: str,
    plan_result: PlanResult,
    instance_graph: Any,
    schema_graph: Any,
    policy: SemanticPolicy,
    cfg: Any,
    supporting_nodes: list[EvidenceNode] | None = None,
    supporting_edges: list[EvidenceEdge] | None = None,
) -> QAEvidence:
    plan = plan_result.plan
    nodes = list(getattr(instance_graph, "nodes", []))
    edges = list(getattr(instance_graph, "edges", []))
    node_by_id = {str(node.id): node for node in nodes}
    raw_out = {(str(e.source), str(e.target)): str(e.rel) for e in edges}

    anchors = _distinct_anchors(plan.linked_entities)
    schema_guidance = build_schema_guidance(
        schema_graph,
        anchor_types={anchor.node.type for anchor in anchors},
        enabled=bool(getattr(cfg, "schema_guided_pruning", True)),
    )
    core: list[CoreEvidenceItem] = []

    # I1 — anchors always present.
    core.extend(_anchor_items(anchors, plan, node_by_id))

    # I2 — connecting paths (connectivity mode).
    if plan.mode == "connectivity":
        core.extend(
            _path_items(
                anchors,
                edges,
                raw_out,
                node_by_id,
                policy,
                plan,
                cfg,
                schema_guidance,
            )
        )

    # I4 — single-anchor fan-out aggregate (impact / trace intent).
    if plan.intent in {"impact", "trace"} and len(anchors) == 1:
        core.extend(
            _fanout_items(
                anchors[0],
                edges,
                raw_out,
                node_by_id,
                policy,
                plan,
                cfg,
                schema_guidance,
            )
        )

    # I3 — one-hop neighbourhood per anchor.
    core.extend(_relation_items(anchors, edges, node_by_id, plan, cfg, schema_guidance))

    # Replace intent — same-type alternatives to the anchor (§4.3).
    if plan.intent == "replace":
        core.extend(_same_type_items(anchors, nodes, edges, cfg))

    # I5 — structural cap on chains.
    core = _cap_chains(core, int(getattr(cfg, "max_chains", 8)))

    # Drop supporting candidates already shown in CORE (avoid duplicate lines /
    # wasted budget); tier-0 closure is re-added afterwards by _close_edge_endpoints.
    core_nodes = _core_node_ids(core)
    core_edges = {
        (e.source, e.target, e.rel) for item in core for e in item.edges
    }
    support_nodes = [
        node for node in (supporting_nodes or []) if node.id not in core_nodes
    ]
    support_edges = [
        edge
        for edge in (supporting_edges or [])
        if (edge.source, edge.target, edge.rel) not in core_edges
    ]
    schema_nodes, schema_edges = _supporting_schema(anchors, plan_result, schema_graph)

    evidence = QAEvidence(
        question=question,
        plan=plan,
        core=core,
        supporting_schema_nodes=schema_nodes,
        supporting_schema_edges=schema_edges,
        supporting_nodes=support_nodes,
        supporting_edges=support_edges,
        meta={
            "mode": plan.mode,
            "intent": plan.intent,
            "anchor_ids": [a.node.id for a in anchors],
            "core_item_count": len(core),
        },
    )

    # I6 — edge endpoint closure.
    _close_edge_endpoints(evidence, node_by_id)
    return evidence


# --------------------------------------------------------------------------- #
# I1 anchors                                                                   #
# --------------------------------------------------------------------------- #


def _distinct_anchors(entities: list[LinkedEntity]) -> list[LinkedEntity]:
    seen: set[str] = set()
    out: list[LinkedEntity] = []
    for entity in entities:
        if entity.node.id in seen:
            continue
        seen.add(entity.node.id)
        out.append(entity)
    return out


def _anchor_items(
    anchors: list[LinkedEntity], plan: QueryPlan, node_by_id: dict[str, Any]
) -> list[CoreEvidenceItem]:
    items: list[CoreEvidenceItem] = []
    for entity in anchors:
        node = entity.node
        if plan.intent == "attribute":
            facts = [f"{k}={v}" for k, v in node.attrs.items() if v not in (None, "")]
            items.append(
                CoreEvidenceItem(
                    item_type="attribute_table",
                    title=f"AttributeTable({node.id})",
                    facts=facts or ["（无可用属性）"],
                    nodes=[node],
                )
            )
        else:
            items.append(
                CoreEvidenceItem(
                    item_type="target_node", title="TargetNode", nodes=[node]
                )
            )
    return items


# --------------------------------------------------------------------------- #
# I2 paths                                                                     #
# --------------------------------------------------------------------------- #


def _path_items(
    anchors: list[LinkedEntity],
    edges: list[Any],
    raw_out: dict[tuple[str, str], str],
    node_by_id: dict[str, Any],
    policy: SemanticPolicy,
    plan: QueryPlan,
    cfg: Any,
    schema_guidance: SchemaGuidance,
) -> list[CoreEvidenceItem]:
    view = "trace" if plan.intent == "trace" else "impact"
    semantic = build_impact_digraph(edges, policy, view=view)
    raw_adj = _raw_adjacency(edges, directed=True)
    undirected = _raw_adjacency(edges, directed=False)
    max_depth = int(getattr(cfg, "path_max_depth", 6))
    per_pair = int(getattr(cfg, "max_paths_per_pair", 3))
    max_pairs = int(getattr(cfg, "max_anchor_pairs", 6))

    item_type, base_title = _chain_kind(plan.intent)
    items: list[CoreEvidenceItem] = []
    pair_count = 0
    for i in range(len(anchors)):
        for j in range(i + 1, len(anchors)):
            if pair_count >= max_pairs:
                return items
            pair_count += 1
            src, dst = anchors[i].node.id, anchors[j].node.id
            paths, path_type = _find_paths(
                src,
                dst,
                semantic,
                raw_adj,
                undirected,
                max_depth,
                per_pair,
                node_by_id,
                schema_guidance,
            )
            for path in paths:
                items.append(
                    _chain_item(
                        path, raw_out, node_by_id, item_type, base_title, path_type
                    )
                )
    return items


def _find_paths(
    src: str,
    dst: str,
    semantic: dict[str, list[tuple[str, str]]],
    raw_adj: dict[str, list[tuple[str, str]]],
    undirected: dict[str, list[tuple[str, str]]],
    max_depth: int,
    per_pair: int,
    node_by_id: dict[str, Any],
    schema_guidance: SchemaGuidance,
) -> tuple[list[list[str]], str]:
    for adjacency, path_type in (
        (semantic, "semantic"),
        (raw_adj, "directed"),
        (undirected, "undirected"),
    ):
        paths = _shortest_paths(
            adjacency,
            src,
            dst,
            max_depth,
            per_pair,
            node_by_id=node_by_id,
            schema_guidance=schema_guidance,
            allow_reverse=path_type in {"semantic", "undirected"},
        )
        if paths:
            return paths, path_type
    return [], "none"


def _shortest_paths(
    adjacency: dict[str, list[tuple[str, str]]],
    source: str,
    target: str,
    max_depth: int,
    max_paths: int,
    *,
    node_by_id: dict[str, Any] | None = None,
    schema_guidance: SchemaGuidance = EMPTY_SCHEMA_GUIDANCE,
    allow_reverse: bool = False,
) -> list[list[str]]:
    queue: deque[list[str]] = deque([[source]])
    found_len: int | None = None
    paths: list[list[str]] = []
    while queue:
        path = queue.popleft()
        if len(path) - 1 > max_depth:
            continue
        node = path[-1]
        if node == target and len(path) > 1:
            if found_len is None:
                found_len = len(path)
            if len(path) == found_len:
                paths.append(path)
                if len(paths) >= max_paths:
                    break
            continue
        if found_len is not None and len(path) >= found_len:
            continue
        neighbours = _ordered_neighbours(
            adjacency.get(node, []),
            node,
            node_by_id or {},
            schema_guidance,
            allow_reverse=allow_reverse,
        )
        for nxt in neighbours:
            if nxt in path:
                continue
            queue.append([*path, nxt])
    return paths


def _chain_item(
    path: list[str],
    raw_out: dict[tuple[str, str], str],
    node_by_id: dict[str, Any],
    item_type: str,
    base_title: str,
    path_type: str,
) -> CoreEvidenceItem:
    hops: list[ChainHop] = []
    item_edges: list[EvidenceEdge] = []
    for position, node_id in enumerate(path):
        ev_node = _ev_node(node_id, node_by_id, tier=1)
        if position == 0:
            hops.append(ChainHop(node=ev_node))
            continue
        prev = path[position - 1]
        rel, direction, edge = _edge_between(prev, node_id, raw_out)
        hops.append(ChainHop(node=ev_node, rel=rel, raw_direction=direction))
        if edge is not None:
            item_edges.append(edge)
    hops_n = len(path) - 1
    title = f"{base_title}(from={path[0]}, to={path[-1]}, hops={hops_n}, path={path_type})"
    note = _chain_note(item_type, path_type)
    return CoreEvidenceItem(
        item_type=item_type, title=title, hops=hops, edges=item_edges, note=note
    )


def _edge_between(
    prev: str, cur: str, raw_out: dict[tuple[str, str], str]
) -> tuple[str, str, EvidenceEdge | None]:
    if (prev, cur) in raw_out:
        rel = raw_out[(prev, cur)]
        return rel, "forward", EvidenceEdge(source=prev, target=cur, rel=rel, tier=1)
    if (cur, prev) in raw_out:
        rel = raw_out[(cur, prev)]
        return rel, "reverse", EvidenceEdge(source=cur, target=prev, rel=rel, tier=1)
    return "", "forward", None


def _ordered_neighbours(
    neighbours: list[tuple[str, str]],
    source_id: str,
    node_by_id: dict[str, Any],
    schema_guidance: SchemaGuidance,
    *,
    allow_reverse: bool,
) -> list[str]:
    best: dict[str, tuple[int, str]] = {}
    for target_id, rel in neighbours:
        priority = _schema_edge_priority(
            source_id,
            target_id,
            rel,
            node_by_id,
            schema_guidance,
            allow_reverse=allow_reverse,
        )
        current = best.get(target_id)
        if current is None or (priority, rel) < current:
            best[target_id] = (priority, rel)
    return [
        target_id
        for target_id, _value in sorted(
            best.items(), key=lambda item: (item[1][0], item[0], item[1][1])
        )
    ]


def _schema_edge_priority(
    source_id: str,
    target_id: str,
    rel: str,
    node_by_id: dict[str, Any],
    schema_guidance: SchemaGuidance,
    *,
    allow_reverse: bool,
) -> int:
    source = node_by_id.get(source_id)
    target = node_by_id.get(target_id)
    if source is None or target is None:
        return 1
    return 0 if edge_is_schema_aligned(
        str(source.type),
        rel,
        str(target.type),
        schema_guidance,
        allow_reverse=allow_reverse,
    ) else 1


def _relation_group_schema_aligned(
    anchor_type: str,
    rel: str,
    is_outgoing: bool,
    neighbour_type: str,
    schema_guidance: SchemaGuidance,
) -> bool:
    if is_outgoing:
        return edge_is_schema_aligned(anchor_type, rel, neighbour_type, schema_guidance)
    return edge_is_schema_aligned(neighbour_type, rel, anchor_type, schema_guidance)


def _chain_kind(intent: str) -> tuple[str, str]:
    if intent == "impact":
        return "impact_chain", "ImpactChain"
    if intent == "trace":
        return "trace_chain", "TraceChain"
    return "candidate_path", "CandidatePath"


def _chain_note(item_type: str, path_type: str) -> str:
    if item_type == "impact_chain":
        base = "列表顺序为影响传播方向；箭头表示图中原始边方向。"
    elif item_type == "trace_chain":
        base = "列表顺序为追溯方向（由结果指向来源）；箭头表示图中原始边方向。"
    else:
        base = "列表顺序为路径行进方向；箭头表示图中原始边方向。"
    if path_type == "undirected":
        base += " 原始图中未找到符合方向的路径，此为无向连通路径。"
    elif path_type == "directed":
        base += " 该路径来自原始有向边（未匹配传播语义）。"
    return base


# --------------------------------------------------------------------------- #
# I4 fan-out aggregate                                                         #
# --------------------------------------------------------------------------- #


def _fanout_items(
    anchor: LinkedEntity,
    edges: list[Any],
    raw_out: dict[tuple[str, str], str],
    node_by_id: dict[str, Any],
    policy: SemanticPolicy,
    plan: QueryPlan,
    cfg: Any,
    schema_guidance: SchemaGuidance,
) -> list[CoreEvidenceItem]:
    view = "trace" if plan.intent == "trace" else "impact"
    semantic = build_impact_digraph(edges, policy, view=view)
    depth = int(getattr(cfg, "impact_bfs_depth", 4))
    example_count = int(getattr(cfg, "aggregate_example_count", 5))
    max_full = int(getattr(cfg, "max_full_chains", 5))

    reached = _bfs_reachable(semantic, anchor.node.id, depth, node_by_id, schema_guidance)
    if not reached:
        return []

    by_type: dict[str, list[str]] = defaultdict(list)
    for node_id in reached:
        node = node_by_id.get(node_id)
        type_name = str(node.type) if node is not None else "未知类型"
        by_type[type_name].append(node_id)

    verb = "受影响" if plan.intent == "impact" else "追溯到"
    facts: list[str] = []
    for type_name in sorted(by_type):
        members = sorted(by_type[type_name])
        examples = ", ".join(members[:example_count])
        facts.append(f"{verb} {type_name} x{len(members)}（示例: {examples}）")

    item_type, base_title = _chain_kind(plan.intent)
    items = [
        CoreEvidenceItem(
            item_type="aggregate_summary",
            title=f"AggregateSummary(root={anchor.node.id}, view={view}, depth<={depth})",
            facts=facts,
        )
    ]
    # Full chains for the first few reached targets (I4).
    for node_id in sorted(reached)[:max_full]:
        path = _reconstruct_path(
            semantic,
            anchor.node.id,
            node_id,
            depth,
            node_by_id,
            schema_guidance,
        )
        if len(path) >= 2:
            items.append(
                _chain_item(path, raw_out, node_by_id, item_type, base_title, "semantic")
            )
    return items


def _bfs_reachable(
    adjacency: dict[str, list[tuple[str, str]]],
    source: str,
    depth: int,
    node_by_id: dict[str, Any],
    schema_guidance: SchemaGuidance,
) -> set[str]:
    reached: set[str] = set()
    queue: deque[tuple[str, int]] = deque([(source, 0)])
    seen = {source}
    while queue:
        node, dist = queue.popleft()
        if dist >= depth:
            continue
        for nxt in _ordered_neighbours(
            adjacency.get(node, []),
            node,
            node_by_id,
            schema_guidance,
            allow_reverse=True,
        ):
            if nxt in seen:
                continue
            seen.add(nxt)
            reached.add(nxt)
            queue.append((nxt, dist + 1))
    return reached


def _reconstruct_path(
    adjacency: dict[str, list[tuple[str, str]]],
    source: str,
    target: str,
    depth: int,
    node_by_id: dict[str, Any],
    schema_guidance: SchemaGuidance,
) -> list[str]:
    paths = _shortest_paths(
        adjacency,
        source,
        target,
        depth,
        1,
        node_by_id=node_by_id,
        schema_guidance=schema_guidance,
        allow_reverse=True,
    )
    return paths[0] if paths else []


# --------------------------------------------------------------------------- #
# I3 relation sets                                                             #
# --------------------------------------------------------------------------- #


def _relation_items(
    anchors: list[LinkedEntity],
    edges: list[Any],
    node_by_id: dict[str, Any],
    plan: QueryPlan,
    cfg: Any,
    schema_guidance: SchemaGuidance,
) -> list[CoreEvidenceItem]:
    if plan.intent == "relation":
        limit = int(getattr(cfg, "max_relation_lines_relation_intent", 24))
    else:
        limit = int(getattr(cfg, "max_relation_lines_per_anchor", 12))
    example_count = int(getattr(cfg, "aggregate_example_count", 5))

    items: list[CoreEvidenceItem] = []
    for entity in anchors:
        anchor_id = entity.node.id
        # (rel, is_outgoing, neighbour_type) -> list[(edge, neighbour_node)]
        groups: dict[tuple[str, bool, str], list[tuple[EvidenceEdge, EvidenceNode]]]
        groups = defaultdict(list)
        for edge in edges:
            src, tgt, rel = str(edge.source), str(edge.target), str(edge.rel)
            if src == anchor_id:
                neighbour_id, is_out = tgt, True
            elif tgt == anchor_id:
                neighbour_id, is_out = src, False
            else:
                continue
            neighbour = _ev_node(neighbour_id, node_by_id, tier=2)
            groups[(rel, is_out, neighbour.type)].append(
                (EvidenceEdge(source=src, target=tgt, rel=rel, tier=2), neighbour)
            )
        if not groups:
            continue

        item_edges: list[EvidenceEdge] = []
        neighbour_nodes: list[EvidenceNode] = []
        facts: list[str] = []
        # Explicit direct-degree rollup (raw edge orientation) so degree/neighbor
        # questions are answerable from one line instead of counting edge lines.
        in_ids = {m[1].id for key, ms in groups.items() if not key[1] for m in ms}
        out_ids = {m[1].id for key, ms in groups.items() if key[1] for m in ms}
        facts.append(f"直接度数: 上游(入边) {len(in_ids)} 个、下游(出边) {len(out_ids)} 个")
        shown = 0
        anchor_type = str(entity.node.type)
        for key in sorted(
            groups,
            key=lambda k: (
                0
                if _relation_group_schema_aligned(
                    anchor_type, k[0], k[1], k[2], schema_guidance
                )
                else 1,
                k[0],
                not k[1],
                k[2],
            ),
        ):
            members = sorted(groups[key], key=lambda m: m[1].id)
            rel, is_out, ntype = key
            if len(members) <= _ROLLUP_MIN and shown + len(members) <= limit:
                for edge, neighbour in members:
                    item_edges.append(edge)
                    neighbour_nodes.append(neighbour)
                    shown += 1
            else:
                ids = [m[1].id for m in members]
                arrow = (
                    f"{anchor_id} -[{rel}]-> {ntype}"
                    if is_out
                    else f"{anchor_id} <-[{rel}]- {ntype}"
                )
                examples = ", ".join(ids[:example_count])
                facts.append(f"{arrow} x{len(members)}（示例: {examples}）")
        items.append(
            CoreEvidenceItem(
                item_type="relation_set",
                title=f"RelationSet(anchor={anchor_id})",
                nodes=[entity.node, *neighbour_nodes],
                edges=item_edges,
                facts=facts,
            )
        )
    return items


# --------------------------------------------------------------------------- #
# supporting schema, closure, helpers                                          #
# --------------------------------------------------------------------------- #


def _supporting_schema(
    anchors: list[LinkedEntity], plan_result: PlanResult, schema_graph: Any
) -> tuple[list[EvidenceNode], list[EvidenceEdge]]:
    schema_nodes = list(getattr(schema_graph, "nodes", []))
    schema_edges = list(getattr(schema_graph, "edges", []))
    wanted_types = {a.node.type for a in anchors}
    wanted_types.update(hint.type_name for hint in plan_result.schema_hints)
    if not wanted_types:
        return [], []

    selected_ids = {
        str(n.id) for n in schema_nodes if str(n.label) in wanted_types
    }
    out_nodes = [
        EvidenceNode(
            id=str(n.id),
            label=str(n.label),
            type=str(n.type),
            attrs=dict(getattr(n, "attrs", {}) or {}),
            tier=2,
        )
        for n in schema_nodes
        if str(n.id) in selected_ids
    ]
    out_edges: list[EvidenceEdge] = []
    for edge in schema_edges:
        if str(edge.source) in selected_ids or str(edge.target) in selected_ids:
            out_edges.append(
                EvidenceEdge(
                    source=str(edge.source),
                    target=str(edge.target),
                    rel=str(edge.rel),
                    tier=2,
                )
            )
    # Cap schema edges deterministically to keep the section bounded.
    out_edges = sorted(out_edges, key=lambda e: (e.source, e.target, e.rel))[:40]
    return out_nodes, out_edges


def _close_edge_endpoints(evidence: QAEvidence, node_by_id: dict[str, Any]) -> None:
    rendered: set[str] = set()
    for item in evidence.core:
        rendered.update(n.id for n in item.nodes)
        rendered.update(h.node.id for h in item.hops)
    rendered.update(n.id for n in evidence.supporting_nodes)

    endpoints: set[str] = set()
    for item in evidence.core:
        for edge in item.edges:
            endpoints.add(edge.source)
            endpoint = edge.target
            endpoints.add(endpoint)
    for edge in evidence.supporting_edges:
        endpoints.add(edge.source)
        endpoints.add(edge.target)

    for node_id in sorted(endpoints - rendered):
        node = node_by_id.get(node_id)
        if node is None:
            continue
        evidence.supporting_nodes.append(
            EvidenceNode(
                id=str(node.id),
                label=str(node.label),
                type=str(node.type),
                attrs=dict(getattr(node, "attrs", {}) or {}),
                tier=0,
            )
        )


def _same_type_items(
    anchors: list[LinkedEntity],
    nodes: list[Any],
    edges: list[Any],
    cfg: Any,
) -> list[CoreEvidenceItem]:
    limit = int(getattr(cfg, "max_same_type_candidates", 10))
    neighbours = _neighbour_index(edges)
    items: list[CoreEvidenceItem] = []
    for entity in anchors:
        anchor_id = entity.node.id
        anchor_type = entity.node.type
        anchor_neighbours = neighbours.get(anchor_id, set())
        candidates = [
            node
            for node in nodes
            if str(node.type) == anchor_type and str(node.id) != anchor_id
        ]
        if not candidates:
            continue
        ranked = sorted(
            candidates,
            key=lambda node: (
                -len(neighbours.get(str(node.id), set()) & anchor_neighbours),
                str(node.id),
            ),
        )[:limit]
        ev_nodes = [
            EvidenceNode(
                id=str(node.id),
                label=str(node.label),
                type=str(node.type),
                attrs=dict(getattr(node, "attrs", {}) or {}),
                tier=2,
            )
            for node in ranked
        ]
        items.append(
            CoreEvidenceItem(
                item_type="same_type_candidates",
                title=f"SameTypeCandidates(type={anchor_type}, total={len(candidates)}, top={len(ev_nodes)})",
                nodes=ev_nodes,
                note="按与目标资源共享邻居数排序，取前若干候选。",
            )
        )
    return items


def _neighbour_index(edges: list[Any]) -> dict[str, set[str]]:
    index: dict[str, set[str]] = defaultdict(set)
    for edge in edges:
        src, tgt = str(edge.source), str(edge.target)
        index[src].add(tgt)
        index[tgt].add(src)
    return index


def _core_node_ids(core: list[CoreEvidenceItem]) -> set[str]:
    ids: set[str] = set()
    for item in core:
        ids.update(node.id for node in item.nodes)
        ids.update(hop.node.id for hop in item.hops)
    return ids


def _cap_chains(core: list[CoreEvidenceItem], max_chains: int) -> list[CoreEvidenceItem]:
    chain_types = {"impact_chain", "trace_chain", "candidate_path"}
    out: list[CoreEvidenceItem] = []
    chains = 0
    for item in core:
        if item.item_type in chain_types:
            if chains >= max_chains:
                continue
            chains += 1
        out.append(item)
    return out


def _raw_adjacency(edges: list[Any], *, directed: bool) -> dict[str, list[tuple[str, str]]]:
    adjacency: dict[str, list[tuple[str, str]]] = {}
    for edge in edges:
        src, tgt, rel = str(edge.source), str(edge.target), str(edge.rel)
        adjacency.setdefault(src, []).append((tgt, rel))
        if not directed:
            adjacency.setdefault(tgt, []).append((src, rel))
    return adjacency


def _ev_node(node_id: str, node_by_id: dict[str, Any], *, tier: int) -> EvidenceNode:
    node = node_by_id.get(node_id)
    if node is None:
        return EvidenceNode(id=node_id, label=node_id, type="未知类型", tier=tier)
    return EvidenceNode(
        id=str(node.id),
        label=str(node.label),
        type=str(node.type),
        attrs=dict(getattr(node, "attrs", {}) or {}),
        tier=tier,
    )
