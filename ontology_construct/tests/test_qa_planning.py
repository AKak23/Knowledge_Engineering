"""Entity-linking and planner tests (§5, §4)."""

from __future__ import annotations

from ontology_construct.config import QAConfig
from ontology_construct.qa import GraphData, GraphEdge, GraphNode
from ontology_construct.qa_linking import resolve_entities
from ontology_construct.qa_planner import detect_intent, plan_question, resolve_mode


def _graph(nodes, edges=None) -> GraphData:
    return GraphData(ontology={}, nodes=nodes, edges=edges or [])


def _instance_graph() -> GraphData:
    return _graph(
        [
            GraphNode(id="inst:411", label="激光干涉仪1", type="制造资源", attrs={"source_field": "资源"}),
            GraphNode(id="inst:538", label="0011号生产工单", type="生产工单", attrs={"实例编码": "0011_3"}),
            GraphNode(id="inst:830", label="生产工单", type="生产工单", attrs={}),
            GraphNode(id="inst:91", label="0011号机床批", type="被检实体", attrs={"实例编码": "0011_32"}),
        ]
    )


def _schema_graph() -> GraphData:
    return _graph(
        [
            GraphNode(id="obj:1", label="生产工单", type="生产工单", attrs={}),
            GraphNode(id="obj:2", label="制造资源", type="制造资源", attrs={}),
        ]
    )


def test_linker_longest_match_shadowing():
    result = resolve_entities(
        question="激光干涉仪1坏了会影响0011号生产工单吗",
        instance_graph=_instance_graph(),
        schema_graph=_schema_graph(),
    )
    ids = {entity.node.id for entity in result.anchors}
    # "0011号生产工单" links inst:538; the bare "生产工单" (inst:830) is shadowed.
    assert ids == {"inst:411", "inst:538"}
    assert "inst:830" not in ids


def test_linker_short_numeric_rejected():
    graph = _graph([GraphNode(id="inst:1", label="X", type="T", attrs={"实例编码": "1"})])
    result = resolve_entities(
        question="1号机床怎么样", instance_graph=graph, schema_graph=_graph([])
    )
    assert result.anchors == []


def test_linker_code_boundary():
    # A node coded "0011" must not link inside the longer code "0011_32".
    graph = _graph(
        [GraphNode(id="inst:x", label="订单X", type="生产订单", attrs={"实例编码": "0011"})]
    )
    result = resolve_entities(
        question="0011_32这个批次坏了吗", instance_graph=graph, schema_graph=_graph([])
    )
    assert all(entity.node.id != "inst:x" for entity in result.anchors)


def test_planner_mode_by_anchor_cardinality():
    cfg = QAConfig()
    inst, schema = _instance_graph(), _schema_graph()
    two = plan_question(
        question="激光干涉仪1坏了会影响0011号生产工单吗",
        instance_graph=inst,
        schema_graph=schema,
        cfg=cfg,
    )
    assert two.plan.mode == "connectivity"
    one = plan_question(
        question="激光干涉仪1坏了吗", instance_graph=inst, schema_graph=schema, cfg=cfg
    )
    assert one.plan.mode == "local"
    schema_q = plan_question(
        question="什么是质量门控点", instance_graph=inst, schema_graph=schema, cfg=cfg
    )
    assert schema_q.plan.mode == "schema"
    general = plan_question(
        question="随便聊聊天气", instance_graph=inst, schema_graph=schema, cfg=cfg
    )
    assert general.plan.mode == "general"


def test_planner_intent_impact_priority():
    assert detect_intent("激光干涉仪1坏了会影响0011号生产工单吗") == "impact"
    assert detect_intent("这个缺陷来源于哪里") == "trace"
    assert detect_intent("从A到B经过哪些步骤") == "path"


def test_planner_intent_fuzzy_phrasings():
    # Failure words are neutral; the direction word decides impact vs trace.
    assert detect_intent("某个工艺毁坏会如何影响下游") == "impact"
    assert detect_intent("某个步骤出问题是哪里的原因") == "trace"  # not path via "步骤"
    assert detect_intent("这个缺陷是什么原因造成的") == "trace"
    assert detect_intent("2号加工中心损坏影响范围有多大") == "impact"


def test_planner_intent_direct_neighbor_questions_route_to_relation():
    # Direct-degree / direct-neighbor asks (G03/G04 phrasing) need raw one-hop
    # edges, not the trace fan-out that "上游/下游" would otherwise trigger.
    assert detect_intent("业务对象 X 有多少个直接上游来源对象，又直接关联了多少个下游对象？") == "relation"
    assert detect_intent("与业务对象 X 直接相关的对象有哪些？请区分上游来源对象和下游关联对象。") == "relation"
    # Failure/cause wording keeps the question on the impact/trace path.
    assert detect_intent("如果对象 X 发生取消或失效，会影响哪些下游对象？") == "impact"
    assert detect_intent("X 一旦故障，会级联影响多少个生产工单") == "impact"
    assert detect_intent("漆膜颗粒增多的直接原因是什么") == "trace"


def test_forced_anchors_injected():
    inst = _instance_graph()
    result = plan_question(
        question="随便问",  # links nothing
        instance_graph=inst,
        schema_graph=_schema_graph(),
        cfg=QAConfig(),
        forced_anchors=["inst:411", "inst:538"],
    )
    ids = {entity.node.id for entity in result.plan.linked_entities}
    assert ids == {"inst:411", "inst:538"}


def test_resolve_mode_ignores_duplicate_anchor_ids():
    from ontology_construct.qa_evidence import EvidenceNode, LinkedEntity

    node = EvidenceNode(id="inst:1", label="X", type="T")
    dup = [LinkedEntity(mention="X", node=node), LinkedEntity(mention="X", node=node)]
    assert resolve_mode(dup, "impact", "X坏了") == "local"
