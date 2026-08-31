from __future__ import annotations

from datetime import datetime
import re
from typing import Any

from pydantic import BaseModel, Field

from ontology_construct.models import (
    ActionInvocation,
    Entity,
    GlobalOntology,
    InstanceAttributeValue,
    InstanceLinkValue,
    OntologyInstance,
)


class OntologyMetadata(BaseModel):
    ontologyId: int = 1
    ontologyName: str
    ontologyDesc: str
    profile: str = "default"
    createdAt: str
    updatedAt: str


class FormattedAttribute(BaseModel):
    attributeId: int
    attributeName: str
    attributeCode: str
    dataType: str = "string"
    description: str = ""
    sampleValue: Any = None
    sourceChunks: list[str] = Field(default_factory=list)


class FormattedObject(BaseModel):
    objectId: int
    objectName: str
    objectCode: str
    description: str = ""
    attributes: list[FormattedAttribute] = Field(default_factory=list)
    instanceIds: list[int] = Field(default_factory=list)
    instanceCount: int = 0
    sourceChunks: list[str] = Field(default_factory=list)


class FormattedLink(BaseModel):
    linkId: int
    sourceObjectId: int
    sourceObjectName: str
    targetObjectId: int
    targetObjectName: str
    linkInfo: str
    linkType: str = "N:N"
    description: str = ""
    confidence: float = 1.0
    sourceChunks: list[str] = Field(default_factory=list)


class FormattedAction(BaseModel):
    actionId: int
    actionName: str
    actionCode: str
    description: str = ""
    inputObjectIds: list[int] = Field(default_factory=list)
    inputObjects: list[str] = Field(default_factory=list)
    outputObjectIds: list[int] = Field(default_factory=list)
    outputObjects: list[str] = Field(default_factory=list)
    triggerRuleIds: list[int] = Field(default_factory=list)
    triggerRules: list[str] = Field(default_factory=list)
    targetObjectIds: list[int] = Field(default_factory=list)
    targetObjects: list[str] = Field(default_factory=list)
    actionType: str = "ontology"
    executor: str = ""
    actionPlan: str = ""
    feedback: str = ""
    sourceChunks: list[str] = Field(default_factory=list)


class FormattedRuleCondition(BaseModel):
    conditionId: str
    field: str
    operator: str
    value: Any = None
    valueType: str = "string"


class FormattedRule(BaseModel):
    ruleId: int
    ruleName: str
    ruleCode: str
    description: str = ""
    scopeObjectId: int
    scopeObjectName: str
    logic: str = "AND"
    conditions: list[FormattedRuleCondition] = Field(default_factory=list)
    actionIds: list[int] = Field(default_factory=list)
    actionNames: list[str] = Field(default_factory=list)
    confidence: float = 1.0
    sourceChunks: list[str] = Field(default_factory=list)


class FormattedInstanceAttributeValue(BaseModel):
    attributeId: int | None = None
    attributeName: str
    attributeCode: str
    dataType: str = "string"
    value: Any = None
    sourceChunks: list[str] = Field(default_factory=list)


class FormattedInstanceLinkValue(BaseModel):
    linkId: int | None = None
    linkInfo: str
    targetInstanceId: int
    targetInstanceName: str
    targetObjectId: int | None = None
    targetObjectName: str | None = None
    confidence: float = 1.0
    sourceChunks: list[str] = Field(default_factory=list)


class FormattedInstance(BaseModel):
    instanceId: int
    instanceCode: str
    instanceName: str
    objectId: int
    objectName: str
    attributeValues: list[FormattedInstanceAttributeValue] = Field(default_factory=list)
    linkValues: list[FormattedInstanceLinkValue] = Field(default_factory=list)
    generated: bool = False
    sourceChunks: list[str] = Field(default_factory=list)


