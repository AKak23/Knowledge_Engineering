"""Semantic-direction and assembly invariant tests (§6, §7)."""

from __future__ import annotations

from ontology_construct.config import QAConfig
from ontology_construct.qa import GraphData, GraphEdge, GraphNode
from ontology_construct.qa_assembly import assemble_evidence
from ontology_construct.qa_evidence import (
    EvidenceEdge,
    EvidenceNode,
    LinkedEntity,
    QueryPlan,
    validate_contract,
)
from ontology_construct.qa_planner import PlanResult
from ontology_construct.qa_prompt import render_graph_qa_evidence_prompt
from ontology_construct.qa_retrieval import retrieve_supporting_candidates
from ontology_construct.qa_semantics import build_impact_digraph, load_default_policy


def _laser_graph() -> GraphData:
    nodes = [
        GraphNode(id="inst:411", label="激光干涉仪1", type="制造资源", attrs={"source_field": "资源"}),
        GraphNode(id="inst:542", label="0011号生产工单-定位精度激光检测", type="加工工序", attrs={}),
        GraphNode(id="inst:538", label="0011号生产工单", type="生产工单", attrs={"实例编码": "0011_3"}),
        GraphNode(id="inst:90", label="0011号机床订单", type="生产订单", attrs={"实例编码": "0011"}),
    ]
    edges = [
        GraphEdge(source="inst:538", target="inst:542", rel="包含"),
        GraphEdge(source="inst:542", target="inst:411", rel="执行于"),
        GraphEdge(source="inst:90", target="inst:538", rel="包含"),
    ]
    return GraphData(ontology={}, nodes=nodes, edges=edges)


def _schema_graph() -> GraphData:
    return GraphData(
        ontology={},
        nodes=[
            GraphNode(id="schema:1", label="类型A", type="object", attrs={}),
            GraphNode(id="schema:2", label="类型B", type="object", attrs={}),
        ],
        edges=[GraphEdge(source="schema:1", target="schema:2", rel="允许")],
    )


def _anchor(node_id: str, type_: str, label: str, **attrs) -> LinkedEntity:
    return LinkedEntity(
        mention=label,
        node=EvidenceNode(id=node_id, label=label, type=type_, attrs=dict(attrs), tier=1),
    )


def _plan(mode, intent, anchors) -> PlanResult:
    return PlanResult(
        plan=QueryPlan(mode=mode, intent=intent, linked_entities=anchors),
        schema_hints=[],
    )


def test_semantics_impact_digraph_direction():
    policy = load_default_policy()
    edges = [
        GraphEdge(source="p", target="r", rel="执行于"),   # reverse -> r->p
        GraphEdge(source="o", target="p", rel="包含"),     # both -> o->p and p->o
        GraphEdge(source="a", target="b", rel="语义关联"),  # exclude -> nothing
    ]
    adj = build_impact_digraph(edges, policy, view="impact")
    assert ("p", "执行于") in adj.get("r", [])          # resource -> process
    assert "p" not in {n for n, _ in adj.get("a", [])}   # 语义关联 excluded
    assert ("p", "包含") in adj.get("o", [])
    assert ("o", "包含") in adj.get("p", [])


def test_assembly_I2_path_on_impact_digraph():
    anchors = [
        _anchor("inst:411", "制造资源", "激光干涉仪1", source_field="资源"),
        _anchor("inst:538", "生产工单", "0011号生产工单", 实例编码="0011_3"),
    ]
    evidence = assemble_evidence(
        question="激光干涉仪1坏了会影响0011号生产工单吗",
        plan_result=_plan("connectivity", "impact", anchors),
        instance_graph=_laser_graph(),
        schema_graph=GraphData(ontology={}, nodes=[], edges=[]),
        policy=load_default_policy(),
        cfg=QAConfig(),
    )
    chains = [i for i in evidence.core if i.item_type == "impact_chain"]
    assert chains, "expected an ImpactChain"
    ids = [hop.node.id for hop in chains[0].hops]
    assert ids == ["inst:411", "inst:542", "inst:538"]
    # Rendered arrows follow raw edge direction.
    prompt = render_graph_qa_evidence_prompt(evidence, QAConfig())
    assert "<-[执行于]- inst:542" in prompt
    assert "<-[包含]- inst:538" in prompt
    assert validate_contract(evidence, prompt, QAConfig()) == []


