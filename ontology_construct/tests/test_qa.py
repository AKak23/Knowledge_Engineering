import asyncio
import json

from ontology_construct.llm import MockLLMClient
from ontology_construct.models import (
    Entity,
    GlobalOntology,
    InstanceLinkValue,
    OntologyAction,
    OntologyInstance,
    OntologyRule,
    RuleCondition,
    Triple,
)
from ontology_construct.output import format_global_ontology
from ontology_construct.config import QAConfig
from ontology_construct.qa import answer_question, formatted_ontology_to_graph


class CapturingLLMClient:
    def __init__(self, response: str):
        self.response = response
        self.prompt = ""
        self.system_prompt = ""

    async def complete(self, prompt: str, *, system_prompt: str | None = None) -> str:
        self.prompt = prompt
        self.system_prompt = system_prompt or ""
        return self.response


def _formatted():
    ontology = GlobalOntology.from_entities_and_triples(
        [
            Entity(id="e1", name="用户"),
            Entity(id="e2", name="订单"),
        ],
        [
            Triple(
                subject_id="e1",
                predicate="下单",
                object_kind="entity",
                object_id="e2",
            )
        ],
        instances=[
            OntologyInstance(
                id="i1",
                name="张三",
                class_id="e1",
                link_values=[InstanceLinkValue(predicate="下单", target_instance_id="i2")],
            ),
            OntologyInstance(id="i2", name="PO-001", class_id="e2"),
        ],
    )
    return format_global_ontology(
        ontology,
        ontology_name="测试本体",
        ontology_desc="",
    )


def test_schema_graph_exposes_rule_and_action_workbench_nodes() -> None:
    ontology = GlobalOntology.from_entities_and_triples(
        [Entity(id="e1", name="设备", source_chunks=["chunk_0001"])],
        triples=[],
        rules=[
            OntologyRule(
                id="r1",
                name="高温冷却规则",
                scope_entity_id="e1",
                conditions=[
                    RuleCondition(
                        id="c1",
                        field="温度",
                        operator=">",
                        value=30,
                        value_type="number",
                    )
                ],
                source_chunks=["chunk_0001"],
            )
        ],
        actions=[
            OntologyAction(
                id="a1",
                name="启动水冷",
                target_entity_ids=["e1"],
                trigger_rule_ids=["r1"],
                source_chunks=["chunk_0001"],
            )
        ],
        instances=[
            OntologyInstance(
                id="i1",
                name="冷却设备01",
                class_id="e1",
                source_chunks=["chunk_0001"],
            )
        ],
    )
    formatted = format_global_ontology(
        ontology,
        ontology_name="设备本体",
        ontology_desc="",
    )

    graph = formatted_ontology_to_graph(formatted, view="schema")

    nodes = {node.id: node for node in graph.nodes}
    assert nodes["obj:1"].workbench["details"]["sourceChunks"] == ["chunk_0001"]
    assert nodes["obj:1"].workbench["details"]["instanceIds"] == [1]
    assert nodes["obj:1"].workbench["details"]["instanceCount"] == 1
    assert nodes["rule:1"].workbench["details"]["scopeObjectId"] == "obj:1"
    assert nodes["rule:1"].workbench["details"]["conditions"][0]["field"] == "温度"
    assert nodes["rule:1"].workbench["details"]["sourceChunks"] == ["chunk_0001"]
    assert nodes["action:1"].workbench["details"]["triggerRuleId"] == "rule:1"
    assert nodes["action:1"].workbench["details"]["sourceChunks"] == ["chunk_0001"]
    assert {(edge.source, edge.target, edge.rel) for edge in graph.edges} == {
        ("obj:1", "rule:1", "适用规则"),
        ("rule:1", "action:1", "触发"),
        ("action:1", "obj:1", "作用于"),
    }


def test_schema_graph_backfills_instance_references_for_legacy_payload() -> None:
    payload = _formatted().model_dump(mode="json")
    for item in payload["objects"]:
        item.pop("instanceIds", None)
        item.pop("instanceCount", None)

    graph = formatted_ontology_to_graph(payload, view="schema")

    nodes = {node.id: node for node in graph.nodes}
    assert nodes["obj:1"].workbench["details"]["instanceIds"] == [1]
    assert nodes["obj:2"].workbench["details"]["instanceIds"] == [2]


