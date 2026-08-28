from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import uuid5, NAMESPACE_URL

from ontology_construct.config import InstancePostprocessConfig
from ontology_construct.models import (
    ActionInvocation,
    GlobalOntology,
    InstanceLinkValue,
    OntologyAction,
    OntologyInstance,
)


class InstanceRepairLLM(Protocol):
    async def complete(self, prompt: str, *, system_prompt: str | None = None) -> str:
        """Return raw LLM output."""


@dataclass(frozen=True)
class InstancePostprocessStats:
    enabled: bool
    total_instances: int
    isolated_before: int
    isolated_after: int
    llm_candidate_count: int = 0
    llm_links_added: int = 0
    fallback_links_added: int = 0
    instances_dropped: int = 0
    actions_added: int = 0
    skipped_reason: str = ""

    def as_dict(self) -> dict[str, int | str | bool]:
        return {
            "enabled": self.enabled,
            "totalInstances": self.total_instances,
            "isolatedBefore": self.isolated_before,
            "isolatedAfter": self.isolated_after,
            "llmCandidateCount": self.llm_candidate_count,
            "llmLinksAdded": self.llm_links_added,
            "fallbackLinksAdded": self.fallback_links_added,
            "instancesDropped": self.instances_dropped,
            "actionsAdded": self.actions_added,
            "skippedReason": self.skipped_reason,
        }


def _is_degenerate_name(name: str) -> bool:
    """True for extraction-noise names with no real content (e.g. a lone "|"
    from a markdown table separator) — no alphanumeric and no CJK character."""
    text = (name or "").strip()
    return not any(ch.isalnum() or "一" <= ch <= "鿿" for ch in text)


def drop_degenerate_instances(ontology: GlobalOntology) -> tuple[GlobalOntology, int]:
    """Deterministically remove instances whose name is extraction noise, and
    prune any link that referenced them. Runs regardless of the LLM repair so the
    graph never carries garbage-named nodes (which downstream anchor selection can
    otherwise fixate on)."""
    degenerate_ids = {
        instance.id for instance in ontology.instances if _is_degenerate_name(instance.name)
    }
    if not degenerate_ids:
        return ontology, 0
    working = ontology.model_copy(deep=True)
    kept: list[OntologyInstance] = []
    for instance in working.instances:
        if instance.id in degenerate_ids:
            continue
        instance.link_values = [
            link for link in instance.link_values if link.target_instance_id not in degenerate_ids
        ]
        kept.append(instance)
    working.instances = kept
    return working, len(degenerate_ids)


async def repair_instance_connectivity(
    ontology: GlobalOntology,
    llm_client: InstanceRepairLLM,
    config: InstancePostprocessConfig,
) -> tuple[GlobalOntology, InstancePostprocessStats]:
    """Remove or connect isolated instances after extraction.

    The LLM only proposes how to handle existing isolated instances. Every id is
    validated against the current ontology and any still-isolated instance is
    connected by a deterministic fallback so the final graph is usable for demos.
    """
    # Deterministic hygiene: drop extraction-noise instances before anything else.
    ontology, degenerate_dropped = drop_degenerate_instances(ontology)

    if not config.enabled:
        return ontology, _stats(
            ontology, enabled=False, skipped_reason="disabled", instances_dropped=degenerate_dropped
        )
    if len(ontology.instances) < config.min_instances:
        return ontology, _stats(
            ontology, skipped_reason="below_min_instances", instances_dropped=degenerate_dropped
        )

    isolated = _isolated_instance_ids(ontology)
    if not isolated:
        return ontology, _stats(
            ontology, skipped_reason="no_isolated_instances", instances_dropped=degenerate_dropped
        )
    if len(ontology.instances) <= 1:
        return ontology, _stats(
            ontology, skipped_reason="single_instance", instances_dropped=degenerate_dropped
        )

    original_total = len(ontology.instances)
    working = ontology.model_copy(deep=True)
    candidate_ids = isolated[: config.max_llm_instances]
    anchors = _anchor_instances(working, candidate_ids, config.max_anchor_instances)
    if not anchors:
        anchors = _bootstrap_anchor_instances(working, candidate_ids)

    llm_data = await _request_llm_plan(working, llm_client, candidate_ids, anchors, config)
    llm_links_added = _apply_llm_links(working, llm_data, set(candidate_ids), config)
    actions_added = _apply_llm_actions(working, llm_data, config)
    dropped = _apply_llm_drops(working, llm_data, set(candidate_ids), config)
    fallback_links_added = _connect_remaining_isolated(working, config)
    isolated_after = len(_isolated_instance_ids(working))
    return working, InstancePostprocessStats(
        enabled=True,
        total_instances=original_total,
        isolated_before=len(isolated),
        isolated_after=isolated_after,
        llm_candidate_count=len(candidate_ids),
        llm_links_added=llm_links_added,
        fallback_links_added=fallback_links_added,
        instances_dropped=dropped + degenerate_dropped,
        actions_added=actions_added,
    )