class FormattedActionInvocation(BaseModel):
    invocationId: int
    actionId: int | None = None
    actionName: str
    inputInstances: list[int] = Field(default_factory=list)
    outputInstances: list[int] = Field(default_factory=list)
    inputInstanceNames: list[str] = Field(default_factory=list)
    outputInstanceNames: list[str] = Field(default_factory=list)
    sourceChunks: list[str] = Field(default_factory=list)


class FormattedOntology(BaseModel):
    ontology: OntologyMetadata
    objects: list[FormattedObject] = Field(default_factory=list)
    links: list[FormattedLink] = Field(default_factory=list)
    rules: list[FormattedRule] = Field(default_factory=list)
    actions: list[FormattedAction] = Field(default_factory=list)
    instances: list[FormattedInstance] = Field(default_factory=list)
    actionInvocations: list[FormattedActionInvocation] = Field(default_factory=list)


def format_global_ontology(
    ontology: GlobalOntology,
    *,
    ontology_name: str,
    ontology_desc: str,
    profile: str = "default",
    generated_instance_count: int = 0,
    timestamp: datetime | None = None,
) -> FormattedOntology:
    now = timestamp or datetime.now()
    formatted_time = now.strftime("%Y-%m-%d %H:%M:%S")
    entities = list(ontology.entities.values())
    object_id_by_entity_id = {
        entity.id: index for index, entity in enumerate(entities, start=1)
    }
    object_name_by_entity_id = {entity.id: entity.name for entity in entities}

    literal_attributes = _literal_attributes_by_subject(ontology)
    instance_attribute_signals = _instance_attribute_signals(ontology.instances)
    inferred_data_types = _majority_data_types(instance_attribute_signals)

    objects: list[FormattedObject] = []
    attribute_lookup: dict[tuple[int, str], FormattedAttribute] = {}
    used_object_codes: set[str] = set()
    for entity in entities:
        object_id = object_id_by_entity_id[entity.id]
        formatted_attributes = _format_attributes(
            entity.id,
            entity.attributes,
            literal_attributes.get(entity.id, []),
            instance_attribute_signals.get(entity.id, []),
            inferred_data_types.get(entity.id, {}),
        )
        objects.append(
            FormattedObject(
                objectId=object_id,
                objectName=entity.name,
                objectCode=_unique_code(entity.name, object_id, used_object_codes),
                description=str(entity.attributes.get("说明") or entity.entity_type or ""),
                attributes=formatted_attributes,
                sourceChunks=entity.source_chunks,
            )
        )
        for attribute in formatted_attributes:
            attribute_lookup[(object_id, attribute.attributeName)] = attribute

    links = _format_links(ontology, object_id_by_entity_id, object_name_by_entity_id)
    _augment_links_from_instances(
        links,
        ontology.instances,
        object_id_by_entity_id,
        object_name_by_entity_id,
    )
    link_lookup: dict[tuple[int, str, int], FormattedLink] = {
        (link.sourceObjectId, link.linkInfo, link.targetObjectId): link for link in links
    }
    rules, rule_id_by_global, rule_name_by_global = _format_rules(
        ontology,
        object_id_by_entity_id,
        object_name_by_entity_id,
    )
    actions = _format_actions(
        ontology,
        object_id_by_entity_id,
        object_name_by_entity_id,
        rule_id_by_global,
        rule_name_by_global,
    )
    action_id_by_name = {action.actionName: action.actionId for action in actions}
    _attach_actions_to_rules(rules, actions)

    used_instance_codes: set[str] = set()
    formatted_instances, instance_id_by_global = _format_instances(
        ontology.instances,
        object_id_by_entity_id,
        object_name_by_entity_id,
        attribute_lookup,
        link_lookup,
        used_instance_codes,
    )
    formatted_instances.extend(
        _generate_instances(
            entities,
            object_id_by_entity_id,
            attribute_lookup,
            generated_instance_count,
            start_index=len(formatted_instances) + 1,
            used_instance_codes=used_instance_codes,
        )
    )
    _attach_instances_to_objects(objects, formatted_instances)

    invocations = _format_action_invocations(
        ontology.action_invocations,
        instance_id_by_global,
        formatted_instances,
        action_id_by_name,
    )

    return FormattedOntology(
        ontology=OntologyMetadata(
            ontologyName=ontology_name,
            ontologyDesc=ontology_desc,
            profile=profile,
            createdAt=formatted_time,
            updatedAt=formatted_time,
        ),
        objects=objects,
        links=links,
        rules=rules,
        actions=actions,
        instances=formatted_instances,
        actionInvocations=invocations,
    )