def _formatted_contracts():
    return {
        "ontology": {"ontologyName": "合同本体"},
        "objects": [],
        "links": [],
        "actions": [],
        "instances": [
            {
                "instanceId": 1,
                "instanceCode": "Manager_001",
                "instanceName": "客户经理_001",
                "objectName": "客户经理",
                "attributeValues": [],
                "linkValues": [
                    {
                        "linkInfo": "负责",
                        "targetInstanceId": 2,
                        "targetInstanceName": "客户合同_002",
                    }
                ],
            },
            {
                "instanceId": 2,
                "instanceCode": "Contract_002",
                "instanceName": "客户合同_002",
                "objectName": "客户合同",
                "attributeValues": [
                    {
                        "attributeName": "状态",
                        "attributeCode": "Contract.status",
                        "value": "ended",
                    }
                ],
                "linkValues": [],
            },
            {
                "instanceId": 3,
                "instanceCode": "Contract_003",
                "instanceName": "客户合同_003",
                "objectName": "客户合同",
                "attributeValues": [
                    {
                        "attributeName": "状态",
                        "attributeCode": "Contract.status",
                        "value": "ended",
                    }
                ],
                "linkValues": [
                    {
                        "linkInfo": "负责",
                        "targetInstanceId": 4,
                        "targetInstanceName": "客户经理_009",
                    }
                ],
            },
            {
                "instanceId": 4,
                "instanceCode": "Manager_009",
                "instanceName": "客户经理_009",
                "objectName": "客户经理",
                "attributeValues": [],
                "linkValues": [],
            },
        ],
        "actionInvocations": [],
    }


def _formatted_chain():
    return {
        "ontology": {"ontologyName": "链路本体"},
        "objects": [
            {"objectId": 1, "objectName": "类型A", "objectCode": "TypeA", "attributes": []},
            {"objectId": 2, "objectName": "类型B", "objectCode": "TypeB", "attributes": []},
            {"objectId": 3, "objectName": "类型C", "objectCode": "TypeC", "attributes": []},
        ],
        "links": [
            {"sourceObjectId": 1, "targetObjectId": 2, "linkInfo": "流转到"},
            {"sourceObjectId": 2, "targetObjectId": 3, "linkInfo": "流转到"},
        ],
        "actions": [],
        "instances": [
            {
                "instanceId": 1,
                "instanceCode": "A001",
                "instanceName": "Alpha",
                "objectName": "类型A",
                "attributeValues": [],
                "linkValues": [{"linkInfo": "流转到", "targetInstanceId": 2}],
            },
            {
                "instanceId": 2,
                "instanceCode": "B001",
                "instanceName": "Bravo",
                "objectName": "类型B",
                "attributeValues": [],
                "linkValues": [{"linkInfo": "流转到", "targetInstanceId": 3}],
            },
            {
                "instanceId": 3,
                "instanceCode": "C001",
                "instanceName": "Charlie",
                "objectName": "类型C",
                "attributeValues": [],
                "linkValues": [],
            },
        ],
        "actionInvocations": [],
    }


def _formatted_disconnected():
    data = _formatted_chain()
    data["instances"] = [
        {
            "instanceId": 1,
            "instanceCode": "A001",
            "instanceName": "Alpha",
            "objectName": "类型A",
            "attributeValues": [],
            "linkValues": [],
        },
        {
            "instanceId": 2,
            "instanceCode": "B001",
            "instanceName": "Bravo",
            "objectName": "类型B",
            "attributeValues": [],
            "linkValues": [],
        },
    ]
    return data


def _formatted_star(size: int = 20):
    instances = [
        {
            "instanceId": 1,
            "instanceCode": "C001",
            "instanceName": "中心节点",
            "objectName": "中心",
            "attributeValues": [],
            "linkValues": [
                {"linkInfo": "关联", "targetInstanceId": index}
                for index in range(2, size + 2)
            ],
        }
    ]
    instances.extend(
        {
            "instanceId": index,
            "instanceCode": f"N{index:03d}",
            "instanceName": f"邻居{index:03d}",
            "objectName": "邻居",
            "attributeValues": [],
            "linkValues": [],
        }
        for index in range(2, size + 2)
    )
    return {
        "ontology": {"ontologyName": "星形本体"},
        "objects": [],
        "links": [],
        "actions": [],
        "instances": instances,
        "actionInvocations": [],
    }