def needs_instance_connectivity_repair(
    ontology: GlobalOntology,
    config: InstancePostprocessConfig,
) -> bool:
    if not config.enabled:
        return False
    if len(ontology.instances) < config.min_instances:
        return False
    return bool(_isolated_instance_ids(ontology))


def _stats(
    ontology: GlobalOntology,
    *,
    enabled: bool = True,
    skipped_reason: str = "",
    instances_dropped: int = 0,
) -> InstancePostprocessStats:
    isolated = len(_isolated_instance_ids(ontology))
    return InstancePostprocessStats(
        enabled=enabled,
        total_instances=len(ontology.instances),
        isolated_before=isolated,
        isolated_after=isolated,
        instances_dropped=instances_dropped,
        skipped_reason=skipped_reason,
    )


def _isolated_instance_ids(ontology: GlobalOntology) -> list[str]:
    ids = {instance.id for instance in ontology.instances}
    degree = {instance_id: 0 for instance_id in ids}
    for instance in ontology.instances:
        for link in instance.link_values:
            if link.target_instance_id not in ids:
                continue
            degree[instance.id] += 1
            degree[link.target_instance_id] += 1
    for invocation in ontology.action_invocations:
        participants = [
            instance_id
            for instance_id in invocation.input_instance_ids + invocation.output_instance_ids
            if instance_id in ids
        ]
        if len(participants) >= 2:
            for instance_id in participants:
                degree[instance_id] += 1
    return [instance.id for instance in ontology.instances if degree.get(instance.id, 0) == 0]


def _anchor_instances(
    ontology: GlobalOntology,
    isolated_ids: list[str],
    limit: int,
) -> list[OntologyInstance]:
    isolated_set = set(isolated_ids)
    linked_ids: set[str] = set()
    for instance in ontology.instances:
        if instance.id in isolated_set:
            continue
        if instance.link_values:
            linked_ids.add(instance.id)
    for instance in ontology.instances:
        for link in instance.link_values:
            if link.target_instance_id not in isolated_set:
                linked_ids.add(link.target_instance_id)
    return [instance for instance in ontology.instances if instance.id in linked_ids][:limit]


def _bootstrap_anchor_instances(
    ontology: GlobalOntology,
    candidate_ids: list[str],
) -> list[OntologyInstance]:
    candidate_set = set(candidate_ids)
    for instance in ontology.instances:
        if instance.id not in candidate_set:
            return [instance]
    for instance in ontology.instances:
        if instance.id != candidate_ids[0]:
            return [instance]
    return []


async def _request_llm_plan(
    ontology: GlobalOntology,
    llm_client: InstanceRepairLLM,
    candidate_ids: list[str],
    anchors: list[OntologyInstance],
    config: InstancePostprocessConfig,
) -> dict[str, Any]:
    payload = {
        "schema_relations": _schema_relation_payload(ontology),
        "isolated_instances": [
            _instance_payload(instance)
            for instance in ontology.instances
            if instance.id in set(candidate_ids)
        ],
        "anchor_instances": [_instance_payload(instance) for instance in anchors],
        "rules": {
            "drop_instance_ids": "只能删除 isolated_instances 中语义价值低、重复或明显噪声的实例。",
            "links": "只能连接现有实例 id，至少一个端点必须来自 isolated_instances。",
            "actions": "只能引用现有实例 id，用于表达过程、处理、流转、生成等动作。",
            "no_fabrication": "不得创造数据库/文档中不存在的实例或事实。",
        },
    }
    prompt = (
        "请对本体实例图中的离散点做演示兜底处理建议。\n"
        "目标：尽量删除低价值离散点；保留的离散点需要通过语义关系或动作连到锚点实例。\n"
        "必须只输出 JSON 对象，格式如下：\n"
        "{\"drop_instance_ids\":[\"...\"],"
        "\"links\":[{\"source_instance_id\":\"...\",\"predicate\":\"...\","
        "\"target_instance_id\":\"...\",\"confidence\":0.0,\"evidence\":\"...\"}],"
        "\"actions\":[{\"name\":\"...\",\"description\":\"...\","
        "\"input_instance_ids\":[\"...\"],\"output_instance_ids\":[\"...\"]}]}\n"
        "不确定时少输出，不要编造 id。\n\n"
        f"输入数据:\n{json.dumps(payload, ensure_ascii=False)}"
    )
    raw = await llm_client.complete(
        prompt,
        system_prompt="你是本体实例图连通性修复专家，只输出合法 JSON。",
    )
    try:
        data = _loads_json_object(raw)
    except ValueError:
        return {}
    return data


