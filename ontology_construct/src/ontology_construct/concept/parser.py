from __future__ import annotations

import json
import re
from typing import Any

from pydantic import ValidationError

from ontology_construct.concept.models import (
    ChunkConceptCandidates,
    ConceptActionCandidate,
    ConceptCandidate,
    ConceptInstanceCandidate,
    ConceptRuleCandidate,
    RelationCandidate,
    RuleConditionCandidate,
)


class ConceptParseError(ValueError):
    pass


FENCED_JSON_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)
TRAILING_COMMA_RE = re.compile(r",\s*([}\]])")


def parse_concept_candidates(
    raw_response: str,
    chunk_id: str,
    *,
    max_candidates: int | None = None,
    relation_vocab: list[str] | None = None,
) -> ChunkConceptCandidates:
    data = _loads_json(raw_response)
    concept_items = _first_present(data, ["concepts", "candidates", "entities"]) or []
    relation_items = _first_present(data, ["relations", "relationships", "triples"]) or []
    if not isinstance(concept_items, list):
        raise ConceptParseError("concepts must be a list")
    if not isinstance(relation_items, list):
        raise ConceptParseError("relations must be a list")

    concepts = [
        concept
        for concept in (_parse_concept(item) for item in concept_items)
        if concept is not None and concept.should_be_class
    ]
    if max_candidates is not None:
        concepts = concepts[:max_candidates]
    concept_names = {concept.name for concept in concepts}
    alias_to_name = {
        alias: concept.name
        for concept in concepts
        for alias in [concept.name, *concept.aliases]
    }
    relations = [
        relation
        for relation in (
            _parse_relation(item, alias_to_name, concept_names, relation_vocab)
            for item in relation_items
        )
        if relation is not None
    ]
    instance_items = _first_present(data, ["instances", "examples", "individuals"]) or []
    instances = (
        _parse_instance_items(instance_items, list(alias_to_name.keys()))
        if isinstance(instance_items, list)
        else []
    )
    rule_items = _first_present(data, ["rules", "business_rules", "policies"]) or []
    action_items = _first_present(data, ["actions", "operations"]) or []
    rules = (
        _parse_rule_items(rule_items, alias_to_name, concept_names)
        if isinstance(rule_items, list)
        else []
    )
    actions = (
        _parse_action_items(action_items, alias_to_name, concept_names, rules)
        if isinstance(action_items, list)
        else []
    )
    return ChunkConceptCandidates(
        chunk_id=chunk_id,
        concepts=concepts,
        relations=relations,
        instances=instances,
        rules=rules,
        actions=actions,
        raw_response=raw_response,
    )


def _parse_rule_items(
    rule_items: list[Any],
    alias_to_name: dict[str, str],
    concept_names: set[str],
) -> list[ConceptRuleCandidate]:
    rules: list[ConceptRuleCandidate] = []
    for index, item in enumerate(rule_items, start=1):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("rule_name") or "").strip()
        scope = str(
            item.get("scope")
            or item.get("scope_object")
            or item.get("scopeObject")
            or item.get("object")
            or ""
        ).strip()
        scope = alias_to_name.get(scope, scope)
        if not name or scope not in concept_names:
            continue
        conditions = _parse_rule_conditions(item.get("conditions") or [])
        if not conditions:
            continue
        logic = str(item.get("logic") or "AND").strip().upper()
        if logic not in {"AND", "OR"}:
            logic = "AND"
        try:
            rules.append(
                ConceptRuleCandidate(
                    id=str(item.get("id") or item.get("rule_id") or f"r{index}"),
                    name=name,
                    description=str(item.get("description") or item.get("desc") or ""),
                    scope=scope,
                    logic=logic,
                    conditions=conditions,
                    evidence=_parse_strings(item.get("evidence", [])),
                    confidence=float(item.get("confidence", 1.0)),
                )
            )
        except (TypeError, ValueError, ValidationError):
            continue
    return rules


def _parse_rule_conditions(items: Any) -> list[RuleConditionCandidate]:
    if not isinstance(items, list):
        return []
    conditions: list[RuleConditionCandidate] = []
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            continue
        field = str(
            item.get("field")
            or item.get("attribute")
            or item.get("property")
            or ""
        ).strip()
        operator = _normalize_rule_operator(
            str(item.get("operator") or item.get("op") or "")
        )
        if not field or operator is None:
            continue
        value = item.get("value")
        if value is None and operator not in {"为空", "不为空"}:
            continue
        raw_value_type = item.get("value_type") or item.get("valueType")
        value_type = _normalize_value_type(raw_value_type, value)
        try:
            conditions.append(
                RuleConditionCandidate(
                    id=str(item.get("id") or item.get("condition_id") or f"c{index}"),
                    field=field,
                    operator=operator,
                    value=value,
                    value_type=value_type,
                )
            )
        except (TypeError, ValueError, ValidationError):
            continue
    return conditions