def _literal_attributes_by_subject(
    ontology: GlobalOntology,
) -> dict[str, list[tuple[str, Any, list[str]]]]:
    attributes: dict[str, list[tuple[str, Any, list[str]]]] = {}
    for triple in ontology.triples:
        if triple.object_kind != "literal":
            continue
        attributes.setdefault(triple.subject_id, []).append(
            (triple.predicate, triple.object_value, triple.source_chunks)
        )
    return attributes


def _instance_attribute_signals(
    instances: list[OntologyInstance],
) -> dict[str, list[tuple[str, str, Any]]]:
    signals: dict[str, list[tuple[str, str, Any]]] = {}
    for instance in instances:
        for attribute in instance.attribute_values:
            signals.setdefault(instance.class_id, []).append(
                (attribute.attribute_name, attribute.data_type, attribute.value)
            )
    return signals


def _majority_data_types(
    signals_by_class: dict[str, list[tuple[str, str, Any]]],
) -> dict[str, dict[str, str]]:
    # Per (class, attribute_name), count dtype occurrences and pick the most
    # frequent one. Ties break toward the first non-string dtype so a single
    # stray "壹仟" doesn't lock a numeric column into "string".
    resolved: dict[str, dict[str, str]] = {}
    for class_id, signals in signals_by_class.items():
        counts_by_name: dict[str, dict[str, int]] = {}
        for name, data_type, _value in signals:
            counts = counts_by_name.setdefault(name, {})
            counts[data_type] = counts.get(data_type, 0) + 1
        attribute_dtypes: dict[str, str] = {}
        for name, counts in counts_by_name.items():
            best_count = max(counts.values())
            winners = [dtype for dtype, count in counts.items() if count == best_count]
            if len(winners) == 1:
                attribute_dtypes[name] = winners[0]
            else:
                non_string = [dtype for dtype in winners if dtype != "string"]
                attribute_dtypes[name] = non_string[0] if non_string else winners[0]
        resolved[class_id] = attribute_dtypes
    return resolved


def _format_attributes(
    entity_id: str,
    entity_attributes: dict[str, Any],
    literal_attributes: list[tuple[str, Any, list[str]]],
    instance_attribute_signals: list[tuple[str, str, Any]],
    instance_dtype_by_name: dict[str, str],
) -> list[FormattedAttribute]:
    seen: set[str] = set()
    used_codes: set[str] = set()
    attributes: list[FormattedAttribute] = []

    def make_attribute(
        name: str,
        data_type: str,
        sample_value: Any,
        source_chunks: list[str] | None = None,
    ) -> FormattedAttribute:
        attribute_id = len(attributes) + 1
        return FormattedAttribute(
            attributeId=attribute_id,
            attributeName=name,
            attributeCode=_unique_code(
                f"{entity_id}_{name}", attribute_id, used_codes
            ),
            dataType=data_type,
            description="",
            sampleValue=sample_value,
            sourceChunks=source_chunks or [],
        )

    for name, value in entity_attributes.items():
        if name in {"说明", "description", "desc"}:
            continue
        if name in seen:
            continue
        seen.add(name)
        attributes.append(make_attribute(str(name), _infer_data_type(value), value))

    for name, value, source_chunks in literal_attributes:
        if name in seen:
            continue
        seen.add(name)
        attributes.append(
            make_attribute(
                name,
                _infer_data_type(value),
                value,
                source_chunks=source_chunks,
            )
        )

    for name, data_type, sample_value in instance_attribute_signals:
        if name in seen:
            continue
        seen.add(name)
        resolved_dtype = (
            instance_dtype_by_name.get(name) or data_type or _infer_data_type(sample_value)
        )
        attributes.append(make_attribute(name, resolved_dtype, None))

    return attributes