def _schema_relation_payload(ontology: GlobalOntology) -> list[dict[str, str]]:
    relations: list[dict[str, str]] = []
    for triple in ontology.triples:
        if triple.object_kind != "entity" or not triple.object_id:
            continue
        source = ontology.entities.get(triple.subject_id)
        target = ontology.entities.get(triple.object_id)
        relations.append(
            {
                "source_class_id": triple.subject_id,
                "source_class_name": source.name if source else triple.subject_id,
                "predicate": triple.predicate,
                "target_class_id": triple.object_id,
                "target_class_name": target.name if target else triple.object_id,
            }
        )
    return relations[:80]


def _instance_payload(instance: OntologyInstance) -> dict[str, Any]:
    attrs = []
    for attr in instance.attribute_values[:6]:
        attrs.append({"name": attr.attribute_name, "value": attr.value})
    return {
        "id": instance.id,
        "name": instance.name,
        "class_id": instance.class_id,
        "attributes": attrs,
        "source_chunks": instance.source_chunks[:3],
    }


def _apply_llm_links(
    ontology: GlobalOntology,
    data: dict[str, Any],
    candidate_ids: set[str],
    config: InstancePostprocessConfig,
) -> int:
    instance_ids = {instance.id for instance in ontology.instances}
    by_id = {instance.id: instance for instance in ontology.instances}
    added = 0
    for raw_link in data.get("links") or []:
        if not isinstance(raw_link, dict):
            continue
        source_id = str(raw_link.get("source_instance_id") or "").strip()
        target_id = str(raw_link.get("target_instance_id") or "").strip()
        predicate = str(raw_link.get("predicate") or "").strip()
        confidence = _bounded_confidence(raw_link.get("confidence"))
        if (
            not source_id
            or not target_id
            or source_id == target_id
            or source_id not in instance_ids
            or target_id not in instance_ids
            or (source_id not in candidate_ids and target_id not in candidate_ids)
            or not predicate
            or confidence < config.min_link_confidence
        ):
            continue
        source = by_id[source_id]
        if _has_link(source, predicate, target_id):
            continue
        source.link_values.append(
            InstanceLinkValue(
                predicate=predicate,
                target_instance_id=target_id,
                confidence=confidence,
                source_chunks=[config.source_tag, "llm"],
            )
        )
        added += 1
    return added


def _apply_llm_drops(
    ontology: GlobalOntology,
    data: dict[str, Any],
    candidate_ids: set[str],
    config: InstancePostprocessConfig,
) -> int:
    drop_ids = [
        str(instance_id)
        for instance_id in data.get("drop_instance_ids") or []
        if str(instance_id) in candidate_ids
    ]
    if not drop_ids:
        return 0
    still_isolated = set(_isolated_instance_ids(ontology))
    max_drop = int(len(ontology.instances) * config.max_drop_ratio)
    allowed = [instance_id for instance_id in drop_ids if instance_id in still_isolated]
    allowed = allowed[:max_drop]
    if not allowed:
        return 0
    allowed_set = set(allowed)
    ontology.instances = [
        instance for instance in ontology.instances if instance.id not in allowed_set
    ]
    for instance in ontology.instances:
        instance.link_values = [
            link
            for link in instance.link_values
            if link.target_instance_id not in allowed_set
        ]
    ontology.action_invocations = [
        invocation.model_copy(
            update={
                "input_instance_ids": [
                    instance_id
                    for instance_id in invocation.input_instance_ids
                    if instance_id not in allowed_set
                ],
                "output_instance_ids": [
                    instance_id
                    for instance_id in invocation.output_instance_ids
                    if instance_id not in allowed_set
                ],
            }
        )
        for invocation in ontology.action_invocations
    ]
    return len(allowed)