def _parse_action_items(
    action_items: list[Any],
    alias_to_name: dict[str, str],
    concept_names: set[str],
    rules: list[ConceptRuleCandidate],
) -> list[ConceptActionCandidate]:
    rule_refs = {rule.id for rule in rules} | {rule.name for rule in rules}
    actions: list[ConceptActionCandidate] = []
    for index, item in enumerate(action_items, start=1):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("action_name") or "").strip()
        if not name:
            continue

        def concept_refs(*keys: str) -> list[str]:
            payload = _first_present(item, list(keys))
            values = _parse_strings(payload or [])
            resolved = [alias_to_name.get(value, value) for value in values]
            return list(dict.fromkeys(value for value in resolved if value in concept_names))

        trigger_payload = _first_present(
            item,
            ["trigger_rules", "triggerRules", "trigger_rule", "triggerRule"],
        )
        trigger_rules = [
            value for value in _parse_strings(trigger_payload or []) if value in rule_refs
        ]
        try:
            actions.append(
                ConceptActionCandidate(
                    id=str(item.get("id") or item.get("action_id") or f"a{index}"),
                    name=name,
                    description=str(item.get("description") or item.get("desc") or ""),
                    inputs=concept_refs("inputs", "input_objects", "inputObjects"),
                    outputs=concept_refs("outputs", "output_objects", "outputObjects"),
                    targets=concept_refs("targets", "target_objects", "targetObjects", "target"),
                    trigger_rules=trigger_rules,
                    action_type=str(item.get("action_type") or item.get("actionType") or "ontology"),
                    executor=str(item.get("executor") or ""),
                    action_plan=str(item.get("action_plan") or item.get("actionPlan") or ""),
                    feedback=str(item.get("feedback") or ""),
                    evidence=_parse_strings(item.get("evidence", [])),
                )
            )
        except (TypeError, ValueError, ValidationError):
            continue
    return actions


def parse_controlled_relations(
    raw_response: str,
    *,
    allowed_concepts: list[str],
    relation_vocab: list[str] | None = None,
) -> list[RelationCandidate]:
    data = _loads_json(raw_response)
    relation_items = _first_present(data, ["relations", "relationships", "triples"]) or []
    if not isinstance(relation_items, list):
        raise ConceptParseError("relations must be a list")
    concept_names = set(allowed_concepts)
    alias_to_name = {name: name for name in allowed_concepts}
    return [
        relation
        for relation in (
            _parse_relation(item, alias_to_name, concept_names, relation_vocab)
            for item in relation_items
        )
        if relation is not None
    ]


def parse_concept_instances(
    raw_response: str,
    *,
    allowed_concepts: list[str],
) -> list[ConceptInstanceCandidate]:
    data = _loads_json(raw_response)
    instance_items = _first_present(data, ["instances", "examples", "individuals"]) or []
    if not isinstance(instance_items, list):
        raise ConceptParseError("instances must be a list")
    return _parse_instance_items(instance_items, allowed_concepts)


def _parse_instance_items(
    instance_items: list[Any],
    allowed_concepts: list[str],
) -> list[ConceptInstanceCandidate]:
    allowed = set(allowed_concepts)
    parsed_items: list[tuple[dict[str, Any], ConceptInstanceCandidate]] = []
    for item in instance_items:
        if not isinstance(item, dict):
            continue
        concept = str(
            item.get("concept")
            or item.get("class")
            or item.get("object")
            or item.get("type")
            or ""
        ).strip()
        name = str(item.get("name") or item.get("instance") or item.get("label") or "").strip()
        if concept not in allowed or not name:
            continue
        attributes = item.get("attributes") or {}
        if not isinstance(attributes, dict):
            attributes = {}
        try:
            parsed_items.append(
                (
                    item,
                    ConceptInstanceCandidate(
                        concept=concept,
                        name=name,
                        attributes=attributes,
                        evidence=_parse_strings(item.get("evidence", [])),
                        confidence=float(item.get("confidence", 1.0)),
                    ),
                )
            )
        except (TypeError, ValueError, ValidationError):
            continue
    instance_names = {candidate.name for _item, candidate in parsed_items}
    resolved: list[ConceptInstanceCandidate] = []
    for item, candidate in parsed_items:
        links = [
            link
            for link in (
                _parse_instance_link(link_item, candidate.name, instance_names)
                for link_item in item.get("links") or item.get("relations") or []
            )
            if link is not None
        ]
        resolved.append(candidate.model_copy(update={"links": links}))
    return resolved