def test_formatted_ontology_to_graph_prefers_instances() -> None:
    graph = formatted_ontology_to_graph(_formatted())

    assert [node.label for node in graph.nodes] == ["张三", "PO-001"]
    assert graph.ontology["graphView"] == "instance"
    assert graph.ontology["availableViews"] == ["schema", "instance"]
    assert graph.nodes[0].id == "inst:1"
    assert graph.nodes[0].type == "用户"
    assert graph.edges[0].source == "inst:1"
    assert graph.edges[0].target == "inst:2"
    assert graph.edges[0].rel == "下单"


def test_formatted_ontology_to_graph_can_return_schema_view() -> None:
    graph = formatted_ontology_to_graph(_formatted(), view="schema")

    assert [node.label for node in graph.nodes] == ["用户", "订单"]
    assert graph.ontology["graphView"] == "schema"
    assert graph.ontology["availableViews"] == ["schema", "instance"]
    assert graph.nodes[0].id == "obj:1"
    assert graph.edges[0].source == "obj:1"
    assert graph.edges[0].target == "obj:2"
    assert graph.edges[0].rel == "下单"


def test_answer_question_returns_raw_text_when_response_is_unstructured() -> None:
    response = asyncio.run(
        answer_question(
            question="张三 有哪些关系？",
            ontology=_formatted(),
            llm_client=MockLLMClient(),
        )
    )

    assert response.answer.summary == '{"entities": [], "triples": []}'
    assert response.highlight.nodes == []
    assert response.highlight.edges == []


def test_answer_question_uses_llm_highlight_ids() -> None:
    llm = MockLLMClient(
        """
        {
          "think": ["定位张三", "读取下单关系"],
          "highlight": {"nodes": ["inst:1", "inst:2"], "edges": [["inst:1", "inst:2"]]},
          "answer": {
            "summary": "张三通过下单关系关联到 PO-001。",
            "path": ["inst:1", "inst:2"],
            "edges": [["inst:1", "inst:2", "下单"]]
          }
        }
        """
    )

    response = asyncio.run(
        answer_question(question="张三 的订单是什么？", ontology=_formatted(), llm_client=llm)
    )

    assert response.answer.summary == "张三通过下单关系关联到 PO-001。"
    assert response.answer.path == ["inst:1", "inst:2"]
    assert response.answer.edges == [["inst:1", "inst:2", "下单"]]
    assert response.highlight.nodes == ["inst:1", "inst:2"]
    assert response.highlight.edges == [["inst:1", "inst:2"]]


def test_answer_question_accepts_dict_form_edges() -> None:
    # General models often emit edges as {source,target,relation} dicts. This
    # must parse (not blow up validation and dump raw JSON into summary with an
    # empty think list).
    llm = MockLLMClient(
        """
        {
          "think": ["定位张三", "读取下单关系"],
          "highlight": {"nodes": ["inst:1", "inst:2"], "edges": [["inst:1", "inst:2"]]},
          "answer": {
            "summary": "张三通过下单关系关联到 PO-001。",
            "path": ["inst:1", "inst:2"],
            "edges": [{"source": "inst:1", "target": "inst:2", "relation": "下单"}],
            "groups": [{"label": "订单", "nodes": ["inst:2"]}]
          }
        }
        """
    )
    response = asyncio.run(
        answer_question(question="张三 的订单是什么？", ontology=_formatted(), llm_client=llm)
    )
    assert response.think == ["定位张三", "读取下单关系"]
    assert response.answer.summary == "张三通过下单关系关联到 PO-001。"
    assert response.answer.edges == [["inst:1", "inst:2", "下单"]]


def test_answer_question_can_use_schema_graph_view() -> None:
    response = asyncio.run(
        answer_question(
            question="用户 和 订单 有什么关系？",
            ontology=_formatted(),
            llm_client=MockLLMClient("信息不足，无法确定。"),
            graph_view="schema",
        )
    )

    assert "用户" in response.answer.summary
    assert response.highlight.edges == [["obj:1", "obj:2"]]


