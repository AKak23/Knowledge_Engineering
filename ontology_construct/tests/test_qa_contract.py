"""Contract-level tests for graph_qa_evidence_v1 (renderer + validator)."""

from __future__ import annotations

from ontology_construct.config import QAConfig
from ontology_construct.qa_evidence import (
    SECTION_ORDER,
    ChainHop,
    CoreEvidenceItem,
    EvidenceEdge,
    EvidenceNode,
    LinkedEntity,
    QAEvidence,
    QueryPlan,
    validate_contract,
)
from ontology_construct.qa_prompt import render_graph_qa_evidence_prompt


def _node(node_id: str, type_: str, label: str, **attrs) -> EvidenceNode:
    return EvidenceNode(id=node_id, label=label, type=type_, attrs=dict(attrs))


def _impact_evidence() -> QAEvidence:
    laser = _node("inst:411", "制造资源", "激光干涉仪1", source_field="资源")
    order = _node("inst:538", "生产工单", "0011号生产工单", 实例编码="0011_3")
    proc = _node("inst:542", "加工工序", "0011号生产工单-定位精度激光检测")
    plan = QueryPlan(
        mode="connectivity",
        intent="impact",
        linked_entities=[
            LinkedEntity(mention="激光干涉仪1", node=laser),
            LinkedEntity(mention="0011号生产工单", node=order),
        ],
    )
    chain = CoreEvidenceItem(
        item_type="impact_chain",
        title="ImpactChain(cause=inst:411, affected=inst:538, hops=2)",
        hops=[
            ChainHop(node=laser),
            ChainHop(node=proc, rel="执行于", raw_direction="reverse"),
            ChainHop(node=order, rel="包含", raw_direction="reverse"),
        ],
        edges=[
            EvidenceEdge(source="inst:542", target="inst:411", rel="执行于", tier=1),
            EvidenceEdge(source="inst:538", target="inst:542", rel="包含", tier=1),
        ],
        note="列表顺序为影响传播方向；箭头表示图中原始边方向。",
    )
    return QAEvidence(
        question="激光干涉仪1坏了会影响0011号生产工单吗",
        plan=plan,
        core=[
            CoreEvidenceItem(
                item_type="target_node", title="TargetNode", nodes=[laser]
            ),
            CoreEvidenceItem(
                item_type="target_node", title="TargetNode", nodes=[order]
            ),
            chain,
        ],
        supporting_nodes=[proc],
    )


def test_section_order_frozen():
    evidence = _impact_evidence()
    cfg = QAConfig()
    prompt = render_graph_qa_evidence_prompt(evidence, cfg)
    present = [line for line in prompt.split("\n") if line in SECTION_ORDER]
    # QUESTION, QUERY_PLAN, CORE_EVIDENCE, SUPPORTING_NODES, ANSWER_REQUIREMENT
    assert present == [
        "[QUESTION]",
        "[QUERY_PLAN]",
        "[CORE_EVIDENCE]",
        "[SUPPORTING_NODES]",
        "[ANSWER_REQUIREMENT]",
    ]


def test_impact_chain_arrows_and_direction():
    prompt = render_graph_qa_evidence_prompt(_impact_evidence(), QAConfig())
    assert "inst:411 | 制造资源 | 激光干涉仪1 | source_field=资源" in prompt
    assert "<-[执行于]- inst:542 | 加工工序 | 0011号生产工单-定位精度激光检测" in prompt
    assert "<-[包含]- inst:538 | 生产工单 | 0011号生产工单 | 实例编码=0011_3" in prompt


def test_render_deterministic():
    evidence = _impact_evidence()
    cfg = QAConfig()
    assert render_graph_qa_evidence_prompt(evidence, cfg) == render_graph_qa_evidence_prompt(
        evidence, cfg
    )


def test_no_score_or_reason_in_prompt():
    evidence = _impact_evidence()
    # Attach debug scores; they must not leak into the prompt.
    evidence.core[0].nodes[0].score = 1.488
    evidence.core[0].nodes[0].reasons = ["ppr", "schema_gate"]
    prompt = render_graph_qa_evidence_prompt(evidence, QAConfig())
    assert "score=" not in prompt
    assert "reason=" not in prompt


def test_contract_validates_clean():
    evidence = _impact_evidence()
    cfg = QAConfig()
    prompt = render_graph_qa_evidence_prompt(evidence, cfg)
    assert validate_contract(evidence, prompt, cfg) == []


def test_edges_before_nodes():
    evidence = _impact_evidence()
    evidence.supporting_edges = [
        EvidenceEdge(source="inst:538", target="inst:542", rel="包含", tier=3)
    ]
    prompt = render_graph_qa_evidence_prompt(evidence, QAConfig())
    assert prompt.index("[SUPPORTING_EDGES]") < prompt.index("[SUPPORTING_NODES]")


def test_core_never_truncated_under_budget():
    evidence = _impact_evidence()
    # Flood supporting nodes so the total would blow a tiny budget.
    evidence.supporting_nodes = [
        _node(f"inst:{900 + i}", "加工工序", f"噪声工序{i}" * 10) for i in range(200)
    ]
    cfg = QAConfig(max_prompt_chars=6000)
    prompt = render_graph_qa_evidence_prompt(evidence, cfg)
    # CORE must survive intact: the impact chain is fully present.
    assert "<-[执行于]- inst:542" in prompt
    assert "<-[包含]- inst:538" in prompt
    # And a truncation marker must appear only in a supporting section.
    assert "超出预算，已省略" in prompt
    violations = validate_contract(evidence, prompt, cfg)
    assert violations == [], violations
    assert len(prompt) <= 6000


def test_empty_core_placeholder():
    plan = QueryPlan(mode="general", intent="general")
    evidence = QAEvidence(question="随便问问", plan=plan)
    prompt = render_graph_qa_evidence_prompt(evidence, QAConfig())
    assert "未找到核心证据项" in prompt
    assert validate_contract(evidence, prompt, QAConfig()) == []


def test_unlinked_mentions_rendered():
    plan = QueryPlan(
        mode="general",
        intent="impact",
        unlinked_mentions=["99号喷漆室"],
    )
    evidence = QAEvidence(question="99号喷漆室坏了会影响什么", plan=plan)
    prompt = render_graph_qa_evidence_prompt(evidence, QAConfig())
    assert "unlinked_mentions:" in prompt
    assert "- 99号喷漆室" in prompt
