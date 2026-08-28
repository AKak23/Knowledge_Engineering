from __future__ import annotations

import json
import re
from typing import Any

from pydantic import ValidationError

from ontology_construct.concept.models import (
    ChunkConceptCandidates,
    ConceptCandidate,
    ConceptInstanceCandidate,
    RelationCandidate,
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
    return ChunkConceptCandidates(
        chunk_id=chunk_id,
        concepts=concepts,
        relations=relations,
        instances=instances,
        raw_response=raw_response,
    )


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