def _format_links(
    ontology: GlobalOntology,
    object_id_by_entity_id: dict[str, int],
    object_name_by_entity_id: dict[str, str],
) -> list[FormattedLink]:
    links: list[FormattedLink] = []
    for triple in ontology.triples:
        if triple.object_kind != "entity" or not triple.object_id:
            continue
        source_id = object_id_by_entity_id.get(triple.subject_id)
        target_id = object_id_by_entity_id.get(triple.object_id)
        if source_id is None or target_id is None:
            continue
        links.append(
            FormattedLink(
                linkId=len(links) + 1,
                sourceObjectId=source_id,
                sourceObjectName=object_name_by_entity_id[triple.subject_id],
                targetObjectId=target_id,
                targetObjectName=object_name_by_entity_id[triple.object_id],
                linkInfo=triple.predicate,
                confidence=triple.confidence,
                sourceChunks=triple.source_chunks,
            )
        )
    return links


def _augment_links_from_instances(
    links: list[FormattedLink],
    instances: list[OntologyInstance],
    object_id_by_entity_id: dict[str, int],
    object_name_by_entity_id: dict[str, str],
) -> None:
    existing_keys = {
        (link.sourceObjectId, link.linkInfo, link.targetObjectId) for link in links
    }
    class_by_instance_id = {
        instance.id: instance.class_id for instance in instances
    }
    for instance in instances:
        source_object_id = object_id_by_entity_id.get(instance.class_id)
        if source_object_id is None:
            continue
        for link in instance.link_values:
            target_class_id = class_by_instance_id.get(link.target_instance_id)
            if target_class_id is None:
                continue
            target_object_id = object_id_by_entity_id.get(target_class_id)
            if target_object_id is None:
                continue
            key = (source_object_id, link.predicate, target_object_id)
            if key in existing_keys:
                continue
            existing_keys.add(key)
            links.append(
                FormattedLink(
                    linkId=len(links) + 1,
                    sourceObjectId=source_object_id,
                    sourceObjectName=object_name_by_entity_id[instance.class_id],
                    targetObjectId=target_object_id,
                    targetObjectName=object_name_by_entity_id[target_class_id],
                    linkInfo=link.predicate,
                    confidence=link.confidence,
                    sourceChunks=link.source_chunks,
                )
            )


def _format_rules(
    ontology: GlobalOntology,
    object_id_by_entity_id: dict[str, int],
    object_name_by_entity_id: dict[str, str],
) -> tuple[list[FormattedRule], dict[str, int], dict[str, str]]:
    rules: list[FormattedRule] = []
    rule_id_by_global: dict[str, int] = {}
    rule_name_by_global: dict[str, str] = {}
    used_codes: set[str] = set()
    for rule in ontology.rules:
        scope_object_id = object_id_by_entity_id.get(rule.scope_entity_id)
        scope_object_name = object_name_by_entity_id.get(rule.scope_entity_id)
        if scope_object_id is None or scope_object_name is None:
            continue
        rule_id = len(rules) + 1
        rules.append(
            FormattedRule(
                ruleId=rule_id,
                ruleName=rule.name,
                ruleCode=_unique_code(rule.name, rule_id, used_codes),
                description=rule.description,
                scopeObjectId=scope_object_id,
                scopeObjectName=scope_object_name,
                logic=rule.logic,
                conditions=[
                    FormattedRuleCondition(
                        conditionId=condition.id,
                        field=condition.field,
                        operator=condition.operator,
                        value=condition.value,
                        valueType=condition.value_type,
                    )
                    for condition in rule.conditions
                ],
                confidence=rule.confidence,
                sourceChunks=rule.source_chunks,
            )
        )
        rule_id_by_global[rule.id] = rule_id
        rule_name_by_global[rule.id] = rule.name
    return rules, rule_id_by_global, rule_name_by_global


