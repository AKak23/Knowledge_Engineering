from __future__ import annotations

import json
import logging
import re
from typing import Any

from pydantic import ValidationError

logger = logging.getLogger(__name__)

from ontology_construct.models import (
    ActionInvocation,
    Entity,
    InstanceAttributeValue,
    InstanceLinkValue,
    LocalOntology,
    OntologyAction,
    OntologyInstance,
    OntologyRule,
    RuleCondition,
    Triple,
)


class OntologyParseError(ValueError):
    pass


FENCED_JSON_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)
TRAILING_COMMA_RE = re.compile(r",\s*([}\]])")


def parse_local_ontology(raw_response: str, chunk_id: str) -> LocalOntology:
    data = _loads_json(raw_response)
    entity_items = _first_present(data, ["entities", "entity", "nodes"]) or []
    triple_items = (
        _first_present(data, ["triples", "relations", "relationships", "edges"]) or []
    )
    rule_items = _first_present(data, ["rules", "business_rules", "policies"]) or []
    action_items = _first_present(data, ["actions", "action", "operations"]) or []
    instance_items = _first_present(data, ["instances", "individuals", "examples"]) or []
    invocation_items = (
        _first_present(
            data,
            ["action_invocations", "actionInvocations", "invocations"],
        )
        or []
    )
    if not isinstance(entity_items, list):
        raise OntologyParseError("entities must be a list")
    if not isinstance(triple_items, list):
        raise OntologyParseError("triples must be a list")
    if not isinstance(rule_items, list):
        raise OntologyParseError("rules must be a list")
    if not isinstance(action_items, list):
        raise OntologyParseError("actions must be a list")
    if not isinstance(instance_items, list):
        raise OntologyParseError("instances must be a list")
    if not isinstance(invocation_items, list):
        raise OntologyParseError("action_invocations must be a list")

    entities = [_parse_entity(item, chunk_id) for item in entity_items]
    entity_ids = {entity.id for entity in entities}
    entity_name_to_id = _build_entity_name_index(entities)
    triples = [
        triple
        for triple in (
            _parse_triple(item, chunk_id, entity_ids, entity_name_to_id)
            for item in triple_items
        )
        if triple is not None
    ]
    triples_dropped = len(triple_items) - len(triples)
    rules = [
        rule
        for rule in (
            _parse_rule(item, chunk_id, entity_ids, entity_name_to_id, index)
            for index, item in enumerate(rule_items, start=1)
        )
        if rule is not None
    ]
    rules_dropped = len(rule_items) - len(rules)
    rule_ids = {rule.id for rule in rules}
    rule_name_to_id = {rule.name: rule.id for rule in rules}
    actions = [
        action
        for action in (
            _parse_action(
                item,
                chunk_id,
                entity_ids,
                entity_name_to_id,
                rule_ids,
                rule_name_to_id,
            )
            for item in action_items
        )
        if action is not None
    ]
    actions_dropped = len(action_items) - len(actions)

    # Two-pass instance parsing: first resolve identity/class, then link_values.
    raw_instances: list[tuple[dict[str, Any], str, str]] = []
    for item in instance_items:
        if not isinstance(item, dict):
            continue
        class_payload = _first_present(
            item,
            ["class_id", "class", "className", "object", "object_type", "type"],
        )
        class_id = _resolve_entity_reference(
            class_payload, entity_ids, entity_name_to_id
        )
        if not class_id:
            continue
        instance_id = (
            item.get("id") or item.get("instance_id") or item.get("instanceId")
        )
        name = (
            item.get("name") or item.get("instance_name") or item.get("instanceName")
        )
        if not name:
            continue
        if instance_id is None:
            instance_id = _slug_instance_id(str(name))
        raw_instances.append((item, str(instance_id), class_id))

    instance_ids = {instance_id for _, instance_id, _ in raw_instances}
    instance_name_to_id = _build_unique_name_index(
        (
            str(
                item.get("name")
                or item.get("instance_name")
                or item.get("instanceName")
            ),
            instance_id,
        )
        for item, instance_id, _ in raw_instances
    )

    instances = [
        _parse_instance(item, chunk_id, instance_id, class_id, instance_ids, instance_name_to_id)
        for item, instance_id, class_id in raw_instances
    ]
    instances_dropped = len(instance_items) - len(instances)

    action_name_index = _build_action_name_index([action.name for action in actions])
    invocations = [
        invocation
        for invocation in (
            _parse_action_invocation(
                item,
                chunk_id,
                action_name_index,
                instance_ids,
                instance_name_to_id,
            )
            for item in invocation_items
        )
        if invocation is not None
    ]
    invocations_dropped = len(invocation_items) - len(invocations)

    dropped_total = (
        triples_dropped
        + rules_dropped
        + actions_dropped
        + instances_dropped
        + invocations_dropped
    )
    if dropped_total:
        logger.warning(
            "parser dropped %d items for %s (triples=%d, rules=%d, actions=%d, instances=%d, invocations=%d)",
            dropped_total,
            chunk_id,
            triples_dropped,
            rules_dropped,
            actions_dropped,
            instances_dropped,
            invocations_dropped,
        )

    try:
        return LocalOntology(
            chunk_id=chunk_id,
            entities=entities,
            triples=triples,
            rules=rules,
            actions=actions,
            instances=instances,
            action_invocations=invocations,
            raw_response=raw_response,
        )
    except ValidationError as exc:
        raise OntologyParseError(str(exc)) from exc