def test_assembly_I3_relation_rollup():
    nodes = [GraphNode(id="inst:411", label="激光干涉仪1", type="制造资源", attrs={})]
    edges = []
    for i in range(5):
        pid = f"inst:{600 + i}"
        nodes.append(GraphNode(id=pid, label=f"工序{i}", type="加工工序", attrs={}))
        edges.append(GraphEdge(source=pid, target="inst:411", rel="执行于"))
    graph = GraphData(ontology={}, nodes=nodes, edges=edges)
    anchors = [_anchor("inst:411", "制造资源", "激光干涉仪1")]
    evidence = assemble_evidence(
        question="激光干涉仪1被哪些工序使用",
        plan_result=_plan("local", "relation", anchors),
        instance_graph=graph,
        schema_graph=GraphData(ontology={}, nodes=[], edges=[]),
        policy=load_default_policy(),
        cfg=QAConfig(),
    )
    rel_items = [i for i in evidence.core if i.item_type == "relation_set"]
    assert rel_items
    facts = " ".join(rel_items[0].facts)
    assert "执行于" in facts and "x5" in facts


def test_assembly_I4_fanout_aggregate():
    anchors = [_anchor("inst:411", "制造资源", "激光干涉仪1")]
    evidence = assemble_evidence(
        question="激光干涉仪1坏了会影响哪些工单",
        plan_result=_plan("local", "impact", anchors),
        instance_graph=_laser_graph(),
        schema_graph=GraphData(ontology={}, nodes=[], edges=[]),
        policy=load_default_policy(),
        cfg=QAConfig(),
    )
    agg = [i for i in evidence.core if i.item_type == "aggregate_summary"]
    assert agg
    facts = " ".join(agg[0].facts)
    assert "生产工单" in facts and "受影响" in facts


def test_assembly_replace_same_type_candidates():
    nodes = [
        GraphNode(id="inst:411", label="激光干涉仪1", type="检测器具", attrs={}),
        GraphNode(id="inst:898", label="激光干涉仪XL-80", type="检测器具", attrs={}),
        GraphNode(id="inst:1034", label="激光干涉仪Renishaw", type="检测器具", attrs={}),
        GraphNode(id="inst:542", label="定位精度激光检测", type="加工工序", attrs={}),
    ]
    edges = [GraphEdge(source="inst:542", target="inst:411", rel="执行于")]
    graph = GraphData(ontology={}, nodes=nodes, edges=edges)
    anchors = [_anchor("inst:411", "检测器具", "激光干涉仪1")]
    evidence = assemble_evidence(
        question="激光干涉仪1坏了有哪些同类可以替代",
        plan_result=_plan("local", "replace", anchors),
        instance_graph=graph,
        schema_graph=GraphData(ontology={}, nodes=[], edges=[]),
        policy=load_default_policy(),
        cfg=QAConfig(),
    )
    cand = [i for i in evidence.core if i.item_type == "same_type_candidates"]
    assert cand
    ids = {n.id for n in cand[0].nodes}
    assert ids == {"inst:898", "inst:1034"}  # same type, anchor excluded
    prompt = render_graph_qa_evidence_prompt(evidence, QAConfig())
    assert validate_contract(evidence, prompt, QAConfig()) == []


def test_assembly_I6_edge_endpoint_closure():
    graph = _laser_graph()
    # An isolated node not reachable from the anchor's one-hop neighbourhood.
    graph.nodes.append(GraphNode(id="inst:999", label="孤立资源", type="制造资源", attrs={}))
    anchors = [_anchor("inst:411", "制造资源", "激光干涉仪1")]
    # A supporting edge whose target (inst:999) has no node line anywhere.
    dangling = [EvidenceEdge(source="inst:411", target="inst:999", rel="使用", tier=3)]
    evidence = assemble_evidence(
        question="激光干涉仪1相关",
        plan_result=_plan("local", "general", anchors),
        instance_graph=graph,
        schema_graph=GraphData(ontology={}, nodes=[], edges=[]),
        policy=load_default_policy(),
        cfg=QAConfig(),
        supporting_edges=dangling,
    )
    node_ids = {n.id for n in evidence.supporting_nodes}
    assert "inst:999" in node_ids
    closure = [n for n in evidence.supporting_nodes if n.id == "inst:999"]
    assert closure[0].tier == 0
    prompt = render_graph_qa_evidence_prompt(evidence, QAConfig())
    assert validate_contract(evidence, prompt, QAConfig()) == []