def _apply_llm_actions(
    ontology: GlobalOntology,
    data: dict[str, Any],
    config: InstancePostprocessConfig,
) -> int:
    instance_ids = {instance.id for instance in ontology.instances}
    added = 0
    existing_action_names = {action.name for action in ontology.actions}
    for raw_action in data.get("actions") or []:
        if not isinstance(raw_action, dict):
            continue
        name = str(raw_action.get("name") or "").strip()
        if not name:
            continue
        inputs = [
            str(instance_id)
            for instance_id in raw_action.get("input_instance_ids") or []
            if str(instance_id) in instance_ids
        ]
        outputs = [
            str(instance_id)
            for instance_id in raw_action.get("output_instance_ids") or []
            if str(instance_id) in instance_ids
        ]
        if len(set(inputs + outputs)) < 2:
            continue
        description = str(raw_action.get("description") or "").strip()
        action_id = _stable_id("action", name, ",".join(inputs), ",".join(outputs))
        if name not in existing_action_names:
            ontology.actions.append(
                OntologyAction(
                    id=action_id,
                    name=name,
                    description=description,
                    source_chunks=[config.source_tag, "llm"],
                )
            )
            existing_action_names.add(name)
        invocation_id = _stable_id("action_invocation", name, ",".join(inputs), ",".join(outputs))
        if any(invocation.id == invocation_id for invocation in ontology.action_invocations):
            continue
        ontology.action_invocations.append(
            ActionInvocation(
                id=invocation_id,
                action_name=name,
                input_instance_ids=inputs,
                output_instance_ids=outputs,
                attributes={"说明": description} if description else {},
                source_chunks=[config.source_tag, "llm"],
            )
        )
        added += 1
    return added


def _connect_remaining_isolated(
    ontology: GlobalOntology,
    config: InstancePostprocessConfig,
) -> int:
    added = 0
    while True:
        isolated_ids = _isolated_instance_ids(ontology)
        if not isolated_ids or len(ontology.instances) <= 1:
            return added
        by_id = {instance.id: instance for instance in ontology.instances}
        first_id = isolated_ids[0]
        source = by_id[first_id]
        target = _select_fallback_target(ontology, source, set(isolated_ids))
        if target is None:
            return added
        predicate = _schema_predicate_for_classes(
            ontology,
            source.class_id,
            target.class_id,
        ) or config.fallback_predicate
        if not _has_link(source, predicate, target.id):
            source.link_values.append(
                InstanceLinkValue(
                    predicate=predicate,
                    target_instance_id=target.id,
                    confidence=0.6,
                    source_chunks=[config.source_tag, "fallback"],
                )
            )
            added += 1
        else:
            return added


def _select_fallback_target(
    ontology: GlobalOntology,
    source: OntologyInstance,
    isolated_ids: set[str],
) -> OntologyInstance | None:
    non_isolated = [
        instance
        for instance in ontology.instances
        if instance.id != source.id and instance.id not in isolated_ids
    ]
    for instance in non_isolated:
        if instance.class_id == source.class_id:
            return instance
    if non_isolated:
        return non_isolated[0]
    for instance in ontology.instances:
        if instance.id != source.id:
            return instance
    return None


def _schema_predicate_for_classes(
    ontology: GlobalOntology,
    source_class_id: str,
    target_class_id: str,
) -> str | None:
    for triple in ontology.triples:
        if (
            triple.object_kind == "entity"
            and triple.subject_id == source_class_id
            and triple.object_id == target_class_id
        ):
            return triple.predicate
    for triple in ontology.triples:
        if (
            triple.object_kind == "entity"
            and triple.subject_id == target_class_id
            and triple.object_id == source_class_id
        ):
            return triple.predicate
    return None


def _has_link(instance: OntologyInstance, predicate: str, target_id: str) -> bool:
    return any(
        link.predicate == predicate and link.target_instance_id == target_id
        for link in instance.link_values
    )


def _loads_json_object(raw: str) -> dict[str, Any]:
    candidate = raw.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", candidate, re.DOTALL)
    if fenced:
        candidate = fenced.group(1).strip()
    if not candidate.startswith("{"):
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start >= 0 and end > start:
            candidate = candidate[start : end + 1]
    data = json.loads(candidate)
    if not isinstance(data, dict):
        raise ValueError("Expected a JSON object")
    return data


def _bounded_confidence(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, number))


def _stable_id(prefix: str, *parts: str) -> str:
    raw = "::".join(parts)
    return f"{prefix}_{uuid5(NAMESPACE_URL, raw).hex[:12]}"
