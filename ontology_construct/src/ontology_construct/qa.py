from __future__ import annotations

import json
import re
from collections import Counter
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from ontology_construct.config import QAConfig
from ontology_construct.llm.client import (
    LLMClient,
    QA_DIRECT_SYSTEM_PROMPT,
    QA_SYSTEM_PROMPT,
)
from ontology_construct.output.formatted_ontology import FormattedOntology
from ontology_construct.qa_assembly import assemble_evidence
from ontology_construct.qa_evidence import validate_contract
from ontology_construct.qa_planner import plan_question
from ontology_construct.qa_prompt import (
    render_graph_qa_evidence_prompt,
    render_graph_rag_prompt,
)
from ontology_construct.qa_retrieval import (
    retrieve_graph_rag_context,
    retrieve_supporting_candidates,
)
from ontology_construct.qa_semantics import load_policy

GraphView = Literal["auto", "schema", "instance"]
QAContextMode = Literal["filtered", "direct", "graph_rag"]


class GraphNode(BaseModel):
    id: str
    label: str
    type: str
    attrs: dict[str, Any] = Field(default_factory=dict)
    workbench: dict[str, Any] = Field(default_factory=dict)


class GraphEdge(BaseModel):
    source: str
    target: str
    rel: str


class GraphData(BaseModel):
    ontology: dict[str, Any] = Field(default_factory=dict)
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)


class HighlightPayload(BaseModel):
    nodes: list[str] = Field(default_factory=list)
    # Accept both [source, target] and {"source","target"} on input; the parser
    # normalizes to [source, target] before the response is returned.
    edges: list[Any] = Field(default_factory=list)


class AnswerGroup(BaseModel):
    label: str
    nodes: list[str] = Field(default_factory=list)


class AnswerPayload(BaseModel):
    summary: str
    status: Literal["answerable", "insufficient_evidence"] = "answerable"
    path: list[str] = Field(default_factory=list)
    # Accept both [source, target, rel] and {"source","target","relation"} on
    # input; _normalize_edges coerces to [source, target, rel] before return.
    edges: list[Any] = Field(default_factory=list)
    groups: list[AnswerGroup] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    raw: str | None = None


class QAResponse(BaseModel):
    think: list[str] = Field(default_factory=list)
    highlight: HighlightPayload = Field(default_factory=HighlightPayload)
    answer: AnswerPayload
    meta: dict[str, Any] = Field(default_factory=dict)


FENCED_JSON_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)
TRAILING_COMMA_RE = re.compile(r",\s*([}\]])")
THINK_BLOCK_RE = re.compile(r"<think>\s*(.*?)\s*</think>", re.DOTALL)
ANSWER_BLOCK_RE = re.compile(r"<answer>\s*(.*?)\s*</answer>", re.DOTALL)
UNANSWERABLE_RE = re.compile(
    r"(无法回答|不能回答|无法确定|不能确定|信息不足|上下文不足|没有足够|缺少.*信息|未提供|找不到|无法定位)"
)


def formatted_ontology_to_graph(
    ontology: FormattedOntology | dict[str, Any],
    *,
    view: GraphView = "auto",
) -> GraphData:
    data = (
        ontology.model_dump(mode="json")
        if isinstance(ontology, FormattedOntology)
        else ontology
    )
    metadata = data.get("ontology") or {}
    objects = data.get("objects") or []
    links = data.get("links") or []
    rules = data.get("rules") or []
    actions = data.get("actions") or []
    instances = data.get("instances") or []
    objects = _objects_with_instance_references(objects, instances)

    if view == "schema":
        return _schema_graph(
            _graph_metadata(metadata, "schema", bool(instances)),
            objects,
            links,
            rules,
            actions,
        )
    if view == "instance" and not instances:
        return _schema_graph(
            _graph_metadata(metadata, "schema", False),
            objects,
            links,
            rules,
            actions,
        )
    if view == "instance" or instances:
        return _instance_graph(
            _graph_metadata(metadata, "instance", bool(instances)),
            instances,
        )
    return _schema_graph(
        _graph_metadata(metadata, "schema", False),
        objects,
        links,
        rules,
        actions,
    )