def _loads_json(raw_response: str) -> dict[str, Any]:
    candidate = raw_response.strip()
    fenced = FENCED_JSON_RE.search(candidate)
    if fenced:
        candidate = fenced.group(1).strip()
    elif not candidate.startswith("{"):
        candidate = _extract_first_json_object(candidate)
    candidate = _repair_json(candidate)
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise OntologyParseError(
            f"invalid JSON response: {exc}; response_excerpt={_excerpt(raw_response)}"
        ) from exc
    if not isinstance(data, dict):
        raise OntologyParseError("LLM response must be a JSON object")
    return data


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


def _repair_json(candidate: str) -> str:
    return TRAILING_COMMA_RE.sub(r"\1", candidate)


def _excerpt(text: str, limit: int = 300) -> str:
    compact = " ".join(text.strip().split())
    if len(compact) <= limit:
        return compact
    return f"{compact[:limit]}..."


def _parse_entity(item: dict[str, Any], chunk_id: str) -> Entity:
    if not isinstance(item, dict):
        raise OntologyParseError("entity item must be an object")
    primary_key_payload = (
        item.get("primary_key") or item.get("primaryKey") or item.get("primary_key_name")
    )
    primary_key = str(primary_key_payload).strip() if primary_key_payload else None
    try:
        return Entity(
            id=str(item["id"]),
            name=str(item["name"]),
            entity_type=str(item.get("entity_type") or item.get("type") or "Concept"),
            aliases=_parse_aliases(item.get("aliases", [])),
            attributes=_parse_attributes(item.get("attributes", {})),
            primary_key=primary_key or None,
            source_chunks=[chunk_id],
        )
    except (KeyError, TypeError, ValidationError) as exc:
        raise OntologyParseError(f"invalid entity item: {item}") from exc


def _parse_triple(
    item: dict[str, Any],
    chunk_id: str,
    entity_ids: set[str],
    entity_name_to_id: dict[str, str],
) -> Triple | None:
    if not isinstance(item, dict):
        return None

    subject_payload = item.get("subject_id") or item.get("subject")
    subject_id = _resolve_entity_reference(subject_payload, entity_ids, entity_name_to_id)
    predicate = str(item.get("predicate") or "").strip()
    if not subject_id or not predicate:
        return None

    object_payload = _first_present(item, ["object_id", "object_value", "object"])
    object_kind = _normalize_object_kind(
        str(item.get("object_kind") or item.get("object_type") or item.get("type") or "")
    )
    if not object_kind:
        object_kind = (
            "entity"
            if _resolve_entity_reference(object_payload, entity_ids, entity_name_to_id)
            else "literal"
        )

    try:
        if object_kind == "entity":
            object_id = _resolve_entity_reference(
                object_payload,
                entity_ids,
                entity_name_to_id,
            )
            if not object_id:
                return None
            return Triple(
                subject_id=subject_id,
                predicate=predicate,
                object_kind="entity",
                object_id=object_id,
                confidence=float(item.get("confidence", 1.0)),
                cardinality=_normalize_cardinality(item.get("cardinality")),
                source_chunks=[chunk_id],
            )
        if object_kind == "literal":
            if object_payload is None:
                return None
            return Triple(
                subject_id=subject_id,
                predicate=predicate,
                object_kind="literal",
                object_value=object_payload,
                confidence=float(item.get("confidence", 1.0)),
                source_chunks=[chunk_id],
            )
    except (TypeError, ValidationError):
        return None

    return None