def _format_actions(
    ontology: GlobalOntology,
    object_id_by_entity_id: dict[str, int],
    object_name_by_entity_id: dict[str, str],
    rule_id_by_global: dict[str, int],
    rule_name_by_global: dict[str, str],
) -> list[FormattedAction]:
    actions: list[FormattedAction] = []
    used_codes: set[str] = set()
    for action in ontology.actions:
        action_id = len(actions) + 1
        actions.append(
            FormattedAction(
                actionId=action_id,
                actionName=action.name,
                actionCode=_unique_code(action.name, action_id, used_codes),
                description=action.description,
                inputObjectIds=[
                    object_id_by_entity_id[entity_id]
                    for entity_id in action.input_entity_ids
                    if entity_id in object_id_by_entity_id
                ],
                inputObjects=[
                    object_name_by_entity_id[entity_id]
                    for entity_id in action.input_entity_ids
                    if entity_id in object_name_by_entity_id
                ],
                outputObjectIds=[
                    object_id_by_entity_id[entity_id]
                    for entity_id in action.output_entity_ids
                    if entity_id in object_id_by_entity_id
                ],
                outputObjects=[
                    object_name_by_entity_id[entity_id]
                    for entity_id in action.output_entity_ids
                    if entity_id in object_name_by_entity_id
                ],
                triggerRuleIds=[
                    rule_id_by_global[rule_id]
                    for rule_id in action.trigger_rule_ids
                    if rule_id in rule_id_by_global
                ],
                triggerRules=[
                    rule_name_by_global[rule_id]
                    for rule_id in action.trigger_rule_ids
                    if rule_id in rule_name_by_global
                ],
                targetObjectIds=[
                    object_id_by_entity_id[entity_id]
                    for entity_id in action.target_entity_ids
                    if entity_id in object_id_by_entity_id
                ],
                targetObjects=[
                    object_name_by_entity_id[entity_id]
                    for entity_id in action.target_entity_ids
                    if entity_id in object_name_by_entity_id
                ],
                actionType=action.action_type,
                executor=action.executor,
                actionPlan=action.action_plan,
                feedback=action.feedback,
                sourceChunks=action.source_chunks,
            )
        )
    return actions


def _attach_actions_to_rules(
    rules: list[FormattedRule],
    actions: list[FormattedAction],
) -> None:
    rule_by_id = {rule.ruleId: rule for rule in rules}
    for action in actions:
        for rule_id in action.triggerRuleIds:
            rule = rule_by_id.get(rule_id)
            if rule is None:
                continue
            if action.actionId not in rule.actionIds:
                rule.actionIds.append(action.actionId)
            if action.actionName not in rule.actionNames:
                rule.actionNames.append(action.actionName)


def _attach_instances_to_objects(
    objects: list[FormattedObject],
    instances: list[FormattedInstance],
) -> None:
    object_by_id = {item.objectId: item for item in objects}
    for instance in instances:
        item = object_by_id.get(instance.objectId)
        if item is None:
            continue
        item.instanceIds.append(instance.instanceId)
    for item in objects:
        item.instanceCount = len(item.instanceIds)


