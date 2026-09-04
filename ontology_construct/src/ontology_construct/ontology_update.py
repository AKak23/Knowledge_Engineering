from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import hashlib
import json
from typing import Any, Iterable

from ontology_construct.output.formatted_ontology import FormattedOntology


UPDATE_SCOPES = ("objects", "relations", "rules", "actions")


def normalize_update_scopes(values: Iterable[str]) -> list[str]:
    requested = {str(value).strip().lower() for value in values}
    return [scope for scope in UPDATE_SCOPES if scope in requested]


def build_update_plan(
    base: FormattedOntology,
    candidate: FormattedOntology,
    *,
    scopes: Iterable[str],
    source_documents: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build an additive, dependency-aware plan without mutating either ontology."""
    enabled = normalize_update_scopes(scopes)
    base_payload = base.model_dump(mode="json")
    candidate_payload = candidate.model_dump(mode="json")
    changes: list[dict[str, Any]] = []

    base_objects = _resource_index(base_payload["objects"], "objectCode", "objectName")
    object_matches: dict[str, dict[str, Any] | None] = {}
    new_object_change_ids: dict[str, str] = {}
    for item in candidate_payload["objects"]:
        candidate_ref = str(item["objectId"])
        match = _resource_match(item, base_objects, "objectCode", "objectName")
        object_matches[candidate_ref] = match
        if "objects" not in enabled:
            continue
        operation = "modified" if match else "added"
        change = _change(
            kind="object",
            operation=operation,
            candidate_ref=candidate_ref,
            title=str(item.get("objectName") or candidate_ref),
            summary=_object_change_summary(match, item, candidate_payload["instances"]),
            before=match,
            after=item,
            source_chunks=item.get("sourceChunks") or [],
        )
        changes.append(change)
        if match is None:
            new_object_change_ids[candidate_ref] = change["changeId"]

    def object_resolvable(value: Any) -> bool:
        ref = str(value)
        return object_matches.get(ref) is not None or ref in new_object_change_ids

    base_links = {
        _relation_key(item): item for item in base_payload["links"]
    }
    if "relations" in enabled:
        for item in candidate_payload["links"]:
            if not object_resolvable(item.get("sourceObjectId")) or not object_resolvable(
                item.get("targetObjectId")
            ):
                continue
            match = base_links.get(_relation_key(item))
            dependencies = [
                new_object_change_ids[ref]
                for ref in (
                    str(item.get("sourceObjectId")),
                    str(item.get("targetObjectId")),
                )
                if ref in new_object_change_ids
            ]
            changes.append(
                _change(
                    kind="relation",
                    operation="modified" if match else "added",
                    candidate_ref=str(item.get("linkId")),
                    title=(
                        f"{item.get('sourceObjectName', '')} → "
                        f"{item.get('targetObjectName', '')}"
                    ),
                    summary=str(item.get("linkInfo") or "关联"),
                    before=match,
                    after=item,
                    source_chunks=item.get("sourceChunks") or [],
                    depends_on=dependencies,
                )
            )

    base_rules = _resource_index(base_payload["rules"], "ruleCode", "ruleName")
    rule_matches: dict[str, dict[str, Any] | None] = {}
    new_rule_change_ids: dict[str, str] = {}
    if "rules" in enabled:
        for item in candidate_payload["rules"]:
            candidate_ref = str(item["ruleId"])
            if not object_resolvable(item.get("scopeObjectId")):
                continue
            match = _resource_match(item, base_rules, "ruleCode", "ruleName")
            rule_matches[candidate_ref] = match
            object_ref = str(item.get("scopeObjectId"))
            dependencies = (
                [new_object_change_ids[object_ref]]
                if object_ref in new_object_change_ids
                else []
            )
            change = _change(
                kind="rule",
                operation="modified" if match else "added",
                candidate_ref=candidate_ref,
                title=str(item.get("ruleName") or candidate_ref),
                summary=(
                    f"圈定 {item.get('scopeObjectName') or '未知对象'}，"
                    f"{len(item.get('conditions') or [])} 个判定条件"
                ),
                before=match,
                after=item,
                source_chunks=item.get("sourceChunks") or [],
                depends_on=dependencies,
            )
            changes.append(change)
            if match is None:
                new_rule_change_ids[candidate_ref] = change["changeId"]
    else:
        for item in candidate_payload["rules"]:
            rule_matches[str(item["ruleId"])] = _resource_match(
                item, base_rules, "ruleCode", "ruleName"
            )

    def rule_resolvable(value: Any) -> bool:
        ref = str(value)
        return rule_matches.get(ref) is not None or ref in new_rule_change_ids

    base_actions = _resource_index(
        base_payload["actions"], "actionCode", "actionName"
    )
    if "actions" in enabled:
        for item in candidate_payload["actions"]:
            object_refs = {
                str(value)
                for key in ("inputObjectIds", "outputObjectIds", "targetObjectIds")
                for value in item.get(key) or []
            }
            rule_refs = {str(value) for value in item.get("triggerRuleIds") or []}
            if any(not object_resolvable(value) for value in object_refs):
                continue
            if any(not rule_resolvable(value) for value in rule_refs):
                continue
            match = _resource_match(
                item, base_actions, "actionCode", "actionName"
            )
            dependencies = [
                new_object_change_ids[value]
                for value in sorted(object_refs)
                if value in new_object_change_ids
            ]
            dependencies.extend(
                new_rule_change_ids[value]
                for value in sorted(rule_refs)
                if value in new_rule_change_ids
            )
            changes.append(
                _change(
                    kind="action",
                    operation="modified" if match else "added",
                    candidate_ref=str(item.get("actionId")),
                    title=str(item.get("actionName") or item.get("actionId")),
                    summary=(
                        str(item.get("actionPlan") or item.get("description") or "")
                        or "补充业务行动定义"
                    ),
                    before=match,
                    after=item,
                    source_chunks=item.get("sourceChunks") or [],
                    depends_on=dependencies,
                )
            )

    counts = {
        "added": sum(1 for item in changes if item["operation"] == "added"),
        "modified": sum(1 for item in changes if item["operation"] == "modified"),
        "objects": sum(1 for item in changes if item["kind"] == "object"),
        "relations": sum(1 for item in changes if item["kind"] == "relation"),
        "rules": sum(1 for item in changes if item["kind"] == "rule"),
        "actions": sum(1 for item in changes if item["kind"] == "action"),
    }
    return {
        "contractVersion": "ontology-update-plan-v1",
        "baseOntology": {
            "ontologyId": base_payload["ontology"].get("ontologyId"),
            "ontologyName": base_payload["ontology"].get("ontologyName"),
        },
        "scopes": enabled,
        "sourceDocuments": source_documents or [],
        "counts": counts,
        "changes": changes,
        "safety": {
            "additiveOnly": True,
            "deletesExistingResources": False,
            "overwritesExistingValues": False,
        },
    }


def resolve_selected_change_ids(
    plan: dict[str, Any], requested_ids: Iterable[str]
) -> list[str]:
    by_id = {
        str(item["changeId"]): item for item in plan.get("changes") or []
    }
    selected = {str(value) for value in requested_ids}
    unknown = selected.difference(by_id)
    if unknown:
        raise ValueError(f"Unknown update change ids: {', '.join(sorted(unknown))}")
    pending = list(selected)
    while pending:
        current = pending.pop()
        for dependency in by_id[current].get("dependsOn") or []:
            dependency = str(dependency)
            if dependency not in by_id:
                raise ValueError(f"Missing update dependency: {dependency}")
            if dependency not in selected:
                selected.add(dependency)
                pending.append(dependency)
    return [item["changeId"] for item in plan.get("changes") or [] if item["changeId"] in selected]


def merge_update_selection(
    base: FormattedOntology,
    candidate: FormattedOntology,
    plan: dict[str, Any],
    selected_change_ids: Iterable[str],
    *,
    timestamp: datetime | None = None,
) -> tuple[FormattedOntology, list[str]]:
    """Apply selected additions non-destructively and return a new ontology value."""
    applied_ids = resolve_selected_change_ids(plan, selected_change_ids)
    selected = set(applied_ids)
    selected_refs: dict[str, set[str]] = {
        scope: set() for scope in ("object", "relation", "rule", "action")
    }
    for change in plan.get("changes") or []:
        if change.get("changeId") in selected:
            selected_refs[str(change["kind"])].add(str(change["candidateRef"]))

    merged = deepcopy(base.model_dump(mode="json"))
    incoming = candidate.model_dump(mode="json")
    object_id_map = _merge_objects(
        merged, incoming, selected_refs["object"]
    )
    _merge_relations(
        merged, incoming, selected_refs["relation"], object_id_map
    )
    rule_id_map = _merge_rules(
        merged, incoming, selected_refs["rule"], object_id_map
    )
    action_id_map = _merge_actions(
        merged,
        incoming,
        selected_refs["action"],
        object_id_map,
        rule_id_map,
    )
    instance_id_map = _merge_instances(
        merged, incoming, selected_refs["object"], object_id_map
    )
    _merge_action_invocations(
        merged,
        incoming,
        selected_refs["action"],
        action_id_map,
        instance_id_map,
    )
    _rebuild_reverse_references(merged)
    merged["ontology"]["updatedAt"] = (timestamp or datetime.now()).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    return FormattedOntology.model_validate(merged), applied_ids


def _change(
    *,
    kind: str,
    operation: str,
    candidate_ref: str,
    title: str,
    summary: str,
    before: dict[str, Any] | None,
    after: dict[str, Any],
    source_chunks: list[Any],
    depends_on: list[str] | None = None,
) -> dict[str, Any]:
    digest = hashlib.sha1(  # noqa: S324 - stable display identifier, not security.
        f"{kind}:{operation}:{candidate_ref}:{title}".encode("utf-8")
    ).hexdigest()[:12]
    return {
        "changeId": f"change_{digest}",
        "kind": kind,
        "operation": operation,
        "candidateRef": candidate_ref,
        "title": title,
        "summary": summary,
        "before": before,
        "after": after,
        "sourceChunks": [str(value) for value in source_chunks if value],
        "dependsOn": list(dict.fromkeys(depends_on or [])),
        "selectedByDefault": True,
    }


def _object_change_summary(
    before: dict[str, Any] | None,
    after: dict[str, Any],
    candidate_instances: list[dict[str, Any]],
) -> str:
    instance_count = sum(
        1
        for item in candidate_instances
        if str(item.get("objectId")) == str(after.get("objectId"))
    )
    if before is None:
        return (
            f"新增 {len(after.get('attributes') or [])} 个属性，"
            f"关联 {instance_count} 个文档实例"
        )
    known_attributes = {
        _normalized(item.get("attributeCode") or item.get("attributeName"))
        for item in before.get("attributes") or []
    }
    new_attributes = sum(
        1
        for item in after.get("attributes") or []
        if _normalized(item.get("attributeCode") or item.get("attributeName"))
        not in known_attributes
    )
    return f"补充 {new_attributes} 个属性、{instance_count} 个实例及来源证据"


def _resource_index(
    items: list[dict[str, Any]], code_field: str, name_field: str
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in items:
        for key in _resource_keys(item, code_field, name_field):
            result.setdefault(key, item)
    return result


def _resource_match(
    item: dict[str, Any],
    index: dict[str, dict[str, Any]],
    code_field: str,
    name_field: str,
) -> dict[str, Any] | None:
    return next(
        (index[key] for key in _resource_keys(item, code_field, name_field) if key in index),
        None,
    )


def _resource_keys(
    item: dict[str, Any], code_field: str, name_field: str
) -> list[str]:
    keys = []
    code = _normalized(item.get(code_field))
    name = _normalized(item.get(name_field))
    if name:
        keys.append(f"name:{name}")
    if code and not _is_generated_code(code):
        keys.append(f"code:{code}")
    return keys


def _normalized(value: Any) -> str:
    return "".join(str(value or "").strip().lower().split())


def _is_generated_code(value: str) -> bool:
    parts = value.rsplit("_", 1)
    return len(parts) == 2 and parts[0] in {"object", "rule", "action"} and parts[1].isdigit()


def _relation_key(item: dict[str, Any]) -> str:
    return "|".join(
        (
            _normalized(item.get("sourceObjectName")),
            _normalized(item.get("linkInfo")),
            _normalized(item.get("targetObjectName")),
        )
    )


def _next_numeric_id(items: list[dict[str, Any]], field: str) -> int:
    return max((int(item.get(field) or 0) for item in items), default=0) + 1


def _union_values(left: Iterable[Any], right: Iterable[Any]) -> list[Any]:
    result: list[Any] = []
    seen: set[str] = set()
    for value in [*left, *right]:
        marker = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        if marker in seen:
            continue
        seen.add(marker)
        result.append(deepcopy(value))
    return result


def _merge_objects(
    merged: dict[str, Any], incoming: dict[str, Any], selected_refs: set[str]
) -> dict[str, int]:
    existing_index = _resource_index(
        merged["objects"], "objectCode", "objectName"
    )
    next_id = _next_numeric_id(merged["objects"], "objectId")
    id_map: dict[str, int] = {}
    for item in incoming["objects"]:
        ref = str(item["objectId"])
        existing = _resource_match(item, existing_index, "objectCode", "objectName")
        if existing is not None:
            id_map[ref] = int(existing["objectId"])
            if ref in selected_refs:
                existing["attributes"] = _merge_attributes(
                    existing.get("attributes") or [], item.get("attributes") or []
                )
                existing["sourceChunks"] = _union_values(
                    existing.get("sourceChunks") or [], item.get("sourceChunks") or []
                )
                if not existing.get("description") and item.get("description"):
                    existing["description"] = item["description"]
            continue
        if ref not in selected_refs:
            continue
        added = deepcopy(item)
        added["objectId"] = next_id
        added["instanceIds"] = []
        added["instanceCount"] = 0
        added["realInstanceCount"] = 0
        added["generatedInstanceCount"] = 0
        added["instanceCoverage"] = "uncovered"
        id_map[ref] = next_id
        merged["objects"].append(added)
        for key in _resource_keys(added, "objectCode", "objectName"):
            existing_index[key] = added
        next_id += 1
    return id_map


def _merge_attributes(
    existing: list[dict[str, Any]], incoming: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    result = deepcopy(existing)
    by_key = {
        _normalized(item.get("attributeCode") or item.get("attributeName")): item
        for item in result
    }
    next_id = _next_numeric_id(result, "attributeId")
    for item in incoming:
        key = _normalized(item.get("attributeCode") or item.get("attributeName"))
        match = by_key.get(key)
        if match:
            match["sourceChunks"] = _union_values(
                match.get("sourceChunks") or [], item.get("sourceChunks") or []
            )
            if match.get("sampleValue") in (None, "") and item.get("sampleValue") not in (
                None,
                "",
            ):
                match["sampleValue"] = item["sampleValue"]
            continue
        added = deepcopy(item)
        added["attributeId"] = next_id
        next_id += 1
        result.append(added)
        by_key[key] = added
    return result


def _merge_relations(
    merged: dict[str, Any],
    incoming: dict[str, Any],
    selected_refs: set[str],
    object_id_map: dict[str, int],
) -> None:
    existing = {_relation_key(item): item for item in merged["links"]}
    object_by_id = {int(item["objectId"]): item for item in merged["objects"]}
    next_id = _next_numeric_id(merged["links"], "linkId")
    for item in incoming["links"]:
        if str(item["linkId"]) not in selected_refs:
            continue
        source_id = object_id_map.get(str(item["sourceObjectId"]))
        target_id = object_id_map.get(str(item["targetObjectId"]))
        if source_id is None or target_id is None:
            continue
        added = deepcopy(item)
        added.update(
            {
                "sourceObjectId": source_id,
                "sourceObjectName": object_by_id[source_id]["objectName"],
                "targetObjectId": target_id,
                "targetObjectName": object_by_id[target_id]["objectName"],
            }
        )
        key = _relation_key(added)
        match = existing.get(key)
        if match:
            match["sourceChunks"] = _union_values(
                match.get("sourceChunks") or [], added.get("sourceChunks") or []
            )
            if not match.get("description") and added.get("description"):
                match["description"] = added["description"]
            continue
        added["linkId"] = next_id
        next_id += 1
        merged["links"].append(added)
        existing[key] = added


def _merge_rules(
    merged: dict[str, Any],
    incoming: dict[str, Any],
    selected_refs: set[str],
    object_id_map: dict[str, int],
) -> dict[str, int]:
    existing_index = _resource_index(merged["rules"], "ruleCode", "ruleName")
    object_by_id = {int(item["objectId"]): item for item in merged["objects"]}
    next_id = _next_numeric_id(merged["rules"], "ruleId")
    id_map: dict[str, int] = {}
    for item in incoming["rules"]:
        ref = str(item["ruleId"])
        match = _resource_match(item, existing_index, "ruleCode", "ruleName")
        if match is not None:
            id_map[ref] = int(match["ruleId"])
            if ref in selected_refs:
                match["conditions"] = _union_values(
                    match.get("conditions") or [], item.get("conditions") or []
                )
                match["sourceChunks"] = _union_values(
                    match.get("sourceChunks") or [], item.get("sourceChunks") or []
                )
            continue
        if ref not in selected_refs:
            continue
        scope_id = object_id_map.get(str(item.get("scopeObjectId")))
        if scope_id is None:
            continue
        added = deepcopy(item)
        added.update(
            {
                "ruleId": next_id,
                "scopeObjectId": scope_id,
                "scopeObjectName": object_by_id[scope_id]["objectName"],
                "actionIds": [],
                "actionNames": [],
            }
        )
        id_map[ref] = next_id
        merged["rules"].append(added)
        for key in _resource_keys(added, "ruleCode", "ruleName"):
            existing_index[key] = added
        next_id += 1
    return id_map


def _merge_actions(
    merged: dict[str, Any],
    incoming: dict[str, Any],
    selected_refs: set[str],
    object_id_map: dict[str, int],
    rule_id_map: dict[str, int],
) -> dict[str, int]:
    existing_index = _resource_index(
        merged["actions"], "actionCode", "actionName"
    )
    next_id = _next_numeric_id(merged["actions"], "actionId")
    id_map: dict[str, int] = {}
    for item in incoming["actions"]:
        ref = str(item["actionId"])
        match = _resource_match(item, existing_index, "actionCode", "actionName")
        mapped = _mapped_action_references(item, object_id_map, rule_id_map)
        if match is not None:
            id_map[ref] = int(match["actionId"])
            if ref in selected_refs:
                for field, values in mapped.items():
                    match[field] = _union_values(match.get(field) or [], values)
                match["sourceChunks"] = _union_values(
                    match.get("sourceChunks") or [], item.get("sourceChunks") or []
                )
                for field in ("description", "executor", "actionPlan", "feedback"):
                    if not match.get(field) and item.get(field):
                        match[field] = item[field]
            continue
        if ref not in selected_refs:
            continue
        added = deepcopy(item)
        added.update(mapped)
        added["actionId"] = next_id
        id_map[ref] = next_id
        merged["actions"].append(added)
        for key in _resource_keys(added, "actionCode", "actionName"):
            existing_index[key] = added
        next_id += 1
    return id_map


def _mapped_action_references(
    item: dict[str, Any],
    object_id_map: dict[str, int],
    rule_id_map: dict[str, int],
) -> dict[str, list[Any]]:
    def map_objects(field: str) -> list[int]:
        return [
            object_id_map[str(value)]
            for value in item.get(field) or []
            if str(value) in object_id_map
        ]

    return {
        "inputObjectIds": map_objects("inputObjectIds"),
        "outputObjectIds": map_objects("outputObjectIds"),
        "targetObjectIds": map_objects("targetObjectIds"),
        "triggerRuleIds": [
            rule_id_map[str(value)]
            for value in item.get("triggerRuleIds") or []
            if str(value) in rule_id_map
        ],
        "inputObjects": list(item.get("inputObjects") or []),
        "outputObjects": list(item.get("outputObjects") or []),
        "targetObjects": list(item.get("targetObjects") or []),
        "triggerRules": list(item.get("triggerRules") or []),
    }


def _merge_instances(
    merged: dict[str, Any],
    incoming: dict[str, Any],
    selected_object_refs: set[str],
    object_id_map: dict[str, int],
) -> dict[str, int]:
    next_id = _next_numeric_id(merged["instances"], "instanceId")
    existing_by_key = {
        _instance_key(item): item for item in merged["instances"]
    }
    id_map: dict[str, int] = {}
    pending_links: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
    for item in incoming["instances"]:
        object_ref = str(item.get("objectId"))
        if object_ref not in selected_object_refs or object_ref not in object_id_map:
            continue
        target_object_id = object_id_map[object_ref]
        candidate = deepcopy(item)
        candidate["objectId"] = target_object_id
        key = _instance_key(candidate)
        match = existing_by_key.get(key)
        if match:
            id_map[str(item["instanceId"])] = int(match["instanceId"])
            match["attributeValues"] = _union_values(
                match.get("attributeValues") or [], candidate.get("attributeValues") or []
            )
            match["sourceChunks"] = _union_values(
                match.get("sourceChunks") or [], candidate.get("sourceChunks") or []
            )
            pending_links.append((match, candidate.get("linkValues") or []))
            continue
        candidate["instanceId"] = next_id
        id_map[str(item["instanceId"])] = next_id
        pending_links.append((candidate, candidate.pop("linkValues", [])))
        merged["instances"].append(candidate)
        existing_by_key[key] = candidate
        next_id += 1
    for target, link_values in pending_links:
        mapped_links = []
        for link in link_values:
            target_id = id_map.get(str(link.get("targetInstanceId")))
            if target_id is None:
                target_match = next(
                    (
                        item
                        for item in merged["instances"]
                        if _normalized(item.get("instanceName"))
                        == _normalized(link.get("targetInstanceName"))
                    ),
                    None,
                )
                target_id = int(target_match["instanceId"]) if target_match else None
            if target_id is None:
                continue
            mapped = deepcopy(link)
            mapped["targetInstanceId"] = target_id
            mapped["linkId"] = None
            if str(link.get("targetObjectId")) in object_id_map:
                mapped["targetObjectId"] = object_id_map[str(link["targetObjectId"])]
            mapped_links.append(mapped)
        target["linkValues"] = _union_values(
            target.get("linkValues") or [], mapped_links
        )
    return id_map


def _instance_key(item: dict[str, Any]) -> str:
    return "|".join(
        (
            str(item.get("objectId") or ""),
            _normalized(item.get("instanceCode") or item.get("instanceName")),
        )
    )


def _merge_action_invocations(
    merged: dict[str, Any],
    incoming: dict[str, Any],
    selected_action_refs: set[str],
    action_id_map: dict[str, int],
    instance_id_map: dict[str, int],
) -> None:
    next_id = _next_numeric_id(merged["actionInvocations"], "invocationId")
    for item in incoming["actionInvocations"]:
        action_ref = str(item.get("actionId"))
        if action_ref not in selected_action_refs or action_ref not in action_id_map:
            continue
        added = deepcopy(item)
        added["invocationId"] = next_id
        added["actionId"] = action_id_map[action_ref]
        added["inputInstances"] = [
            instance_id_map[str(value)]
            for value in item.get("inputInstances") or []
            if str(value) in instance_id_map
        ]
        added["outputInstances"] = [
            instance_id_map[str(value)]
            for value in item.get("outputInstances") or []
            if str(value) in instance_id_map
        ]
        merged["actionInvocations"].append(added)
        next_id += 1


def _rebuild_reverse_references(payload: dict[str, Any]) -> None:
    instances_by_object: dict[int, list[dict[str, Any]]] = {}
    for item in payload["instances"]:
        instances_by_object.setdefault(int(item["objectId"]), []).append(item)
    for item in payload["objects"]:
        instances = instances_by_object.get(int(item["objectId"]), [])
        item["instanceIds"] = [int(value["instanceId"]) for value in instances]
        item["instanceCount"] = len(instances)
        item["generatedInstanceCount"] = sum(
            1 for value in instances if value.get("generated")
        )
        item["realInstanceCount"] = len(instances) - item["generatedInstanceCount"]
        item["instanceCoverage"] = "grounded" if item["realInstanceCount"] else (
            "generated" if instances else "uncovered"
        )

    action_by_id = {int(item["actionId"]): item for item in payload["actions"]}
    for rule in payload["rules"]:
        linked_actions = [
            action
            for action in payload["actions"]
            if int(rule["ruleId"]) in {int(value) for value in action.get("triggerRuleIds") or []}
        ]
        rule["actionIds"] = [int(action["actionId"]) for action in linked_actions]
        rule["actionNames"] = [str(action["actionName"]) for action in linked_actions]
    for invocation in payload["actionInvocations"]:
        action = action_by_id.get(int(invocation.get("actionId") or 0))
        if action:
            invocation["actionName"] = action["actionName"]