def _parse_rule(
    item: Any,
    chunk_id: str,
    entity_ids: set[str],
    entity_name_to_id: dict[str, str],
    fallback_index: int,
) -> OntologyRule | None:
    if not isinstance(item, dict):
        return None
    name = str(item.get("name") or item.get("rule_name") or item.get("ruleName") or "").strip()
    scope_payload = _first_present(
        item,
        ["scope_entity_id", "scopeEntityId", "scope", "scope_object", "scopeObject", "object"],
    )
    scope_id = _resolve_entity_reference(
        scope_payload,
        entity_ids,
        entity_name_to_id,
    )
    if not name or not scope_id:
        return None
    conditions = _parse_rule_conditions(item.get("conditions"))
    if not conditions:
        return None
    logic = str(item.get("logic") or "AND").strip().upper()
    if logic not in {"AND", "OR"}:
        logic = "AND"
    try:
        return OntologyRule(
            id=str(item.get("id") or item.get("rule_id") or f"r{fallback_index}"),
            name=name,
            description=str(item.get("description") or item.get("desc") or ""),
            scope_entity_id=scope_id,
            logic=logic,
            conditions=conditions,
            confidence=float(item.get("confidence", 1.0)),
            source_chunks=[chunk_id],
        )
    except (TypeError, ValueError, ValidationError):
        return None


def _parse_rule_conditions(payload: Any) -> list[RuleCondition]:
    if not isinstance(payload, list):
        return []
    conditions: list[RuleCondition] = []
    for index, item in enumerate(payload, start=1):
        if not isinstance(item, dict):
            continue
        field = str(
            item.get("field") or item.get("attribute") or item.get("property") or ""
        ).strip()
        operator = _normalize_rule_operator(
            str(item.get("operator") or item.get("op") or "")
        )
        value = item.get("value")
        if not field or operator is None:
            continue
        try:
            conditions.append(
                RuleCondition(
                    id=str(item.get("id") or item.get("condition_id") or f"c{index}"),
                    field=field,
                    operator=operator,
                    value=value,
                    value_type=_normalize_value_type(
                        item.get("value_type") or item.get("valueType"),
                        value,
                    ),
                )
            )
        except (TypeError, ValueError, ValidationError):
            continue
    return conditions


def _parse_action(
    item: dict[str, Any],
    chunk_id: str,
    entity_ids: set[str],
    entity_name_to_id: dict[str, str],
    rule_ids: set[str],
    rule_name_to_id: dict[str, str],
) -> OntologyAction | None:
    if not isinstance(item, dict):
        return None

    action_id = item.get("id") or item.get("action_id") or item.get("actionId")
    raw_name = item.get("name") or item.get("action_name") or item.get("actionName")
    name = str(raw_name).strip() if raw_name else ""
    if not name:
        return None
    if action_id is None:
        action_id = _slug_action_id(name)

    input_payload = _first_present(
        item,
        ["inputs", "input_entities", "inputEntityIds", "inputObjects"],
    )
    output_payload = _first_present(
        item,
        ["outputs", "output_entities", "outputEntityIds", "outputObjects"],
    )
    target_payload = _first_present(
        item,
        ["targets", "target_entities", "targetEntityIds", "targetObjects", "target"],
    )
    trigger_payload = _first_present(
        item,
        ["trigger_rules", "triggerRules", "trigger_rule_ids", "triggerRuleIds", "trigger_rule", "triggerRule"],
    )

    try:
        return OntologyAction(
            id=str(action_id),
            name=name,
            description=str(item.get("description") or item.get("desc") or ""),
            input_entity_ids=_resolve_entity_references(
                input_payload,
                entity_ids,
                entity_name_to_id,
            ),
            output_entity_ids=_resolve_entity_references(
                output_payload,
                entity_ids,
                entity_name_to_id,
            ),
            trigger_rule_ids=_resolve_rule_references(
                trigger_payload,
                rule_ids,
                rule_name_to_id,
            ),
            target_entity_ids=_resolve_entity_references(
                target_payload,
                entity_ids,
                entity_name_to_id,
            ),
            action_type=str(item.get("action_type") or item.get("actionType") or "ontology"),
            executor=str(item.get("executor") or ""),
            action_plan=str(item.get("action_plan") or item.get("actionPlan") or ""),
            feedback=str(item.get("feedback") or ""),
            attributes=_parse_attributes(item.get("attributes", {})),
            source_chunks=[chunk_id],
        )
    except (TypeError, ValidationError):
        return None