def _objects_with_instance_references(
    objects: list[dict[str, Any]],
    instances: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Backfill object-to-instance mappings for legacy/imported formatted JSON."""
    instance_ids_by_object_id: dict[Any, list[Any]] = {}
    instance_ids_by_object_name: dict[str, list[Any]] = {}
    for instance in instances:
        instance_id = instance.get("instanceId")
        if instance_id is None:
            continue
        object_id = instance.get("objectId")
        if object_id is not None:
            instance_ids_by_object_id.setdefault(object_id, []).append(instance_id)
        object_name = instance.get("objectName")
        if object_name:
            instance_ids_by_object_name.setdefault(str(object_name), []).append(instance_id)

    enriched: list[dict[str, Any]] = []
    for item in objects:
        derived_ids = instance_ids_by_object_id.get(item.get("objectId"), [])
        if not derived_ids and item.get("objectName"):
            derived_ids = instance_ids_by_object_name.get(str(item["objectName"]), [])
        instance_ids = list(
            dict.fromkeys([*(item.get("instanceIds") or []), *derived_ids])
        )
        enriched.append(
            {
                **item,
                "instanceIds": instance_ids,
                "instanceCount": len(instance_ids),
            }
        )
    return enriched


async def answer_question(
    *,
    question: str,
    ontology: FormattedOntology | dict[str, Any],
    llm_client: LLMClient,
    graph_view: GraphView = "auto",
    context_mode: QAContextMode = "graph_rag",
    fallback_enabled: bool = True,
    qa_config: QAConfig | None = None,
) -> QAResponse:
    graph = formatted_ontology_to_graph(ontology, view=graph_view)
    qa_settings = qa_config or QAConfig()
    if (
        context_mode == "graph_rag"
        and qa_settings.evidence_contract == "graph_qa_evidence_v1"
    ):
        return await _answer_graph_qa_evidence(
            question=question,
            ontology=ontology,
            graph=graph,
            llm_client=llm_client,
            graph_view=graph_view,
            qa_settings=qa_settings,
            fallback_enabled=fallback_enabled,
        )
    if context_mode == "direct":
        prompt = _render_direct_qa_prompt(question, ontology)
        system_prompt = QA_DIRECT_SYSTEM_PROMPT
    elif context_mode == "graph_rag":
        schema_graph = formatted_ontology_to_graph(ontology, view="schema")
        evidence_graph = (
            schema_graph
            if graph_view == "schema"
            else formatted_ontology_to_graph(ontology, view="instance")
        )
        retrieval = retrieve_graph_rag_context(
            question=question,
            schema_graph=schema_graph,
            instance_graph=evidence_graph,
            qa_config=qa_settings,
        )
        prompt = render_graph_rag_prompt(
            question=question,
            retrieval=retrieval,
            max_prompt_chars=qa_settings.max_prompt_chars,
            include_retrieval_trace=qa_settings.include_retrieval_trace,
        )
        system_prompt = QA_SYSTEM_PROMPT
    else:
        seed_graph = _select_context_graph(graph, question)
        prompt = _render_qa_prompt(question, graph, seed_graph)
        system_prompt = QA_SYSTEM_PROMPT
    raw_response = await llm_client.complete(prompt, system_prompt=system_prompt)
    try:
        parsed = _parse_qa_response(raw_response)
    except (ValueError, ValidationError):
        raw_summary = raw_response.strip()
        summary = raw_summary or "模型未返回有效答案。"
        if fallback_enabled and _is_unanswerable(summary):
            reason = "parse_failed" if raw_summary else "empty_response"
            return _with_qa_log_payload(
                _fallback_answer(
                    question,
                    graph,
                    reason=reason,
                    raw_response=summary,
                ),
                prompt=prompt,
                system_prompt=system_prompt,
                raw_response=raw_response,
            )
        response = QAResponse(
            think=[],
            highlight=HighlightPayload(),
            answer=AnswerPayload(summary=summary),
        )
        return _with_qa_log_payload(
            _apply_text_highlight(response, graph),
            prompt=prompt,
            system_prompt=system_prompt,
            raw_response=raw_response,
        )
    normalized = _normalize_qa_response(parsed, graph)
    if fallback_enabled and _is_unanswerable(normalized.answer.summary):
        return _with_qa_log_payload(
            _fallback_answer(
                question,
                graph,
                reason="model_unanswerable",
                raw_response=normalized.answer.summary,
            ),
            prompt=prompt,
            system_prompt=system_prompt,
            raw_response=raw_response,
        )
    return _with_qa_log_payload(
        _apply_text_highlight(normalized, graph),
        prompt=prompt,
        system_prompt=system_prompt,
        raw_response=raw_response,
    )


async def _answer_graph_qa_evidence(
    *,
    question: str,
    ontology: FormattedOntology | dict[str, Any],
    graph: GraphData,
    llm_client: LLMClient,
    graph_view: GraphView,
    qa_settings: QAConfig,
    fallback_enabled: bool,
) -> QAResponse:
    """graph_qa_evidence_v1 QA path (§10.2)."""
    schema_graph = formatted_ontology_to_graph(ontology, view="schema")
    instance_graph = (
        schema_graph
        if graph_view == "schema"
        else formatted_ontology_to_graph(ontology, view="instance")
    )
    plan_result = plan_question(
        question=question,
        instance_graph=instance_graph,
        schema_graph=schema_graph,
        cfg=qa_settings,
    )
    anchor_id_list = [entity.node.id for entity in plan_result.plan.linked_entities]
    support_nodes, support_edges = retrieve_supporting_candidates(
        anchor_ids=anchor_id_list,
        instance_graph=instance_graph,
        schema_graph=schema_graph,
        qa_config=qa_settings,
    )
    evidence = assemble_evidence(
        question=question,
        plan_result=plan_result,
        instance_graph=instance_graph,
        schema_graph=schema_graph,
        policy=load_policy(None),
        cfg=qa_settings,
        supporting_nodes=support_nodes,
        supporting_edges=support_edges,
    )
    prompt = render_graph_qa_evidence_prompt(evidence, qa_settings)
    violations = validate_contract(evidence, prompt, qa_settings)
    meta: dict[str, Any] = {
        "contract_version": evidence.contract_version,
        "mode": evidence.plan.mode,
        "intent": evidence.plan.intent,
        "linked_entities": [
            {
                "mention": entity.mention,
                "id": entity.node.id,
                "type": entity.node.type,
                "label": entity.node.label,
            }
            for entity in evidence.plan.linked_entities
        ],
        "unlinked_mentions": evidence.plan.unlinked_mentions,
        "core_item_count": len(evidence.core),
        "supporting_node_count": len(evidence.supporting_nodes),
        "supporting_edge_count": len(evidence.supporting_edges),
        "prompt_chars": len(prompt),
        "conformance_violations": violations,
    }
    if qa_settings.include_retrieval_trace:
        meta["evidence_meta"] = evidence.meta

    raw_response = await llm_client.complete(prompt, system_prompt=QA_DIRECT_SYSTEM_PROMPT)
    response = _parse_evidence_response(
        raw_response,
        graph,
        fallback_enabled=fallback_enabled,
        question=question,
    )
    response.meta = meta
    return _with_qa_log_payload(
        response,
        prompt=prompt,
        system_prompt=QA_DIRECT_SYSTEM_PROMPT,
        raw_response=raw_response,
    )


def _with_qa_log_payload(
    response: QAResponse,
    *,
    prompt: str,
    system_prompt: str,
    raw_response: str,
) -> QAResponse:
    response.meta = {
        **(response.meta or {}),
        "_qa_log": {
            "prompt": prompt,
            "system_prompt": system_prompt,
            "raw_answer": raw_response,
            "prompt_chars": len(prompt),
            "raw_answer_chars": len(raw_response or ""),
        },
    }
    return response


def _parse_evidence_response(
    raw_response: str,
    graph: GraphData,
    *,
    fallback_enabled: bool,
    question: str,
) -> QAResponse:
    """Parse a graph_qa_evidence_v1 completion (<think></think><answer>JSON</answer>).

    ``answer_status=insufficient_evidence`` is a first-class answer and never
    routes to the heuristic fallback (§3.8).
    """
    text = (raw_response or "").strip()
    think_match = THINK_BLOCK_RE.search(text)
    think = _extract_think_steps(think_match.group(1).strip()) if think_match else []
    answer_match = ANSWER_BLOCK_RE.search(text)
    answer_text = answer_match.group(1).strip() if answer_match else text

    payload: Any = None
    try:
        payload = _parse_json_blob(answer_text)
    except (json.JSONDecodeError, ValueError):
        payload = None

    if isinstance(payload, dict):
        # New contract answer JSON: flat object with answer_status/summary.
        if "answer_status" in payload or (
            "summary" in payload and "answer" not in payload
        ):
            response = QAResponse(
                think=think,
                highlight=HighlightPayload(),
                answer=_answer_from_evidence_json(payload),
            )
            normalized = _normalize_qa_response(response, graph)
            return _apply_text_highlight(normalized, graph)
        # A general model may still emit the legacy {think, highlight, answer}
        # shape even on this path — parse it with the legacy parser rather than
        # dumping raw JSON into summary.
        if isinstance(payload.get("answer"), dict):
            try:
                legacy = _parse_qa_response(raw_response)
                normalized = _normalize_qa_response(legacy, graph)
                return _apply_text_highlight(normalized, graph)
            except (ValueError, ValidationError):
                pass

    # No structured answer JSON — treat as a plain text answer, with fallback.
    summary = answer_text or "模型未返回有效答案。"
    if fallback_enabled and (not answer_text or _is_unanswerable(summary)):
        reason = "empty_response" if not answer_text else "parse_failed"
        return _fallback_answer(question, graph, reason=reason, raw_response=summary)
    response = QAResponse(
        think=think,
        highlight=HighlightPayload(),
        answer=AnswerPayload(summary=summary),
    )
    return _apply_text_highlight(response, graph)


def _answer_from_evidence_json(data: dict[str, Any]) -> AnswerPayload:
    status = data.get("answer_status") or data.get("status") or "answerable"
    if status not in ("answerable", "insufficient_evidence"):
        status = "answerable"
    groups: list[AnswerGroup] = []
    for group in data.get("groups") or []:
        if isinstance(group, dict):
            groups.append(
                AnswerGroup(
                    label=str(group.get("label", "")),
                    nodes=[str(node) for node in group.get("nodes") or []],
                )
            )
    edges = [
        edge for edge in data.get("edges") or [] if isinstance(edge, (list, tuple, dict))
    ]
    return AnswerPayload(
        summary=str(data.get("summary", "")),
        status=status,
        path=[str(node) for node in data.get("path") or []],
        edges=edges,
        groups=groups,
        missing=[str(item) for item in data.get("missing") or []],
    )


def _graph_metadata(
    metadata: dict[str, Any],
    view: Literal["schema", "instance"],
    has_instances: bool,
) -> dict[str, Any]:
    enriched = dict(metadata)
    enriched["graphView"] = view
    enriched["availableViews"] = ["schema", "instance"] if has_instances else ["schema"]
    return enriched


def _instance_graph(metadata: dict[str, Any], instances: list[dict[str, Any]]) -> GraphData:
    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []
    id_by_instance_id: dict[int, str] = {}

    for instance in instances:
        instance_id = instance.get("instanceId")
        if instance_id is None:
            continue
        node_id = f"inst:{instance_id}"
        id_by_instance_id[int(instance_id)] = node_id
        attrs = {
            "对象类型": instance.get("objectName"),
            "实例编码": instance.get("instanceCode"),
        }
        for value in instance.get("attributeValues") or []:
            name = value.get("attributeName")
            if name:
                attrs[str(name)] = value.get("value")
        nodes.append(
            GraphNode(
                id=node_id,
                label=str(instance.get("instanceName") or node_id),
                type=str(instance.get("objectName") or "Instance"),
                attrs={k: v for k, v in attrs.items() if v is not None},
            )
        )

    for instance in instances:
        source_id = id_by_instance_id.get(instance.get("instanceId"))
        if not source_id:
            continue
        for link in instance.get("linkValues") or []:
            target_id = id_by_instance_id.get(link.get("targetInstanceId"))
            if not target_id:
                continue
            edges.append(
                GraphEdge(
                    source=source_id,
                    target=target_id,
                    rel=str(link.get("linkInfo") or "related_to"),
                )
            )

    return GraphData(ontology=metadata, nodes=nodes, edges=edges)


def _schema_graph(
    metadata: dict[str, Any],
    objects: list[dict[str, Any]],
    links: list[dict[str, Any]],
    rules: list[dict[str, Any]] | None = None,
    actions: list[dict[str, Any]] | None = None,
) -> GraphData:
    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []
    id_by_object_id: dict[int, str] = {}

    for item in objects:
        object_id = item.get("objectId")
        if object_id is None:
            continue
        node_id = f"obj:{object_id}"
        id_by_object_id[int(object_id)] = node_id
        attrs = {
            "对象编码": item.get("objectCode"),
            "说明": item.get("description"),
        }
        for attribute in item.get("attributes") or []:
            name = attribute.get("attributeName")
            if name:
                attrs[str(name)] = attribute.get("sampleValue")
        nodes.append(
            GraphNode(
                id=node_id,
                label=str(item.get("objectName") or node_id),
                type=str(item.get("objectName") or "Object"),
                attrs={k: v for k, v in attrs.items() if v not in (None, "")},
                workbench={
                    "kind": "object",
                    "origin": "auto",
                    "details": {
                        "code": item.get("objectCode") or node_id,
                        "description": item.get("description") or "",
                        "status": "draft",
                        "sourceChunks": item.get("sourceChunks") or [],
                        "instanceIds": item.get("instanceIds") or [],
                        "instanceCount": item.get("instanceCount") or 0,
                    },
                },
            )
        )

    for link in links:
        source_id = id_by_object_id.get(link.get("sourceObjectId"))
        target_id = id_by_object_id.get(link.get("targetObjectId"))
        if source_id and target_id:
            edges.append(
                GraphEdge(
                    source=source_id,
                    target=target_id,
                    rel=str(link.get("linkInfo") or "related_to"),
                )
            )

    id_by_rule_id: dict[int, str] = {}
    for item in rules or []:
        rule_id = item.get("ruleId")
        if rule_id is None:
            continue
        node_id = f"rule:{rule_id}"
        id_by_rule_id[int(rule_id)] = node_id
        scope_id = id_by_object_id.get(item.get("scopeObjectId"))
        nodes.append(
            GraphNode(
                id=node_id,
                label=str(item.get("ruleName") or node_id),
                type="Rule",
                attrs={
                    "规则编码": item.get("ruleCode"),
                    "说明": item.get("description"),
                    "__workbenchKind": "rule",
                },
                workbench={
                    "kind": "rule",
                    "origin": "auto",
                    "details": {
                        "code": item.get("ruleCode") or node_id,
                        "description": item.get("description") or "",
                        "status": "draft",
                        "logic": item.get("logic") or "AND",
                        "scopeObjectId": scope_id or "",
                        "sourceChunks": item.get("sourceChunks") or [],
                        "conditions": [
                            {
                                "id": condition.get("conditionId"),
                                "field": condition.get("field"),
                                "operator": condition.get("operator"),
                                "value": condition.get("value"),
                                "valueType": condition.get("valueType") or "string",
                            }
                            for condition in item.get("conditions") or []
                        ],
                    },
                },
            )
        )
        if scope_id:
            edges.append(GraphEdge(source=scope_id, target=node_id, rel="适用规则"))

    for item in actions or []:
        action_id = item.get("actionId")
        if action_id is None:
            continue
        node_id = f"action:{action_id}"
        trigger_rule_ids = [
            id_by_rule_id[rule_id]
            for rule_id in item.get("triggerRuleIds") or []
            if rule_id in id_by_rule_id
        ]
        target_ids = [
            id_by_object_id[object_id]
            for object_id in item.get("targetObjectIds") or []
            if object_id in id_by_object_id
        ]
        if not target_ids:
            target_ids = [
                object_node_id
                for name in item.get("targetObjects") or []
                for object_item in objects
                if object_item.get("objectName") == name
                if (
                    object_node_id := id_by_object_id.get(object_item.get("objectId"))
                )
            ]
        nodes.append(
            GraphNode(
                id=node_id,
                label=str(item.get("actionName") or node_id),
                type="Action",
                attrs={
                    "行动编码": item.get("actionCode"),
                    "说明": item.get("description"),
                    "__workbenchKind": "action",
                },
                workbench={
                    "kind": "action",
                    "origin": "auto",
                    "details": {
                        "code": item.get("actionCode") or node_id,
                        "description": item.get("description") or "",
                        "status": "draft",
                        "actionType": item.get("actionType") or "ontology",
                        "triggerRuleId": trigger_rule_ids[0] if trigger_rule_ids else "",
                        "targetObjectId": target_ids[0] if target_ids else "",
                        "executor": item.get("executor") or "",
                        "actionPlan": item.get("actionPlan") or "",
                        "feedback": item.get("feedback") or "",
                        "sourceChunks": item.get("sourceChunks") or [],
                        "inputObjects": item.get("inputObjects") or [],
                        "outputObjects": item.get("outputObjects") or [],
                    },
                },
            )
        )
        for rule_node_id in trigger_rule_ids:
            edges.append(GraphEdge(source=rule_node_id, target=node_id, rel="触发"))
        for target_id in target_ids:
            edges.append(GraphEdge(source=node_id, target=target_id, rel="作用于"))

    return GraphData(ontology=metadata, nodes=nodes, edges=edges)


def _select_context_graph(graph: GraphData, question: str) -> GraphData:
    seed_ids = _match_nodes(graph, question)
    if not seed_ids:
        return GraphData(
            ontology=graph.ontology,
            nodes=graph.nodes[:120],
            edges=graph.edges[:180],
        )

    selected = set(seed_ids)
    selected_edges: list[GraphEdge] = []
    for _ in range(2):
        changed = False
        for edge in graph.edges:
            if edge.source in selected or edge.target in selected:
                if edge not in selected_edges:
                    selected_edges.append(edge)
                before = len(selected)
                selected.add(edge.source)
                selected.add(edge.target)
                changed = changed or len(selected) != before
        if not changed:
            break

    node_by_id = {node.id: node for node in graph.nodes}
    nodes = [node_by_id[node_id] for node_id in selected if node_id in node_by_id]
    return GraphData(
        ontology=graph.ontology,
        nodes=nodes[:120],
        edges=selected_edges[:180],
    )


def _render_qa_prompt(question: str, graph: GraphData, context_graph: GraphData) -> str:
    lines = [f"问题：{question}", ""]

    # Ontology metadata
    meta = graph.ontology
    if meta.get("ontologyName"):
        lines.append(f"本体名称：{meta['ontologyName']}")
    if meta.get("ontologyDesc"):
        lines.append(f"本体描述：{meta['ontologyDesc']}")
    lines.append("")

    # Nodes as text table
    lines.append("节点列表：")
    for node in context_graph.nodes:
        attrs = ", ".join(
            f"{k}={v}" for k, v in node.attrs.items() if v is not None
        )
        parts = [node.id, node.label, node.type]
        if attrs:
            parts.append(attrs)
        lines.append(f"- {' | '.join(parts)}")
    lines.append("")

    # Edges as text
    lines.append("关系列表：")
    for edge in context_graph.edges:
        lines.append(f"- {edge.source} -> {edge.target} ({edge.rel})")
    lines.append("")

    return "\n".join(lines)


def _render_direct_qa_prompt(
    question: str,
    ontology: FormattedOntology | dict[str, Any],
) -> str:
    data = (
        ontology.model_dump(mode="json")
        if isinstance(ontology, FormattedOntology)
        else ontology
    )
    schema = {
        "ontology": data.get("ontology") or {},
        "objects": data.get("objects") or [],
        "links": data.get("links") or [],
        "rules": data.get("rules") or [],
        "actions": data.get("actions") or [],
    }
    graph = {
        "instances": data.get("instances") or [],
        "actionInvocations": data.get("actionInvocations") or [],
    }
    return "\n".join(
        [
            "[SCHEMA]",
            json.dumps(schema, ensure_ascii=False, indent=2),
            "",
            "[GRAPH]",
            json.dumps(graph, ensure_ascii=False, indent=2),
            "",
            f"问题：{question}",
        ]
    )


def _parse_qa_response(raw_response: str) -> QAResponse:
    """Parse the LLM response, supporting two formats:

    1. Jiutian native: <think>...</think> followed by a JSON object with highlight and answer.
    2. Standard: a single JSON object with think, highlight, answer fields.
    """
    text = raw_response.strip()

    answer_match = ANSWER_BLOCK_RE.search(text)
    if answer_match:
        think_match = THINK_BLOCK_RE.search(text)
        think = (
            _extract_think_steps(think_match.group(1).strip())
            if think_match
            else []
        )
        return QAResponse(
            think=think,
            highlight=HighlightPayload(),
            answer=AnswerPayload(summary=answer_match.group(1).strip()),
        )

    # Try Jiutian native format: <think>...</think> + JSON
    think_match = THINK_BLOCK_RE.search(text)
    if think_match:
        think_text = think_match.group(1).strip()
        think_steps = _extract_think_steps(think_text)
        # Extract JSON after </think>
        after_think = text[think_match.end():].strip()
        if after_think:
            try:
                data = _parse_json_blob(after_think)
                response = QAResponse.model_validate(data)
                response.think = think_steps
                return response
            except (json.JSONDecodeError, ValidationError):
                pass

    # Try standard format: single JSON object
    try:
        data = _parse_json_blob(text)
        return QAResponse.model_validate(data)
    except (json.JSONDecodeError, ValidationError):
        pass

    raise ValueError("Failed to parse QA response")


def _parse_json_blob(text: str) -> dict:
    """Extract and parse a JSON object from text."""
    candidate = text.strip()
    fenced = FENCED_JSON_RE.search(candidate)
    if fenced:
        candidate = fenced.group(1).strip()
    elif not candidate.startswith("{"):
        candidate = _extract_first_json_object(candidate)
    candidate = TRAILING_COMMA_RE.sub(r"\1", candidate)
    return json.loads(candidate)


def _extract_think_steps(think_text: str) -> list[str]:
    """Extract reasoning steps from Jiutian's <think> block.

    The think block contains a JSON with reasoning_steps, each having a narrative field.
    """
    try:
        data = _parse_json_blob(think_text)
        steps = data.get("reasoning_steps") or []
        return [s.get("narrative", "") for s in steps if s.get("narrative")]
    except (json.JSONDecodeError, KeyError):
        pass
    # Fallback: treat the entire think block as a single step
    return [think_text[:500]]


def _extract_first_json_object(text: str) -> str:
    start = text.find("{")
    if start < 0:
        return text

    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return text[start:]


def _normalize_qa_response(
    response: QAResponse,
    graph: GraphData,
) -> QAResponse:
    node_ids = {node.id for node in graph.nodes}
    edge_pairs = {(edge.source, edge.target) for edge in graph.edges}
    label_to_id = _node_label_index(graph)

    def resolve_node(value: str) -> str | None:
        if value in node_ids:
            return value
        return label_to_id.get(value)

    highlight_nodes = [
        resolved
        for node in response.highlight.nodes
        if (resolved := resolve_node(str(node)))
    ]
    highlight_edges = _normalize_edges(response.highlight.edges, edge_pairs, label_to_id)
    path = [
        resolved
        for node in response.answer.path
        if (resolved := resolve_node(str(node)))
    ]
    answer_edges = _normalize_edges(response.answer.edges, edge_pairs, label_to_id, keep_rel=True)
    groups = [
        AnswerGroup(
            label=group.label,
            nodes=[
                resolved
                for node in group.nodes
                if (resolved := resolve_node(str(node)))
            ],
        )
        for group in response.answer.groups
    ]
    groups = [group for group in groups if group.nodes]

    response.highlight = HighlightPayload(
        nodes=list(dict.fromkeys(highlight_nodes)),
        edges=highlight_edges,
    )
    response.answer.path = list(dict.fromkeys(path))
    response.answer.edges = answer_edges
    response.answer.groups = groups
    return response


def _is_unanswerable(summary: str) -> bool:
    return bool(UNANSWERABLE_RE.search(summary or ""))


def _apply_text_highlight(response: QAResponse, graph: GraphData) -> QAResponse:
    if response.highlight.nodes or response.highlight.edges:
        return response
    structured_highlight = _highlight_from_answer(response.answer, graph)
    if structured_highlight.nodes or structured_highlight.edges:
        response.highlight = structured_highlight
        return response
    text = "\n".join(
        [
            *response.think,
            response.answer.summary,
            response.answer.raw or "",
        ]
    )
    response.highlight = _highlight_from_text(text, graph)
    return response


def _highlight_from_answer(answer: AnswerPayload, graph: GraphData) -> HighlightPayload:
    node_ids = {node.id for node in graph.nodes}
    edge_pairs = {(edge.source, edge.target) for edge in graph.edges}
    nodes: list[str] = []
    edges: list[list[str]] = []

    def add_node(node_id: str) -> None:
        if node_id in node_ids and node_id not in nodes:
            nodes.append(node_id)

    for node_id in answer.path:
        add_node(node_id)
    for group in answer.groups:
        for node_id in group.nodes:
            add_node(node_id)
    for edge in answer.edges:
        if not isinstance(edge, (list, tuple)) or len(edge) < 2:
            continue
        source, target = str(edge[0]), str(edge[1])
        if (source, target) not in edge_pairs:
            continue
        edges.append([source, target])
        add_node(source)
        add_node(target)

    # A normalized path without explicit edges still identifies a graph route.
    if not edges:
        for source, target in zip(answer.path, answer.path[1:]):
            for edge in _shortest_edge_path(graph, source, target):
                edges.append(edge)
                add_node(edge[0])
                add_node(edge[1])

    return HighlightPayload(nodes=nodes, edges=_dedupe_nested(edges))


def _highlight_from_text(text: str, graph: GraphData) -> HighlightPayload:
    if not text.strip():
        return HighlightPayload()
    core_nodes = _match_nodes_in_text(graph, text)
    if not core_nodes:
        return HighlightPayload()

    path_nodes = list(core_nodes)
    path_edges: list[list[str]] = []
    core_set = set(core_nodes)

    for edge in graph.edges:
        if edge.source in core_set and edge.target in core_set:
            path_edges.append([edge.source, edge.target])

    for source, target in zip(core_nodes, core_nodes[1:]):
        path = _shortest_edge_path(graph, source, target)
        if not path:
            continue
        for edge in path:
            path_edges.append(edge)
            for node_id in edge[:2]:
                if node_id not in path_nodes:
                    path_nodes.append(node_id)

    return HighlightPayload(nodes=path_nodes, edges=_dedupe_nested(path_edges))


def _match_nodes_in_text(graph: GraphData, text: str) -> list[str]:
    normalized_text = text.casefold()
    positions: list[tuple[int, str]] = []
    positions.extend(_explicit_graph_id_positions(graph, text))
    for node in graph.nodes:
        for candidate in _node_highlight_candidates(node):
            position = _candidate_position(candidate, normalized_text)
            if position >= 0:
                positions.append((position, node.id))
                break
    ordered: list[str] = []
    for _position, node_id in sorted(positions, key=lambda item: item[0]):
        if node_id not in ordered:
            ordered.append(node_id)
    return ordered


def _explicit_graph_id_positions(graph: GraphData, text: str) -> list[tuple[int, str]]:
    node_ids = {node.id for node in graph.nodes}
    positions: list[tuple[int, str]] = []
    scalar_patterns = [
        (r'["\']?(?:instanceId|targetInstanceId)["\']?\s*[:：]\s*(\d+)', "inst"),
        (
            r'["\']?(?:objectId|sourceObjectId|targetObjectId)["\']?\s*[:：]\s*(\d+)',
            "obj",
        ),
    ]
    for pattern, prefix in scalar_patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            node_id = f"{prefix}:{match.group(1)}"
            if node_id in node_ids:
                positions.append((match.start(), node_id))

    list_patterns = [
        (r'["\']?(?:inputInstances|outputInstances)["\']?\s*[:：]\s*\[([^\]]*)\]', "inst"),
        (
            r'["\']?(?:sourceObjectIds|targetObjectIds)["\']?\s*[:：]\s*\[([^\]]*)\]',
            "obj",
        ),
    ]
    for pattern, prefix in list_patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            for number in re.finditer(r"\d+", match.group(1)):
                node_id = f"{prefix}:{number.group(0)}"
                if node_id in node_ids:
                    positions.append((match.start() + number.start(), node_id))
    return positions


def _node_highlight_candidates(node: GraphNode) -> list[str]:
    candidates = [node.id, node.label]
    for key, value in node.attrs.items():
        if value in (None, ""):
            continue
        key_text = str(key)
        if key_text in {"实例编码", "对象编码"} or key_text.casefold().endswith("code"):
            candidates.append(str(value))
    return candidates


def _candidate_position(candidate: str, normalized_text: str) -> int:
    value = str(candidate or "").strip()
    if len(value) < 2:
        return -1
    return normalized_text.find(value.casefold())


def _shortest_edge_path(
    graph: GraphData,
    source: str,
    target: str,
    *,
    max_depth: int = 4,
) -> list[list[str]]:
    if source == target:
        return []
    adjacency: dict[str, list[tuple[str, list[str]]]] = {}
    for edge in graph.edges:
        item = [edge.source, edge.target]
        adjacency.setdefault(edge.source, []).append((edge.target, item))
        adjacency.setdefault(edge.target, []).append((edge.source, item))

    queue: list[tuple[str, list[list[str]]]] = [(source, [])]
    visited = {source}
    while queue:
        node_id, path = queue.pop(0)
        if len(path) >= max_depth:
            continue
        for next_id, edge in adjacency.get(node_id, []):
            if next_id in visited:
                continue
            next_path = [*path, edge]
            if next_id == target:
                return next_path
            visited.add(next_id)
            queue.append((next_id, next_path))
    return []


def _normalize_edges(
    values: list[list[str]],
    edge_pairs: set[tuple[str, str]],
    label_to_id: dict[str, str],
    *,
    keep_rel: bool = False,
) -> list[list[str]]:
    normalized: list[list[str]] = []
    for item in values:
        if isinstance(item, dict):
            source = str(item.get("source", ""))
            target = str(item.get("target", ""))
            rel = str(item.get("relation_type", item.get("rel", item.get("relation", ""))))
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            source = str(item[0])
            target = str(item[1])
            rel = str(item[2]) if len(item) > 2 else ""
        else:
            continue
        if not source or not target:
            continue
        source_id = source if any(source in pair for pair in edge_pairs) else label_to_id.get(source)
        target_id = target if any(target in pair for pair in edge_pairs) else label_to_id.get(target)
        if not source_id or not target_id:
            continue
        pair = (source_id, target_id)
        reverse_pair = (target_id, source_id)
        if pair not in edge_pairs and reverse_pair in edge_pairs:
            source_id, target_id = target_id, source_id
        elif pair not in edge_pairs:
            continue
        if keep_rel:
            normalized.append([source_id, target_id, rel])
        else:
            normalized.append([source_id, target_id])
    return _dedupe_nested(normalized)


def _fallback_answer(
    question: str,
    graph: GraphData,
    *,
    reason: str,
    raw_response: str | None = None,
    max_nodes: int = 10,
    max_edges: int = 10,
) -> QAResponse:
    matched = _match_nodes(graph, question)
    matched_set = set(matched)
    related_edges: list[GraphEdge] = []
    neighbor_ids: list[str] = []
    for edge in graph.edges:
        if edge.source in matched_set or edge.target in matched_set:
            related_edges.append(edge)
            for node_id in (edge.source, edge.target):
                if node_id not in matched_set and node_id not in neighbor_ids:
                    neighbor_ids.append(node_id)

    path_edges = _fallback_path_edges(graph, matched)
    path_found = bool(path_edges)
    relation_counts = Counter(edge.rel for edge in related_edges)
    limited_node_ids = list(dict.fromkeys([*matched, *neighbor_ids]))[:max_nodes]
    limited_edges = (
        [GraphEdge(source=edge[0], target=edge[1], rel=_edge_rel(graph, edge[0], edge[1])) for edge in path_edges]
        if path_edges
        else related_edges[:max_edges]
    )
    answer_edges = [[edge.source, edge.target, edge.rel] for edge in limited_edges[:max_edges]]
    highlight_edges = [[edge.source, edge.target] for edge in limited_edges[:max_edges]]
    highlight_nodes = list(
        dict.fromkeys(
            [
                *limited_node_ids,
                *[node_id for edge in highlight_edges for node_id in edge],
            ]
        )
    )[:max_nodes]

    raw_payload = {
        "fallback_reason": reason,
        "raw_response": _truncate_text(raw_response or "", 500),
        "matched_nodes": matched,
        "path_found": path_found,
        "relation_counts": dict(relation_counts.most_common(10)),
    }

    if not matched:
        summary = (
            f"模型未能基于当前证据生成可靠答案（原因：{reason}）。"
            "同时未在当前本体中定位到与问题直接匹配的节点或属性。"
            f"当前本体包含 {len(graph.nodes)} 个节点、{len(graph.edges)} 条关系；"
            "可以使用图中节点名称、实例名称或关系名继续追问。"
        )
        return QAResponse(
            think=[
                f"模型未能生成可靠答案，进入降级模式（原因：{reason}）。",
                "按问题关键词匹配节点名称、类型和属性值。",
                "未找到可稳定定位的图谱证据，返回边界说明。",
            ],
            highlight=HighlightPayload(),
            answer=AnswerPayload(summary=summary, raw=json.dumps(raw_payload, ensure_ascii=False)),
        )

    labels = [_node_label(graph, node_id) for node_id in matched]
    if path_found:
        path_summary = "路径检查：找到一条候选连通路径。"
    elif len(matched) >= 2:
        path_summary = "路径检查：未找到已定位节点之间的明确连通路径。"
    else:
        path_summary = "路径检查：相关节点不足两个，未进行连通路径检查。"
    relation_summary = "、".join(
        f"{rel}({count})" for rel, count in relation_counts.most_common(5)
    ) or "无"
    summary = (
        f"模型未能基于当前证据生成可靠答案（原因：{reason}）。"
        f"已定位到 {len(matched)} 个相关节点：{'、'.join(labels)}。"
        f"{path_summary}"
        f"相关关系类型摘要：{relation_summary}。"
        "为避免误导，仅返回少量相关证据摘要；未展开全部邻接关系。"
    )
    return QAResponse(
        think=[
            f"模型未能生成可靠答案，进入降级模式（原因：{reason}）。",
            "按问题中的实体名称、节点 id、类型和属性值定位相关节点。",
            "返回少量相关证据摘要，并在多节点命中时尝试候选路径检查。",
        ],
        highlight=HighlightPayload(nodes=highlight_nodes, edges=highlight_edges),
        answer=AnswerPayload(
            summary=summary,
            groups=[AnswerGroup(label="相关节点", nodes=limited_node_ids)],
            edges=answer_edges,
            raw=json.dumps(raw_payload, ensure_ascii=False),
        ),
    )


def _fallback_path_edges(
    graph: GraphData,
    matched: list[str],
    *,
    max_depth: int = 6,
) -> list[list[str]]:
    if len(matched) < 2:
        return []
    for source_index, source in enumerate(matched):
        for target in matched[source_index + 1 :]:
            path = _shortest_edge_path(graph, source, target, max_depth=max_depth)
            if path:
                return path
    return []


def _edge_rel(graph: GraphData, source: str, target: str) -> str:
    for edge in graph.edges:
        if edge.source == source and edge.target == target:
            return edge.rel
        if edge.source == target and edge.target == source:
            return edge.rel
    return "related_to"


def _truncate_text(text: str, limit: int) -> str:
    value = str(text or "")
    return value if len(value) <= limit else value[:limit]


def _match_nodes(graph: GraphData, question: str) -> list[str]:
    normalized_question = question.casefold()
    terms = [
        term.casefold()
        for term in re.split(r"[^\w\u4e00-\u9fff]+", question)
        if len(term.strip()) >= 2
    ]
    matches: list[str] = []
    for node in graph.nodes:
        haystack = " ".join(
            [
                node.id,
                node.label,
                node.type,
                *[str(value) for value in node.attrs.values() if value is not None],
            ]
        ).casefold()
        if node.id.casefold() in normalized_question or node.label.casefold() in normalized_question:
            matches.append(node.id)
            continue
        if terms and any(term in haystack for term in terms):
            matches.append(node.id)
    return list(dict.fromkeys(matches))


def _node_label_index(graph: GraphData) -> dict[str, str]:
    index: dict[str, str] = {}
    for node in graph.nodes:
        index[node.label] = node.id
        index[node.type] = node.id
    return index


def _node_label(graph: GraphData, node_id: str) -> str:
    for node in graph.nodes:
        if node.id == node_id:
            return node.label
    return node_id


def _dedupe_nested(items: list[list[str]]) -> list[list[str]]:
    seen: set[tuple[str, ...]] = set()
    deduped: list[list[str]] = []
    for item in items:
        key = tuple(item)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped
