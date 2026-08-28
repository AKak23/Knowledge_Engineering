"""Golden coverage regression (§11.2, §11.3).

This suite is the recall-layer change gate: any qa_retrieval / assembly change
must keep these passing. Assertions run on QAEvidence + rendered prompt only —
no LLM. A slow integration case runs against the real job dir when present.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from ontology_construct.config import QAConfig
from ontology_construct.qa import formatted_ontology_to_graph
from ontology_construct.qa_assembly import assemble_evidence
from ontology_construct.qa_evidence import validate_contract
from ontology_construct.qa_planner import plan_question
from ontology_construct.qa_prompt import render_graph_qa_evidence_prompt
from ontology_construct.qa_semantics import load_default_policy

_FIXTURE = Path(__file__).parent / "fixtures" / "golden_industrial.json"
_REAL_JOB = Path(
    "outputs/api_jobs/job_b58e2654d68b4ebe819d73f96246af94/formatted_ontology.json"
)


def _evidence_for(data: dict, question: str, cfg: QAConfig | None = None):
    cfg = cfg or QAConfig()
    inst = formatted_ontology_to_graph(data, view="instance")
    schema = formatted_ontology_to_graph(data, view="schema")
    plan_result = plan_question(
        question=question, instance_graph=inst, schema_graph=schema, cfg=cfg
    )
    evidence = assemble_evidence(
        question=question,
        plan_result=plan_result,
        instance_graph=inst,
        schema_graph=schema,
        policy=load_default_policy(),
        cfg=cfg,
    )
    prompt = render_graph_qa_evidence_prompt(evidence, cfg)
    return evidence, prompt


@pytest.fixture(scope="module")
def golden() -> dict:
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


def _anchor_ids(evidence) -> set[str]:
    return {entity.node.id for entity in evidence.plan.linked_entities}


def test_golden_impact_judgement(golden):
    evidence, prompt = _evidence_for(golden, "激光干涉仪1坏了会影响0011号生产工单吗")
    assert _anchor_ids(evidence) == {"inst:411", "inst:538"}
    assert evidence.plan.mode == "connectivity"
    chains = [i for i in evidence.core if i.item_type == "impact_chain"]
    assert chains and [h.node.id for h in chains[0].hops] == [
        "inst:411",
        "inst:542",
        "inst:538",
    ]
    # Both critical edges are in the prompt (the original bug lost them).
    assert "<-[执行于]- inst:542" in prompt
    assert "<-[包含]- inst:538" in prompt
    assert validate_contract(evidence, prompt, QAConfig()) == []


def test_golden_impact_enumeration(golden):
    evidence, prompt = _evidence_for(golden, "激光干涉仪1坏了会影响哪些生产工单")
    assert _anchor_ids(evidence) == {"inst:411"}
    agg = [i for i in evidence.core if i.item_type == "aggregate_summary"]
    assert agg
    facts = " ".join(agg[0].facts)
    assert "生产工单" in facts
    assert validate_contract(evidence, prompt, QAConfig()) == []


def test_golden_relation_enumeration(golden):
    evidence, prompt = _evidence_for(golden, "0011号生产工单包含哪些工序")
    assert _anchor_ids(evidence) == {"inst:538"}
    rel_items = [i for i in evidence.core if i.item_type == "relation_set"]
    assert rel_items
    # The 包含 children of inst:538 must be reachable in the prompt.
    for child in ("inst:539", "inst:541", "inst:542"):
        assert child in prompt
    assert validate_contract(evidence, prompt, QAConfig()) == []


def test_golden_path_query(golden):
    evidence, prompt = _evidence_for(golden, "从0011号机床订单到激光干涉仪1的链路是什么")
    assert _anchor_ids(evidence) == {"inst:90", "inst:411"}
    paths = [i for i in evidence.core if i.item_type == "candidate_path"]
    assert paths
    ids = [h.node.id for h in paths[0].hops]
    assert ids[0] in {"inst:90", "inst:411"} and ids[-1] in {"inst:90", "inst:411"}
    assert "inst:542" in ids or "inst:538" in ids
    assert validate_contract(evidence, prompt, QAConfig()) == []


def test_golden_fictional_entity(golden):
    evidence, prompt = _evidence_for(golden, "99号喷漆室坏了会影响什么")
    assert _anchor_ids(evidence) == set()
    assert any("99号喷漆室" in m for m in evidence.plan.unlinked_mentions)
    # No fabricated chain when nothing links.
    assert not [
        i for i in evidence.core if i.item_type in {"impact_chain", "trace_chain"}
    ]
    assert validate_contract(evidence, prompt, QAConfig()) == []


def test_golden_budget_never_truncates_core(golden):
    # Even at a tight budget the impact chain survives (the original failure).
    evidence, prompt = _evidence_for(
        golden, "激光干涉仪1坏了会影响0011号生产工单吗", QAConfig(max_prompt_chars=4000)
    )
    assert "<-[执行于]- inst:542" in prompt
    assert len(prompt) <= 4000
    assert validate_contract(evidence, prompt, QAConfig(max_prompt_chars=4000)) == []


def test_golden_seed_explosion_regression(golden):
    # The "生产工单" substring used to seed ~68 nodes. With the recall layer wired
    # in, anchors stay at 2 and no scorer internals leak into the prompt.
    from ontology_construct.qa_retrieval import retrieve_supporting_candidates

    cfg = QAConfig()
    inst = formatted_ontology_to_graph(golden, view="instance")
    schema = formatted_ontology_to_graph(golden, view="schema")
    question = "激光干涉仪1坏了会影响0011号生产工单吗"
    plan_result = plan_question(
        question=question, instance_graph=inst, schema_graph=schema, cfg=cfg
    )
    assert len(plan_result.plan.linked_entities) == 2
    anchor_ids = [e.node.id for e in plan_result.plan.linked_entities]
    support_nodes, support_edges = retrieve_supporting_candidates(
        anchor_ids=anchor_ids, instance_graph=inst, qa_config=cfg
    )
    evidence = assemble_evidence(
        question=question,
        plan_result=plan_result,
        instance_graph=inst,
        schema_graph=schema,
        policy=load_default_policy(),
        cfg=cfg,
        supporting_nodes=support_nodes,
        supporting_edges=support_edges,
    )
    prompt = render_graph_qa_evidence_prompt(evidence, cfg)
    assert "score=" not in prompt and "reason=" not in prompt
    assert "<-[执行于]- inst:542" in prompt  # CORE intact
    assert validate_contract(evidence, prompt, cfg) == []


@pytest.mark.skipif(not _REAL_JOB.exists(), reason="real job dir not present")
def test_golden_real_job_impact_chain():
    data = json.loads(_REAL_JOB.read_text(encoding="utf-8"))
    evidence, prompt = _evidence_for(data, "激光干涉仪1坏了会影响0011号生产工单吗")
    assert _anchor_ids(evidence) == {"inst:411", "inst:538"}
    assert "<-[执行于]- inst:542" in prompt
    assert "<-[包含]- inst:538" in prompt
    assert validate_contract(evidence, prompt, QAConfig()) == []
    assert len(prompt) <= QAConfig().max_prompt_chars