def parse_instance_relations(
    raw_response: str,
    *,
    allowed_instances: list[str],
    relation_vocab: list[str] | None = None,
) -> list[RelationCandidate]:
    data = _loads_json(raw_response)
    relation_items = _first_present(data, ["relations", "relationships", "triples"]) or []
    if not isinstance(relation_items, list):
        raise ConceptParseError("relations must be a list")
    instance_names = set(allowed_instances)
    alias_to_name = {name: name for name in allowed_instances}
    return [
        relation
        for relation in (
            _parse_relation(item, alias_to_name, instance_names, relation_vocab)
            for item in relation_items
        )
        if relation is not None
    ]


def _parse_instance_link(
    item: Any,
    default_source: str,
    instance_names: set[str],
) -> RelationCandidate | None:
    if not isinstance(item, dict):
        return None
    source = str(item.get("source") or default_source).strip()
    target = str(item.get("target") or "").strip()
    predicate = str(item.get("predicate") or item.get("relation") or item.get("rel") or "").strip()
    if source not in instance_names or target not in instance_names or not predicate:
        return None
    try:
        return RelationCandidate(
            source=source,
            predicate=predicate,
            target=target,
            evidence=_parse_strings(item.get("evidence", [])),
            confidence=float(item.get("confidence", 1.0)),
        )
    except (TypeError, ValueError, ValidationError):
        return None


def _parse_concept(item: Any) -> ConceptCandidate | None:
    if not isinstance(item, dict):
        return None
    name = str(item.get("name") or item.get("concept") or item.get("label") or "").strip()
    if not name:
        return None
    definition = str(item.get("definition") or item.get("description") or "").strip()
    raw_level = item.get("abstraction_level") or item.get("abstractionLevel") or "medium"
    raw_class = item.get("should_be_class")
    if raw_class is None:
        raw_class = item.get("shouldBeClass", True)
    try:
        return ConceptCandidate(
            name=name,
            definition=definition,
            abstraction_level=str(raw_level or "medium"),
            aliases=_parse_strings(item.get("aliases", [])),
            evidence=_parse_strings(item.get("evidence", [])),
            should_be_class=bool(raw_class),
        )
    except ValidationError:
        return None


def _parse_relation(
    item: Any,
    alias_to_name: dict[str, str],
    concept_names: set[str],
    relation_vocab: list[str] | None,
) -> RelationCandidate | None:
    if not isinstance(item, dict):
        return None
    source = str(item.get("source") or item.get("subject") or "").strip()
    target = str(item.get("target") or item.get("object") or "").strip()
    predicate = str(item.get("predicate") or item.get("relation") or item.get("rel") or "").strip()
    if not source or not target or not predicate:
        return None
    source = alias_to_name.get(source, source)
    target = alias_to_name.get(target, target)
    if source not in concept_names or target not in concept_names:
        return None
    if relation_vocab and predicate not in relation_vocab:
        predicate = _closest_vocab(predicate, relation_vocab)
    try:
        return RelationCandidate(
            source=source,
            predicate=predicate,
            target=target,
            evidence=_parse_strings(item.get("evidence", [])),
            confidence=float(item.get("confidence", 1.0)),
        )
    except (TypeError, ValueError, ValidationError):
        return None


def _closest_vocab(predicate: str, relation_vocab: list[str]) -> str:
    for item in relation_vocab:
        if item in predicate or predicate in item:
            return item
    return "关联" if "关联" in relation_vocab else relation_vocab[0]


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
    if normalized in aliases:
        return aliases[normalized]
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    return "string"


def _parse_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _first_present(data: dict[str, Any], keys: list[str]) -> Any:
    for key in keys:
        if key in data:
            return data[key]
    return None


def _loads_json(raw_response: str) -> dict[str, Any]:
    candidate = raw_response.strip()
    fenced = FENCED_JSON_RE.search(candidate)
    if fenced:
        candidate = fenced.group(1).strip()
    elif not candidate.startswith("{"):
        candidate = _extract_first_json_object(candidate)
    candidate = TRAILING_COMMA_RE.sub(r"\1", candidate)
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise ConceptParseError(f"invalid JSON response: {exc}") from exc
    if not isinstance(data, dict):
        raise ConceptParseError("LLM response must be a JSON object")
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