def test_answer_question_direct_mode_parses_think_answer_tags() -> None:
    llm = MockLLMClient(
        """
        <think>先检查 schema，再查看 graph 中的实例关系。</think>
        <answer>张三通过下单关系关联到 PO-001。</answer>
        """
    )

    response = asyncio.run(
        answer_question(
            question="张三 的订单是什么？",
            ontology=_formatted(),
            llm_client=llm,
            context_mode="direct",
        )
    )

    assert response.think == ["先检查 schema，再查看 graph 中的实例关系。"]
    assert response.answer.summary == "张三通过下单关系关联到 PO-001。"
    assert response.highlight.edges == [["inst:1", "inst:2"]]


def test_answer_question_infers_highlight_from_model_text_without_changing_answer() -> None:
    llm = MockLLMClient(
        """
        <think>图实例中出现了下单关系。</think>
        <answer>原始回答：张三关联到 PO-001。</answer>
        """
    )

    response = asyncio.run(
        answer_question(
            question="张三 的订单是什么？",
            ontology=_formatted(),
            llm_client=llm,
            context_mode="direct",
        )
    )

    assert response.answer.summary == "原始回答：张三关联到 PO-001。"
    assert response.highlight.nodes == ["inst:1", "inst:2"]
    assert response.highlight.edges == [["inst:1", "inst:2"]]


def test_answer_question_derives_highlight_from_structured_answer() -> None:
    llm = MockLLMClient(
        """
        <think>{"reasoning_steps": [{"narrative": "已找到关联路径。"}]}</think>
        <answer>{
          "answer_status": "answerable",
          "summary": "存在一条关联路径。",
          "path": ["inst:1", "inst:2"],
          "edges": [["inst:1", "inst:2", "下单"]],
          "groups": [{"label": "补充节点", "nodes": ["inst:3"]}]
        }</answer>
        """
    )

    response = asyncio.run(
        answer_question(
            question="张三和订单有关联吗？",
            ontology=_formatted_chain(),
            llm_client=llm,
            qa_config=QAConfig(evidence_contract="graph_qa_evidence_v1"),
        )
    )

    assert response.highlight.nodes == ["inst:1", "inst:2", "inst:3"]
    assert response.highlight.edges == [["inst:1", "inst:2"]]


def test_answer_question_derives_path_edges_without_model_edges() -> None:
    llm = MockLLMClient(
        """
        {
          "think": ["已找到关联路径。"],
          "highlight": {"nodes": [], "edges": []},
          "answer": {
            "summary": "存在两跳路径。",
            "path": ["inst:1", "inst:2", "inst:3"]
          }
        }
        """
    )

    response = asyncio.run(
        answer_question(question="Alpha 到 Charlie 的路径是什么？", ontology=_formatted_chain(), llm_client=llm)
    )

    assert response.highlight.nodes == ["inst:1", "inst:2", "inst:3"]
    assert response.highlight.edges == [["inst:1", "inst:2"], ["inst:2", "inst:3"]]


def test_text_highlight_does_not_expand_all_same_relation_edges() -> None:
    llm = MockLLMClient(
        """
        <think>需要判断“负责”这类关系。</think>
        <answer>客户经理_001 执行了“结束合同”动作，使客户合同_002 的状态变为 ended。</answer>
        """
    )

    response = asyncio.run(
        answer_question(
            question="谁结束了客户合同_002？",
            ontology=_formatted_contracts(),
            llm_client=llm,
            context_mode="direct",
        )
    )

    assert response.highlight.nodes == ["inst:1", "inst:2"]
    assert response.highlight.edges == [["inst:1", "inst:2"]]


def test_text_highlight_does_not_match_generic_status_values() -> None:
    llm = MockLLMClient(
        """
        <think>定位 instanceId: 2，attributeCode: "Contract.status", value: "ended"。</think>
        <answer>客户合同_002 的状态是 ended。</answer>
        """
    )

    response = asyncio.run(
        answer_question(
            question="客户合同_002 是什么状态？",
            ontology=_formatted_contracts(),
            llm_client=llm,
            context_mode="direct",
        )
    )

    assert response.highlight.nodes == ["inst:2"]
    assert response.highlight.edges == []