def _parse_instance(
    item: dict[str, Any],
    chunk_id: str,
    instance_id: str,
    class_id: str,
    instance_ids: set[str],
    instance_name_to_id: dict[str, str],
) -> OntologyInstance:
    name = item.get("name") or item.get("instance_name") or item.get("instanceName")
    attribute_values = _parse_instance_attribute_values(
        _first_present(item, ["attribute_values", "attributeValues"]),
        item.get("attributes"),
        chunk_id,
    )
    link_values = _parse_instance_link_values(
        _first_present(item, ["link_values", "linkValues", "links", "relations"]),
        chunk_id,
        instance_ids,
        instance_name_to_id,
    )

    try:
        return OntologyInstance(
            id=instance_id,
            name=str(name),
            class_id=class_id,
            attribute_values=attribute_values,
            link_values=link_values,
            generated=bool(item.get("generated", False)),
            source_chunks=[chunk_id],
        )
    except (TypeError, ValidationError) as exc:
        raise OntologyParseError(f"invalid instance item: {item}") from exc


def _parse_action_invocation(
    item: Any,
    chunk_id: str,
    action_name_index: dict[str, str],
    instance_ids: set[str],
    instance_name_to_id: dict[str, str],
) -> ActionInvocation | None:
    if not isinstance(item, dict):
        return None

    action_payload = _first_present(
        item,
        ["action", "action_name", "actionName", "name"],
    )
    if action_payload is None:
        return None
    raw_action_name = str(action_payload).strip()
    if not raw_action_name:
        return None
    # Resolve to the canonical action name when an exact or normalized match
    # exists; otherwise keep the LLM's spelling so downstream can still surface
    # the invocation with actionId=None instead of dropping it silently.
    action_name = (
        action_name_index.get(raw_action_name)
        or action_name_index.get(_normalize_action_key(raw_action_name))
        or raw_action_name
    )

    invocation_id = (
        item.get("id")
        or item.get("invocation_id")
        or item.get("invocationId")
        or _slug_invocation_id(action_name)
    )

    inputs = _resolve_instance_references(
        _first_present(item, ["inputs", "input_instances", "inputInstanceIds"]),
        instance_ids,
        instance_name_to_id,
    )
    outputs = _resolve_instance_references(
        _first_present(item, ["outputs", "output_instances", "outputInstanceIds"]),
        instance_ids,
        instance_name_to_id,
    )

    try:
        return ActionInvocation(
            id=str(invocation_id),
            action_name=action_name,
            input_instance_ids=inputs,
            output_instance_ids=outputs,
            attributes=_parse_attributes(item.get("attributes", {})),
            source_chunks=[chunk_id],
        )
    except (TypeError, ValidationError):
        return None


def _parse_instance_attribute_values(
    values_payload: Any,
    legacy_attributes: Any,
    chunk_id: str,
) -> list[InstanceAttributeValue]:
    items: list[InstanceAttributeValue] = []
    seen: set[str] = set()

    if isinstance(values_payload, list):
        for entry in values_payload:
            if not isinstance(entry, dict):
                continue
            name = entry.get("attribute_name") or entry.get("name") or entry.get("attributeName")
            if not name:
                continue
            name_str = str(name)
            if name_str in seen:
                continue
            value = entry.get("value") if "value" in entry else entry.get("attribute_value")
            data_type = str(
                entry.get("data_type") or entry.get("dataType") or _infer_data_type(value)
            )
            try:
                items.append(
                    InstanceAttributeValue(
                        attribute_name=name_str,
                        value=value,
                        data_type=data_type,
                        confidence=float(entry.get("confidence", 1.0)),
                        source_chunks=[chunk_id],
                    )
                )
            except (TypeError, ValueError, ValidationError):
                continue
            seen.add(name_str)

    if isinstance(legacy_attributes, dict):
        for name, value in legacy_attributes.items():
            name_str = str(name)
            if not name_str or name_str in seen:
                continue
            try:
                items.append(
                    InstanceAttributeValue(
                        attribute_name=name_str,
                        value=value,
                        data_type=_infer_data_type(value),
                        source_chunks=[chunk_id],
                    )
                )
            except ValidationError:
                continue
            seen.add(name_str)

    return items