def test_retrieval_soft_weights_schema_aligned_nodes_before_noise():
    graph = GraphData(
        ontology={},
        nodes=[
            GraphNode(id="inst:root", label="根节点", type="类型A", attrs={}),
            GraphNode(id="inst:z-aligned", label="对齐节点", type="类型B", attrs={}),
            GraphNode(id="inst:a-noise", label="噪声节点", type="类型C", attrs={}),
        ],
        edges=[
            GraphEdge(source="inst:root", target="inst:z-aligned", rel="允许"),
            GraphEdge(source="inst:root", target="inst:a-noise", rel="允许"),
        ],
    )
    nodes, edges = retrieve_supporting_candidates(
        anchor_ids=["inst:root"],
        instance_graph=graph,
        schema_graph=_schema_graph(),
        qa_config=QAConfig(supporting_node_limit=1, supporting_edge_limit=2),
    )
    assert [node.id for node in nodes] == ["inst:z-aligned"]
    assert [edge.target for edge in edges] == ["inst:z-aligned"]


def test_relation_set_soft_weights_schema_aligned_edges_with_fallback_rollup():
    graph = GraphData(
        ontology={},
        nodes=[
            GraphNode(id="inst:root", label="根节点", type="类型A", attrs={}),
            GraphNode(id="inst:z-aligned", label="对齐节点", type="类型B", attrs={}),
            GraphNode(id="inst:a-noise", label="噪声节点", type="类型C", attrs={}),
        ],
        edges=[
            GraphEdge(source="inst:root", target="inst:a-noise", rel="允许"),
            GraphEdge(source="inst:root", target="inst:z-aligned", rel="允许"),
        ],
    )
    evidence = assemble_evidence(
        question="根节点直接关联哪些对象",
        plan_result=_plan(
            "local",
            "relation",
            [_anchor("inst:root", "类型A", "根节点")],
        ),
        instance_graph=graph,
        schema_graph=_schema_graph(),
        policy=load_default_policy(),
        cfg=QAConfig(max_relation_lines_relation_intent=1),
    )
    rel_items = [i for i in evidence.core if i.item_type == "relation_set"]
    assert rel_items
    assert [(edge.source, edge.target, edge.rel) for edge in rel_items[0].edges] == [
        ("inst:root", "inst:z-aligned", "允许")
    ]
    assert any("类型C x1" in fact for fact in rel_items[0].facts)
    prompt = render_graph_qa_evidence_prompt(evidence, QAConfig())
    assert "score=" not in prompt and "reason=" not in prompt
    assert "schema_aligned" not in prompt
    assert validate_contract(evidence, prompt, QAConfig()) == []


def test_connectivity_path_prefers_schema_aligned_path_on_tie():
    schema = GraphData(
        ontology={},
        nodes=[
            GraphNode(id="schema:1", label="类型A", type="object", attrs={}),
            GraphNode(id="schema:2", label="类型B", type="object", attrs={}),
            GraphNode(id="schema:3", label="类型D", type="object", attrs={}),
        ],
        edges=[
            GraphEdge(source="schema:1", target="schema:2", rel="流转"),
            GraphEdge(source="schema:2", target="schema:3", rel="流转"),
        ],
    )
    graph = GraphData(
        ontology={},
        nodes=[
            GraphNode(id="inst:root", label="根节点", type="类型A", attrs={}),
            GraphNode(id="inst:z-aligned", label="对齐中间节点", type="类型B", attrs={}),
            GraphNode(id="inst:a-noise", label="噪声中间节点", type="类型C", attrs={}),
            GraphNode(id="inst:target", label="目标节点", type="类型D", attrs={}),
        ],
        edges=[
            GraphEdge(source="inst:root", target="inst:a-noise", rel="流转"),
            GraphEdge(source="inst:a-noise", target="inst:target", rel="流转"),
            GraphEdge(source="inst:root", target="inst:z-aligned", rel="流转"),
            GraphEdge(source="inst:z-aligned", target="inst:target", rel="流转"),
        ],
    )
    evidence = assemble_evidence(
        question="根节点到目标节点的路径",
        plan_result=_plan(
            "connectivity",
            "path",
            [
                _anchor("inst:root", "类型A", "根节点"),
                _anchor("inst:target", "类型D", "目标节点"),
            ],
        ),
        instance_graph=graph,
        schema_graph=schema,
        policy=load_default_policy(),
        cfg=QAConfig(max_paths_per_pair=1),
    )
    chains = [item for item in evidence.core if item.item_type == "candidate_path"]
    assert chains
    assert [hop.node.id for hop in chains[0].hops] == [
        "inst:root",
        "inst:z-aligned",
        "inst:target",
    ]