def test_answer_question_direct_mode_sends_full_schema_and_graph() -> None:
    llm = CapturingLLMClient("<think>检查完整本体。</think><answer>已回答。</answer>")

    asyncio.run(
        answer_question(
            question="张三 的订单是什么？",
            ontology=_formatted(),
            llm_client=llm,
            context_mode="direct",
        )
    )

    assert "[SCHEMA]" in llm.prompt
    assert "[GRAPH]" in llm.prompt
    assert '"objects"' in llm.prompt
    assert '"links"' in llm.prompt
    assert '"instances"' in llm.prompt
    assert "张三 的订单是什么？" in llm.prompt
    assert "<think></think>" in llm.system_prompt
    assert "<answer></answer>" in llm.system_prompt


def test_answer_question_graph_rag_does_not_send_full_graph_json() -> None:
    llm = CapturingLLMClient("<think>检查证据。</think><answer>已回答。</answer>")

    asyncio.run(
        answer_question(
            question="张三 的订单是什么？",
            ontology=_formatted(),
            llm_client=llm,
            context_mode="graph_rag",
        )
    )

    assert "[QUESTION]" in llm.prompt
    assert "[RELEVANT_SCHEMA]" in llm.prompt
    assert "[EVIDENCE_SUBGRAPH]" in llm.prompt
    assert '"instances"' not in llm.prompt
    assert '"objects"' not in llm.prompt


def test_answer_question_graph_rag_matches_instance_code_seed() -> None:
    llm = CapturingLLMClient("<think>定位合同。</think><answer>已回答。</answer>")

    asyncio.run(
        answer_question(
            question="Contract_002 是什么状态？",
            ontology=_formatted_contracts(),
            llm_client=llm,
            context_mode="graph_rag",
        )
    )

    assert "Seeds:" in llm.prompt
    assert "inst:2 | 客户合同 | 客户合同_002" in llm.prompt
    assert "exact_code" in llm.prompt


def test_answer_question_graph_rag_includes_relevant_schema_for_type_seed() -> None:
    llm = CapturingLLMClient("<think>检查类型。</think><answer>已回答。</answer>")

    asyncio.run(
        answer_question(
            question="用户 和 订单 有什么关系？",
            ontology=_formatted(),
            llm_client=llm,
            graph_view="schema",
            context_mode="graph_rag",
        )
    )

    assert "ObjectType:" in llm.prompt
    assert "用户" in llm.prompt
    assert "订单" in llm.prompt
    assert "RelationType:" in llm.prompt
    assert "下单" in llm.prompt


def test_answer_question_graph_rag_keeps_downstream_direction() -> None:
    llm = CapturingLLMClient("<think>检查下游。</think><answer>已回答。</answer>")

    asyncio.run(
        answer_question(
            question="张三 的下游是什么？",
            ontology=_formatted(),
            llm_client=llm,
            context_mode="graph_rag",
        )
    )

    assert "intent=downstream" in llm.prompt
    assert "inst:1 -> inst:2 | 下单" in llm.prompt


def test_answer_question_graph_rag_keeps_shortest_path_between_seeds() -> None:
    llm = CapturingLLMClient("<think>检查路径。</think><answer>已回答。</answer>")

    asyncio.run(
        answer_question(
            question="Alpha 到 Charlie 的路径是什么？",
            ontology=_formatted_chain(),
            llm_client=llm,
            context_mode="graph_rag",
        )
    )

    assert "Paths:" in llm.prompt
    assert "inst:1 -> inst:2 -> inst:3" in llm.prompt


def test_answer_question_graph_rag_prompt_budget_and_no_seed_fallback() -> None:
    llm = CapturingLLMClient("<think>检查兜底。</think><answer>已回答。</answer>")

    asyncio.run(
        answer_question(
            question="完全不存在的问题",
            ontology=_formatted_chain(),
            llm_client=llm,
            context_mode="graph_rag",
            qa_config=QAConfig(max_prompt_chars=900, max_evidence_nodes=2, max_evidence_edges=2),
        )
    )

    assert len(llm.prompt) <= 900
    assert "无直接命中 seed" in llm.prompt
    assert "可用边端点" not in llm.prompt


def test_answer_question_graph_rag_keeps_out_of_schema_exact_seed_edges() -> None:
    llm = CapturingLLMClient("<think>检查强命中。</think><answer>已回答。</answer>")

    asyncio.run(
        answer_question(
            question="Manager_001 负责什么？",
            ontology=_formatted_contracts(),
            llm_client=llm,
            context_mode="graph_rag",
        )
    )

    assert "inst:1 | 客户经理 | 客户经理_001" in llm.prompt
    assert "inst:1 -> inst:2 | 负责" in llm.prompt


