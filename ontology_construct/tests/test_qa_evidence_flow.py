"""End-to-end answer_question tests for the graph_qa_evidence_v1 path (§10, §3.8)."""

from __future__ import annotations

import asyncio
import json

from ontology_construct.config import QAConfig
from ontology_construct.qa import answer_question


class CapturingLLMClient:
    def __init__(self, response: str):
        self.response = response
        self.prompt = ""
        self.system_prompt = ""

    async def complete(self, prompt: str, *, system_prompt: str | None = None) -> str:
        self.prompt = prompt
        self.system_prompt = system_prompt or ""
        return self.response


def _laser_ontology() -> dict:
    return {
        "ontology": {"ontologyName": "机床制造本体"},
        "objects": [
            {"objectId": 1, "objectName": "制造资源", "attributes": []},
            {"objectId": 2, "objectName": "加工工序", "attributes": []},
            {"objectId": 3, "objectName": "生产工单", "attributes": []},
        ],
        "links": [
            {"sourceObjectId": 3, "targetObjectId": 2, "linkInfo": "包含"},
            {"sourceObjectId": 2, "targetObjectId": 1, "linkInfo": "执行于"},
        ],
        "actions": [],
        "instances": [
            {
                "instanceId": 411,
                "instanceName": "激光干涉仪1",
                "instanceCode": "1_12",
                "objectName": "制造资源",
                "attributeValues": [],
                "linkValues": [],
            },
            {
                "instanceId": 542,
                "instanceName": "0011号生产工单-定位精度激光检测",
                "instanceCode": "0011_gb_t_3",
                "objectName": "加工工序",
                "attributeValues": [],
                "linkValues": [{"linkInfo": "执行于", "targetInstanceId": 411}],
            },
            {
                "instanceId": 538,
                "instanceName": "0011号生产工单",
                "instanceCode": "0011_3",
                "objectName": "生产工单",
                "attributeValues": [],
                "linkValues": [{"linkInfo": "包含", "targetInstanceId": 542}],
            },
        ],
    }


def _run(coro):
    return asyncio.run(coro)


def test_evidence_path_builds_contract_prompt():
    client = CapturingLLMClient(
        "<think>沿链检查</think><answer>"
        + json.dumps(
            {
                "answer_status": "answerable",
                "summary": "会影响。0011号生产工单包含定位精度激光检测工序，该工序执行于激光干涉仪1。",
                "path": ["inst:411", "inst:542", "inst:538"],
                "edges": [["inst:538", "inst:542", "包含"], ["inst:542", "inst:411", "执行于"]],
                "groups": [],
                "missing": [],
            },
            ensure_ascii=False,
        )
        + "</answer>"
    )
    response = _run(
        answer_question(
            question="激光干涉仪1坏了会影响0011号生产工单吗",
            ontology=_laser_ontology(),
            llm_client=client,
            context_mode="graph_rag",
            qa_config=QAConfig(evidence_contract="graph_qa_evidence_v1"),
        )
    )
    # Prompt is the new contract, seeded on exactly two anchors.
    assert "[CORE_EVIDENCE]" in client.prompt
    assert "<-[执行于]- inst:542" in client.prompt
    assert client.prompt.index("[SUPPORTING_EDGES]") < client.prompt.index("[SUPPORTING_NODES]") if "[SUPPORTING_EDGES]" in client.prompt else True
    assert response.answer.status == "answerable"
    assert response.answer.path == ["inst:411", "inst:542", "inst:538"]
    assert response.meta["mode"] == "connectivity"
    assert response.meta["intent"] == "impact"
    assert response.meta["conformance_violations"] == []


def test_insufficient_evidence_not_fallback():
    client = CapturingLLMClient(
        "<think>图中无人员信息</think><answer>"
        + json.dumps(
            {
                "answer_status": "insufficient_evidence",
                "summary": "证据不足：图中没有该工单的负责人属性。",
                "missing": ["0011号生产工单 的负责人属性"],
            },
            ensure_ascii=False,
        )
        + "</answer>"
    )
    response = _run(
        answer_question(
            question="0011号生产工单的负责人是谁",
            ontology=_laser_ontology(),
            llm_client=client,
            context_mode="graph_rag",
            qa_config=QAConfig(evidence_contract="graph_qa_evidence_v1"),
        )
    )
    assert response.answer.status == "insufficient_evidence"
    assert response.answer.missing == ["0011号生产工单 的负责人属性"]
    # Must NOT be a heuristic fallback answer.
    assert response.answer.raw is None or "fallback_reason" not in (response.answer.raw or "")


def test_legacy_graph_rag_v1_still_works():
    client = CapturingLLMClient(
        json.dumps(
            {
                "think": ["step"],
                "highlight": {"nodes": [], "edges": []},
                "answer": {"summary": "答案"},
            },
            ensure_ascii=False,
        )
    )
    response = _run(
        answer_question(
            question="激光干涉仪1坏了会影响0011号生产工单吗",
            ontology=_laser_ontology(),
            llm_client=client,
            context_mode="graph_rag",
            qa_config=QAConfig(evidence_contract="graph_rag_v1"),
        )
    )
    # Legacy renderer emits the old sectioning, not the new contract.
    assert "[EVIDENCE_SUBGRAPH]" in client.prompt
    assert "[CORE_EVIDENCE]" not in client.prompt
    assert response.answer.summary == "答案"


def test_general_model_legacy_format_on_new_path():
    # A general model emits the legacy {think,highlight,answer} shape even though
    # the new contract asked for <think></think><answer>JSON</answer>. It must
    # still parse (populated think, real summary) — not dump raw JSON.
    client = CapturingLLMClient(
        json.dumps(
            {
                "think": ["定位0011号生产工单", "沿包含链检查"],
                "highlight": {"nodes": ["inst:538"], "edges": []},
                "answer": {
                    "summary": "会影响。",
                    "path": ["inst:411", "inst:542", "inst:538"],
                    "edges": [
                        {"source": "inst:542", "target": "inst:411", "relation": "执行于"}
                    ],
                },
            },
            ensure_ascii=False,
        )
    )
    response = _run(
        answer_question(
            question="激光干涉仪1坏了会影响0011号生产工单吗",
            ontology=_laser_ontology(),
            llm_client=client,
            context_mode="graph_rag",
            qa_config=QAConfig(evidence_contract="graph_qa_evidence_v1"),
        )
    )
    assert response.think == ["定位0011号生产工单", "沿包含链检查"]
    assert response.answer.summary == "会影响。"
    assert ["inst:542", "inst:411", "执行于"] in response.answer.edges