def _parse_instance_link_values(
    payload: Any,
    chunk_id: str,
    instance_ids: set[str],
    instance_name_to_id: dict[str, str],
) -> list[InstanceLinkValue]:
    if not isinstance(payload, list):
        return []
    links: list[InstanceLinkValue] = []
    seen: set[tuple[str, str]] = set()
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        predicate_payload = (
            entry.get("predicate") or entry.get("linkInfo") or entry.get("name")
        )
        predicate = str(predicate_payload).strip() if predicate_payload else ""
        target_payload = _first_present(
            entry,
            ["target_instance_id", "targetInstanceId", "target", "object"],
        )
        target = _resolve_instance_reference(
            target_payload, instance_ids, instance_name_to_id
        )
        if not predicate or not target:
            continue
        key = (predicate, target)
        if key in seen:
            continue
        seen.add(key)
        try:
            links.append(
                InstanceLinkValue(
                    predicate=predicate,
                    target_instance_id=target,
                    confidence=float(entry.get("confidence", 1.0)),
                    source_chunks=[chunk_id],
                )
            )
        except (TypeError, ValueError, ValidationError):
            continue
    return links


def _first_present(item: dict[str, Any], keys: list[str]) -> Any:
    for key in keys:
        if key in item:
            return item[key]
    return None


def _build_entity_name_index(entities: list[Entity]) -> dict[str, str]:
    index: dict[str, str] = {}
    for entity in entities:
        index[entity.name] = entity.id
        for alias in entity.aliases:
            index[alias] = entity.id
    return index


def _build_unique_name_index(pairs: Any) -> dict[str, str]:
    # Index `name -> id` only when the name unambiguously identifies one id.
    # Names that collide (different ids share the same name) are removed so
    # that link/invocation references fall back to id-based resolution rather
    # than silently picking the wrong target.
    candidates: dict[str, str] = {}
    blocked: set[str] = set()
    for name, identifier in pairs:
        if not name or name in blocked:
            continue
        existing = candidates.get(name)
        if existing is None:
            candidates[name] = identifier
            continue
        if existing != identifier:
            blocked.add(name)
            candidates.pop(name, None)
    return candidates


def _build_action_name_index(action_names: list[str]) -> dict[str, str]:
    # Map both exact and normalized spellings back to the canonical action
    # name so that invocations can find their action even when the LLM uses
    # a slight variant ("创建订单" vs "新建订单 ").
    index: dict[str, str] = {}
    for name in action_names:
        if not name:
            continue
        index.setdefault(name, name)
        index.setdefault(_normalize_action_key(name), name)
    return index


def _normalize_action_key(value: str) -> str:
    return "".join(value.split()).lower()


def _resolve_entity_reference(
    value: Any,
    entity_ids: set[str],
    entity_name_to_id: dict[str, str],
) -> str | None:
    if value is None:
        return None
    value_str = str(value)
    if value_str in entity_ids:
        return value_str
    return entity_name_to_id.get(value_str)


def _resolve_entity_references(
    value: Any,
    entity_ids: set[str],
    entity_name_to_id: dict[str, str],
) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        candidates = value
    else:
        candidates = [value]
    resolved: list[str] = []
    for candidate in candidates:
        entity_id = _resolve_entity_reference(candidate, entity_ids, entity_name_to_id)
        if entity_id:
            resolved.append(entity_id)
    return list(dict.fromkeys(resolved))


def _resolve_rule_references(
    value: Any,
    rule_ids: set[str],
    rule_name_to_id: dict[str, str],
) -> list[str]:
    if value is None:
        return []
    candidates = value if isinstance(value, list) else [value]
    resolved: list[str] = []
    for candidate in candidates:
        candidate_text = str(candidate).strip()
        rule_id = (
            candidate_text
            if candidate_text in rule_ids
            else rule_name_to_id.get(candidate_text)
        )
        if rule_id:
            resolved.append(rule_id)
    return list(dict.fromkeys(resolved))