def test_answer_question_filtered_mode_does_not_append_full_graph_ids() -> None:
    llm = CapturingLLMClient('{"answer": {"summary": "ok"}}')

    asyncio.run(
        answer_question(
            question="张三 的订单是什么？",
            ontology=_formatted(),
            llm_client=llm,
            context_mode="filtered",
        )
    )

    assert "可用节点 id" not in llm.prompt
    assert "可用边端点" not in llm.prompt


def test_answer_question_can_disable_parse_fallback() -> None:
    response = asyncio.run(
        answer_question(
            question="张三 有哪些关系？",
            ontology=_formatted(),
            llm_client=MockLLMClient("not structured"),
            fallback_enabled=False,
        )
    )

    assert response.answer.summary == "not structured"
    assert response.highlight.nodes == []


def test_answer_question_falls_back_when_raw_answer_is_unanswerable() -> None:
    response = asyncio.run(
        answer_question(
            question="张三 有哪些关系？",
            ontology=_formatted(),
            llm_client=MockLLMClient("信息不足，无法确定。"),
        )
    )

    assert "parse_failed" in response.answer.summary
    assert "张三" in response.answer.summary
    assert "一跳邻域" not in response.answer.summary
    assert response.highlight.edges == [["inst:1", "inst:2"]]
    raw = json.loads(response.answer.raw or "{}")
    assert raw["fallback_reason"] == "parse_failed"
    assert "信息不足" in raw["raw_response"]


def test_answer_question_only_falls_back_for_unanswerable_answer() -> None:
    response = asyncio.run(
        answer_question(
            question="张三 有哪些关系？",
            ontology=_formatted(),
            llm_client=MockLLMClient(
                """
                {
                  "think": ["检查上下文"],
                  "highlight": {"nodes": [], "edges": []},
                  "answer": {"summary": "信息不足，无法确定。"}
                }
                """
            ),
        )
    )

    assert "model_unanswerable" in response.answer.summary
    assert "张三" in response.answer.summary
    assert response.highlight.edges == [["inst:1", "inst:2"]]
    raw = json.loads(response.answer.raw or "{}")
    assert raw["fallback_reason"] == "model_unanswerable"


def test_answer_question_fallback_limits_related_edges() -> None:
    response = asyncio.run(
        answer_question(
            question="中心节点 有哪些关系？",
            ontology=_formatted_star(20),
            llm_client=MockLLMClient("信息不足，无法确定。"),
        )
    )

    assert len(response.answer.edges) <= 10
    assert "未展开全部邻接关系" in response.answer.summary


def test_answer_question_fallback_reports_candidate_path() -> None:
    response = asyncio.run(
        answer_question(
            question="Alpha 到 Charlie 的路径是什么？",
            ontology=_formatted_chain(),
            llm_client=MockLLMClient("信息不足，无法确定。"),
        )
    )

    assert "找到一条候选连通路径" in response.answer.summary
    assert response.answer.edges == [["inst:1", "inst:2", "流转到"], ["inst:2", "inst:3", "流转到"]]
    assert response.highlight.edges == [["inst:1", "inst:2"], ["inst:2", "inst:3"]]
    raw = json.loads(response.answer.raw or "{}")
    assert raw["path_found"] is True


def test_answer_question_fallback_reports_no_path_between_matches() -> None:
    response = asyncio.run(
        answer_question(
            question="Alpha 到 Bravo 的路径是什么？",
            ontology=_formatted_disconnected(),
            llm_client=MockLLMClient("信息不足，无法确定。"),
        )
    )

    assert "未找到已定位节点之间的明确连通路径" in response.answer.summary
    raw = json.loads(response.answer.raw or "{}")
    assert raw["path_found"] is False


def test_answer_question_fallback_reports_no_matched_nodes() -> None:
    response = asyncio.run(
        answer_question(
            question="完全不存在的对象是什么？",
            ontology=_formatted(),
            llm_client=MockLLMClient("信息不足，无法确定。"),
        )
    )

    assert "parse_failed" in response.answer.summary
    assert "未在当前本体中定位到" in response.answer.summary
    raw = json.loads(response.answer.raw or "{}")
    assert raw["fallback_reason"] == "parse_failed"
    assert raw["matched_nodes"] == []