def _format_instances(
    instances: list[OntologyInstance],
    object_id_by_entity_id: dict[str, int],
    object_name_by_entity_id: dict[str, str],
    attribute_lookup: dict[tuple[int, str], FormattedAttribute],
    link_lookup: dict[tuple[int, str, int], FormattedLink],
    used_instance_codes: set[str],
) -> tuple[list[FormattedInstance], dict[str, int]]:
    instance_id_by_global: dict[str, int] = {}
    object_id_by_instance: dict[str, int] = {}
    object_name_by_instance: dict[str, str] = {}
    instance_name_by_global: dict[str, str] = {}

    # First pass: assign integer instance IDs so link_values can be resolved.
    ordered: list[OntologyInstance] = []
    for instance in instances:
        if instance.class_id not in object_id_by_entity_id:
            continue
        instance_id_by_global[instance.id] = len(instance_id_by_global) + 1
        object_id_by_instance[instance.id] = object_id_by_entity_id[instance.class_id]
        object_name_by_instance[instance.id] = object_name_by_entity_id[instance.class_id]
        instance_name_by_global[instance.id] = instance.name
        ordered.append(instance)

    formatted: list[FormattedInstance] = []
    for instance in ordered:
        object_id = object_id_by_instance[instance.id]
        attribute_values = _format_instance_attribute_values(
            instance.attribute_values, object_id, attribute_lookup
        )
        link_values = _format_instance_link_values(
            instance.link_values,
            object_id,
            instance_id_by_global,
            object_id_by_instance,
            object_name_by_instance,
            instance_name_by_global,
            link_lookup,
        )
        instance_int_id = instance_id_by_global[instance.id]
        formatted.append(
            FormattedInstance(
                instanceId=instance_int_id,
                instanceCode=_unique_code(
                    instance.name, instance_int_id, used_instance_codes
                ),
                instanceName=instance.name,
                objectId=object_id,
                objectName=object_name_by_instance[instance.id],
                attributeValues=attribute_values,
                linkValues=link_values,
                generated=instance.generated,
                sourceChunks=instance.source_chunks,
            )
        )
    return formatted, instance_id_by_global


def _format_instance_attribute_values(
    attribute_values: list[InstanceAttributeValue],
    object_id: int,
    attribute_lookup: dict[tuple[int, str], FormattedAttribute],
) -> list[FormattedInstanceAttributeValue]:
    items: list[FormattedInstanceAttributeValue] = []
    used_codes: set[str] = set()
    for attribute in attribute_values:
        schema = attribute_lookup.get((object_id, attribute.attribute_name))
        items.append(
            FormattedInstanceAttributeValue(
                attributeId=schema.attributeId if schema else None,
                attributeName=attribute.attribute_name,
                attributeCode=(
                    schema.attributeCode
                    if schema
                    else _unique_code(
                        attribute.attribute_name, len(items) + 1, used_codes
                    )
                ),
                dataType=(
                    schema.dataType if schema else (attribute.data_type or _infer_data_type(attribute.value))
                ),
                value=attribute.value,
                sourceChunks=attribute.source_chunks,
            )
        )
    return items


def _format_instance_link_values(
    link_values: list[InstanceLinkValue],
    source_object_id: int,
    instance_id_by_global: dict[str, int],
    object_id_by_instance: dict[str, int],
    object_name_by_instance: dict[str, str],
    instance_name_by_global: dict[str, str],
    link_lookup: dict[tuple[int, str, int], FormattedLink],
) -> list[FormattedInstanceLinkValue]:
    items: list[FormattedInstanceLinkValue] = []
    for link in link_values:
        target_instance_id = instance_id_by_global.get(link.target_instance_id)
        if target_instance_id is None:
            continue
        target_object_id = object_id_by_instance.get(link.target_instance_id)
        target_object_name = object_name_by_instance.get(link.target_instance_id)
        target_instance_name = instance_name_by_global.get(
            link.target_instance_id, ""
        )
        link_schema = (
            link_lookup.get((source_object_id, link.predicate, target_object_id))
            if target_object_id is not None
            else None
        )
        items.append(
            FormattedInstanceLinkValue(
                linkId=link_schema.linkId if link_schema else None,
                linkInfo=link.predicate,
                targetInstanceId=target_instance_id,
                targetInstanceName=target_instance_name,
                targetObjectId=target_object_id,
                targetObjectName=target_object_name,
                confidence=link.confidence,
                sourceChunks=link.source_chunks,
            )
        )
    return items