def _resolve_instance_reference(
    value: Any,
    instance_ids: set[str],
    instance_name_to_id: dict[str, str],
) -> str | None:
    if value is None:
        return None
    value_str = str(value)
    if value_str in instance_ids:
        return value_str
    return instance_name_to_id.get(value_str)


def _resolve_instance_references(
    value: Any,
    instance_ids: set[str],
    instance_name_to_id: dict[str, str],
) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        candidates = value
    else:
        candidates = [value]
    resolved: list[str] = []
    for candidate in candidates:
        resolved_id = _resolve_instance_reference(
            candidate, instance_ids, instance_name_to_id
        )
        if resolved_id:
            resolved.append(resolved_id)
    return list(dict.fromkeys(resolved))


_CARDINALITY_ALIASES = {
    "1:1": "ONE_TO_ONE",
    "1-1": "ONE_TO_ONE",
    "onetoone": "ONE_TO_ONE",
    "1:n": "ONE_TO_MANY",
    "1-n": "ONE_TO_MANY",
    "1:m": "ONE_TO_MANY",
    "onetomany": "ONE_TO_MANY",
    "n:1": "MANY_TO_ONE",
    "n-1": "MANY_TO_ONE",
    "m:1": "MANY_TO_ONE",
    "manytoone": "MANY_TO_ONE",
    "n:n": "MANY_TO_MANY",
    "n:m": "MANY_TO_MANY",
    "m:n": "MANY_TO_MANY",
    "manytomany": "MANY_TO_MANY",
}
_CARDINALITY_CANONICAL = {
    "ONE_TO_ONE",
    "ONE_TO_MANY",
    "MANY_TO_ONE",
    "MANY_TO_MANY",
}


def _normalize_cardinality(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    upper = text.upper()
    if upper in _CARDINALITY_CANONICAL:
        return upper
    collapsed = re.sub(r"[\s_-]+", "", text.lower())
    if collapsed in _CARDINALITY_ALIASES:
        return _CARDINALITY_ALIASES[collapsed]
    return _CARDINALITY_ALIASES.get(text.lower())


def _normalize_object_kind(value: str) -> str:
    normalized = value.strip().lower()
    if normalized in {"entity", "relation", "relationship"}:
        return "entity"
    if normalized in {"literal", "attribute", "value"}:
        return "literal"
    return normalized


def _normalize_rule_operator(value: str) -> str | None:
    normalized = value.strip().lower().replace(" ", "")
    aliases = {
        "为空": "为空",
        "isnull": "为空",
        "null": "为空",
        "不为空": "不为空",
        "notnull": "不为空",
        "isnotnull": "不为空",
        "包含": "包含",
        "contains": "包含",
        "不包含": "不包含",
        "notcontains": "不包含",
        "=": "=",
        "==": "=",
        "eq": "=",
        "!=": "!=",
        "≠": "!=",
        "ne": "!=",
        ">": ">",
        "gt": ">",
        ">=": ">=",
        "≥": ">=",
        "gte": ">=",
        "<": "<",
        "lt": "<",
        "<=": "<=",
        "≤": "<=",
        "lte": "<=",
    }
    return aliases.get(normalized)


def _normalize_value_type(value_type: Any, value: Any) -> str:
    normalized = str(value_type or "").strip().lower()
    aliases = {
        "str": "string",
        "string": "string",
        "text": "string",
        "int": "integer",
        "integer": "integer",
        "float": "number",
        "double": "number",
        "decimal": "number",
        "number": "number",
        "bool": "boolean",
        "boolean": "boolean",
    }
    return aliases.get(normalized) or _infer_data_type(value)


def _parse_aliases(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(alias) for alias in value if alias]
    return [str(value)]


def _parse_attributes(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    return {}


def _infer_data_type(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    return "string"


def _slug_action_id(name: str) -> str:
    return f"action_{_stable_suffix(name)}"


def _slug_instance_id(name: str) -> str:
    return f"instance_{_stable_suffix(name)}"


def _slug_invocation_id(action_name: str) -> str:
    return f"invocation_{_stable_suffix(action_name)}"


def _stable_suffix(value: str) -> str:
    return str(sum((index + 1) * ord(char) for index, char in enumerate(value)))
