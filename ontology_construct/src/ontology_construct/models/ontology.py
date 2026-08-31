from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


ObjectKind = Literal["entity", "literal"]
DataType = Literal["string", "integer", "number", "boolean"]
RuleLogic = Literal["AND", "OR"]
RuleOperator = Literal[
    "为空",
    "不为空",
    "包含",
    "不包含",
    "=",
    "!=",
    ">",
    ">=",
    "<",
    "<=",
]


class Entity(BaseModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    entity_type: str = Field(default="Concept", min_length=1)
    aliases: list[str] = Field(default_factory=list)
    attributes: dict[str, Any] = Field(default_factory=dict)
    # LLM-suggested primary key (a property name). Used by the canonical export;
    # falls back to a heuristic when absent.
    primary_key: str | None = None
    source_chunks: list[str] = Field(default_factory=list)

    @field_validator("aliases", "source_chunks")
    @classmethod
    def deduplicate_strings(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(value for value in values if value))


class Triple(BaseModel):
    subject_id: str = Field(min_length=1)
    predicate: str = Field(min_length=1)
    object_kind: ObjectKind = "entity"
    object_id: str | None = None
    object_value: str | int | float | bool | None = None
    confidence: float = Field(default=1.0, ge=0, le=1)
    # LLM-suggested cardinality for entity links (ONE_TO_ONE/ONE_TO_MANY/
    # MANY_TO_ONE/MANY_TO_MANY). Used by the canonical export; falls back to
    # instance-derived inference when absent.
    cardinality: str | None = None
    source_chunks: list[str] = Field(default_factory=list)

    @field_validator("source_chunks")
    @classmethod
    def deduplicate_source_chunks(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(value for value in values if value))

    @model_validator(mode="after")
    def validate_object_payload(self) -> "Triple":
        if self.object_kind == "entity" and not self.object_id:
            raise ValueError("entity triple requires object_id")
        if self.object_kind == "literal" and self.object_value is None:
            raise ValueError("literal triple requires object_value")
        return self


class RuleCondition(BaseModel):
    id: str = Field(min_length=1)
    field: str = Field(min_length=1)
    operator: RuleOperator
    value: str | int | float | bool | None = None
    value_type: DataType = "string"

    @model_validator(mode="after")
    def validate_value(self) -> "RuleCondition":
        if self.operator not in {"为空", "不为空"} and self.value is None:
            raise ValueError("rule condition requires value for this operator")
        return self


class OntologyRule(BaseModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = ""
    scope_entity_id: str = Field(min_length=1)
    logic: RuleLogic = "AND"
    conditions: list[RuleCondition] = Field(default_factory=list)
    confidence: float = Field(default=1.0, ge=0, le=1)
    source_chunks: list[str] = Field(default_factory=list)

    @field_validator("source_chunks")
    @classmethod
    def deduplicate_source_chunks(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(value for value in values if value))


class OntologyAction(BaseModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = ""
    input_entity_ids: list[str] = Field(default_factory=list)
    output_entity_ids: list[str] = Field(default_factory=list)
    trigger_rule_ids: list[str] = Field(default_factory=list)
    target_entity_ids: list[str] = Field(default_factory=list)
    action_type: str = "ontology"
    executor: str = ""
    action_plan: str = ""
    feedback: str = ""
    attributes: dict[str, Any] = Field(default_factory=dict)
    source_chunks: list[str] = Field(default_factory=list)

    @field_validator(
        "input_entity_ids",
        "output_entity_ids",
        "trigger_rule_ids",
        "target_entity_ids",
        "source_chunks",
    )
    @classmethod
    def deduplicate_strings(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(value for value in values if value))


class InstanceAttributeValue(BaseModel):
    attribute_name: str = Field(min_length=1)
    value: str | int | float | bool | None = None
    data_type: DataType = "string"
    confidence: float = Field(default=1.0, ge=0, le=1)
    source_chunks: list[str] = Field(default_factory=list)

    @field_validator("value", mode="before")
    @classmethod
    def coerce_scalar_value(cls, value: Any) -> Any:
        # LLMs sometimes return an attribute value as a list (multiple values)
        # or an object. The schema only stores scalars, so flatten those to a
        # string instead of failing the whole build.
        if isinstance(value, (list, tuple, set)):
            return "、".join(str(item).strip() for item in value if str(item).strip())
        if isinstance(value, dict):
            return json.dumps(value, ensure_ascii=False)
        return value

    @field_validator("source_chunks")
    @classmethod
    def deduplicate_source_chunks(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(value for value in values if value))


class InstanceLinkValue(BaseModel):
    predicate: str = Field(min_length=1)
    target_instance_id: str = Field(min_length=1)
    confidence: float = Field(default=1.0, ge=0, le=1)
    source_chunks: list[str] = Field(default_factory=list)

    @field_validator("source_chunks")
    @classmethod
    def deduplicate_source_chunks(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(value for value in values if value))


class OntologyInstance(BaseModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    class_id: str = Field(min_length=1)
    attribute_values: list[InstanceAttributeValue] = Field(default_factory=list)
    link_values: list[InstanceLinkValue] = Field(default_factory=list)
    generated: bool = False
    source_chunks: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_payload(cls, data: Any) -> Any:
        # Older runs persisted instances with `attributes: dict` and no `class_id`.
        # Fold the dict into attribute_values and let downstream filters drop the
        # ones that still lack class_id rather than crashing on load.
        if not isinstance(data, dict):
            return data
        legacy_attributes = data.get("attributes")
        if isinstance(legacy_attributes, dict) and legacy_attributes:
            existing = list(data.get("attribute_values") or [])
            seen = {
                entry.get("attribute_name") or entry.get("name")
                for entry in existing
                if isinstance(entry, dict)
            }
            for name, value in legacy_attributes.items():
                if name in seen:
                    continue
                existing.append({"attribute_name": str(name), "value": value})
                seen.add(name)
            data["attribute_values"] = existing
        if not data.get("class_id"):
            data["class_id"] = "__unbound__"
        return data

    @field_validator("source_chunks")
    @classmethod
    def deduplicate_source_chunks(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(value for value in values if value))

    @property
    def is_unbound(self) -> bool:
        return self.class_id == "__unbound__"


class ActionInvocation(BaseModel):
    id: str = Field(min_length=1)
    action_name: str = Field(min_length=1)
    input_instance_ids: list[str] = Field(default_factory=list)
    output_instance_ids: list[str] = Field(default_factory=list)
    attributes: dict[str, Any] = Field(default_factory=dict)
    source_chunks: list[str] = Field(default_factory=list)

    @field_validator("input_instance_ids", "output_instance_ids", "source_chunks")
    @classmethod
    def deduplicate_strings(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(value for value in values if value))


class LocalOntology(BaseModel):
    chunk_id: str
    entities: list[Entity] = Field(default_factory=list)
    triples: list[Triple] = Field(default_factory=list)
    rules: list[OntologyRule] = Field(default_factory=list)
    actions: list[OntologyAction] = Field(default_factory=list)
    instances: list[OntologyInstance] = Field(default_factory=list)
    action_invocations: list[ActionInvocation] = Field(default_factory=list)
    raw_response: str | None = None


class GlobalOntology(BaseModel):
    entities: dict[str, Entity] = Field(default_factory=dict)
    triples: list[Triple] = Field(default_factory=list)
    rules: list[OntologyRule] = Field(default_factory=list)
    actions: list[OntologyAction] = Field(default_factory=list)
    instances: list[OntologyInstance] = Field(default_factory=list)
    action_invocations: list[ActionInvocation] = Field(default_factory=list)
    name_to_id: dict[str, str] = Field(default_factory=dict)

    @classmethod
    def from_entities_and_triples(
        cls,
        entities: list[Entity],
        triples: list[Triple],
        actions: list[OntologyAction] | None = None,
        instances: list[OntologyInstance] | None = None,
        action_invocations: list[ActionInvocation] | None = None,
        rules: list[OntologyRule] | None = None,
    ) -> "GlobalOntology":
        entity_map = {entity.id: entity for entity in entities}
        name_to_id: dict[str, str] = {}
        for entity in entities:
            name_to_id[entity.name] = entity.id
            for alias in entity.aliases:
                name_to_id[alias] = entity.id
        return cls(
            entities=entity_map,
            triples=triples,
            rules=rules or [],
            actions=actions or [],
            instances=instances or [],
            action_invocations=action_invocations or [],
            name_to_id=name_to_id,
        )