def _format_action_invocations(
    invocations: list[ActionInvocation],
    instance_id_by_global: dict[str, int],
    formatted_instances: list[FormattedInstance],
    action_id_by_name: dict[str, int],
) -> list[FormattedActionInvocation]:
    name_by_instance_int_id = {
        instance.instanceId: instance.instanceName for instance in formatted_instances
    }
    items: list[FormattedActionInvocation] = []
    for invocation in invocations:
        inputs = [
            instance_id_by_global[instance_id]
            for instance_id in invocation.input_instance_ids
            if instance_id in instance_id_by_global
        ]
        outputs = [
            instance_id_by_global[instance_id]
            for instance_id in invocation.output_instance_ids
            if instance_id in instance_id_by_global
        ]
        items.append(
            FormattedActionInvocation(
                invocationId=len(items) + 1,
                actionId=action_id_by_name.get(invocation.action_name),
                actionName=invocation.action_name,
                inputInstances=inputs,
                outputInstances=outputs,
                inputInstanceNames=[
                    name_by_instance_int_id.get(instance_id, "")
                    for instance_id in inputs
                ],
                outputInstanceNames=[
                    name_by_instance_int_id.get(instance_id, "")
                    for instance_id in outputs
                ],
                sourceChunks=invocation.source_chunks,
            )
        )
    return items


def _generate_instances(
    entities: list[Entity],
    object_id_by_entity_id: dict[str, int],
    attribute_lookup: dict[tuple[int, str], FormattedAttribute],
    count: int,
    start_index: int,
    used_instance_codes: set[str],
) -> list[FormattedInstance]:
    if count <= 0 or not entities:
        return []
    generated: list[FormattedInstance] = []
    for index in range(count):
        entity = entities[index % len(entities)]
        object_id = object_id_by_entity_id[entity.id]
        instance_int_id = start_index + index
        class_attributes = [
            attribute
            for (oid, _name), attribute in attribute_lookup.items()
            if oid == object_id
        ]
        attribute_values = [
            FormattedInstanceAttributeValue(
                attributeId=attribute.attributeId,
                attributeName=attribute.attributeName,
                attributeCode=attribute.attributeCode,
                dataType=attribute.dataType,
                value=_placeholder_value(attribute.dataType, attribute.attributeName, index),
            )
            for attribute in class_attributes
        ]
        instance_name = f"{entity.name}示例{index + 1}"
        generated.append(
            FormattedInstance(
                instanceId=instance_int_id,
                instanceCode=_unique_code(
                    instance_name, instance_int_id, used_instance_codes
                ),
                instanceName=instance_name,
                objectId=object_id,
                objectName=entity.name,
                attributeValues=attribute_values,
                generated=True,
            )
        )
    return generated


def _placeholder_value(data_type: str, attribute_name: str, index: int) -> Any:
    if data_type == "integer":
        return index + 1
    if data_type == "number":
        return float(index + 1)
    if data_type == "boolean":
        return False
    return f"{attribute_name}示例{index + 1}"


def _infer_data_type(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    return "string"


def _code(value: str, fallback_index: int) -> str:
    normalized = re.sub(r"[^0-9A-Za-z_]+", "_", value.strip().lower()).strip("_")
    if normalized:
        return normalized[:64]
    return f"object_{fallback_index}"


def _unique_code(value: str, fallback_index: int, used: set[str]) -> str:
    # Generate a deterministic code from `value` and suffix `_2`, `_3`, ... when
    # the base code collides with one already issued in the same scope so that
    # downstream consumers can treat the code as a primary key.
    base = _code(value, fallback_index)
    candidate = base
    counter = 2
    while candidate in used:
        candidate = f"{base}_{counter}"
        counter += 1
    used.add(candidate)
    return candidate
