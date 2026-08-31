from __future__ import annotations

import logging
import json
from dataclasses import dataclass, field
from typing import Any

from ontology_construct.merger.normalizer import normalize_name

logger = logging.getLogger(__name__)
from ontology_construct.models import (
    ActionInvocation,
    Entity,
    GlobalOntology,
    InstanceAttributeValue,
    InstanceLinkValue,
    LocalOntology,
    OntologyAction,
    OntologyInstance,
    OntologyRule,
    Triple,
)


@dataclass
class EntityAccumulator:
    entity: Entity
    normalized_names: set[str] = field(default_factory=set)


@dataclass
class InstanceAccumulator:
    instance: OntologyInstance
    attribute_index: dict[str, InstanceAttributeValue] = field(default_factory=dict)
    link_index: dict[tuple[str, str], InstanceLinkValue] = field(default_factory=dict)


class OntologyMerger:
    def merge(self, local_ontologies: list[LocalOntology]) -> GlobalOntology:
        entity_accumulators: dict[str, EntityAccumulator] = {}
        local_to_global: dict[tuple[str, str], str] = {}

        for local in local_ontologies:
            for entity in local.entities:
                global_id = self._merge_entity(entity, entity_accumulators)
                local_to_global[(local.chunk_id, entity.id)] = global_id

        triples = self._merge_triples(local_ontologies, local_to_global)
        rules, local_to_global_rule = self._merge_rules(
            local_ontologies,
            local_to_global,
        )
        actions, action_name_canonical = self._merge_actions(
            local_ontologies,
            local_to_global,
            local_to_global_rule,
        )
        instances, local_to_global_instance = self._merge_instances(
            local_ontologies, local_to_global
        )
        action_invocations = self._merge_action_invocations(
            local_ontologies,
            local_to_global_instance,
            action_name_canonical,
        )
        entities = [accumulator.entity for accumulator in entity_accumulators.values()]
        return GlobalOntology.from_entities_and_triples(
            entities,
            triples,
            rules=rules,
            actions=actions,
            instances=instances,
            action_invocations=action_invocations,
        )

    def _merge_entity(
        self,
        entity: Entity,
        accumulators: dict[str, EntityAccumulator],
    ) -> str:
        candidate_names = [entity.name, *entity.aliases]
        normalized_candidates = {normalize_name(name) for name in candidate_names if name}
        matched_key = next(
            (
                key
                for key, accumulator in accumulators.items()
                if accumulator.normalized_names & normalized_candidates
            ),
            None,
        )

        if matched_key is None:
            global_id = f"g{len(accumulators) + 1:06d}"
            merged_entity = entity.model_copy(update={"id": global_id})
            accumulators[global_id] = EntityAccumulator(
                entity=merged_entity,
                normalized_names=normalized_candidates,
            )
            return global_id

        accumulator = accumulators[matched_key]
        accumulator.normalized_names.update(normalized_candidates)
        accumulator.entity.aliases = _dedupe(
            [*accumulator.entity.aliases, entity.name, *entity.aliases]
        )
        accumulator.entity.source_chunks = _dedupe(
            [*accumulator.entity.source_chunks, *entity.source_chunks]
        )
        accumulator.entity.attributes = {
            **entity.attributes,
            **accumulator.entity.attributes,
        }
        return accumulator.entity.id

    def _merge_triples(
        self,
        local_ontologies: list[LocalOntology],
        local_to_global: dict[tuple[str, str], str],
    ) -> list[Triple]:
        triples_by_key: dict[tuple[object, ...], Triple] = {}
        for local in local_ontologies:
            for triple in local.triples:
                subject_id = local_to_global.get((local.chunk_id, triple.subject_id))
                if not subject_id:
                    continue
                object_id = None
                if triple.object_kind == "entity":
                    object_id = local_to_global.get((local.chunk_id, triple.object_id or ""))
                    if not object_id:
                        continue

                key = (
                    subject_id,
                    triple.predicate,
                    triple.object_kind,
                    object_id,
                    triple.object_value,
                )
                updated = triple.model_copy(
                    update={
                        "subject_id": subject_id,
                        "object_id": object_id,
                    }
                )
                if key not in triples_by_key:
                    triples_by_key[key] = updated
                    continue

                existing = triples_by_key[key]
                existing.confidence = max(existing.confidence, updated.confidence)
                existing.source_chunks = _dedupe(
                    [*existing.source_chunks, *updated.source_chunks]
                )
        return list(triples_by_key.values())

    def _merge_rules(
        self,
        local_ontologies: list[LocalOntology],
        local_to_global: dict[tuple[str, str], str],
    ) -> tuple[list[OntologyRule], dict[tuple[str, str], str]]:
        rules_by_key: dict[tuple[object, ...], OntologyRule] = {}
        local_to_global_rule: dict[tuple[str, str], str] = {}
        for local in local_ontologies:
            for rule in local.rules:
                scope_id = local_to_global.get((local.chunk_id, rule.scope_entity_id))
                if not scope_id:
                    continue
                condition_key = tuple(
                    (
                        condition.field,
                        condition.operator,
                        json.dumps(
                            condition.value,
                            ensure_ascii=False,
                            sort_keys=True,
                            default=str,
                        ),
                        condition.value_type,
                    )
                    for condition in rule.conditions
                )
                key = (
                    normalize_name(rule.name),
                    scope_id,
                    rule.logic,
                    condition_key,
                )
                existing = rules_by_key.get(key)
                if existing is None:
                    existing = rule.model_copy(
                        update={
                            "id": f"rule_{len(rules_by_key) + 1:06d}",
                            "scope_entity_id": scope_id,
                        }
                    )
                    rules_by_key[key] = existing
                else:
                    existing.confidence = max(existing.confidence, rule.confidence)
                    existing.source_chunks = _dedupe(
                        [*existing.source_chunks, *rule.source_chunks]
                    )
                    if not existing.description and rule.description:
                        existing.description = rule.description
                local_to_global_rule[(local.chunk_id, rule.id)] = existing.id
                local_to_global_rule[(local.chunk_id, normalize_name(rule.name))] = (
                    existing.id
                )
        return list(rules_by_key.values()), local_to_global_rule

    def _merge_actions(
        self,
        local_ontologies: list[LocalOntology],
        local_to_global: dict[tuple[str, str], str],
        local_to_global_rule: dict[tuple[str, str], str],
    ) -> tuple[list[OntologyAction], dict[str, str]]:
        actions_by_key: dict[tuple[object, ...], OntologyAction] = {}
        # Maps every variant spelling seen in local actions back to the
        # canonical action name kept in the merged set so that invocations
        # can be remapped to a name that still resolves to an actionId.
        canonical_by_raw_name: dict[str, str] = {}
        for local in local_ontologies:
            for action in local.actions:
                input_ids = _remap_ids(
                    action.input_entity_ids,
                    local.chunk_id,
                    local_to_global,
                )
                output_ids = _remap_ids(
                    action.output_entity_ids,
                    local.chunk_id,
                    local_to_global,
                )
                target_ids = _remap_ids(
                    action.target_entity_ids,
                    local.chunk_id,
                    local_to_global,
                )
                trigger_rule_ids = _dedupe(
                    [
                        global_id
                        for rule_id in action.trigger_rule_ids
                        if (
                            global_id := local_to_global_rule.get(
                                (local.chunk_id, rule_id)
                            )
                            or local_to_global_rule.get(
                                (local.chunk_id, normalize_name(rule_id))
                            )
                        )
                    ]
                )
                key = (
                    normalize_name(action.name),
                    tuple(input_ids),
                    tuple(output_ids),
                    tuple(target_ids),
                )
                updated = action.model_copy(
                    update={
                        "id": f"act_{len(actions_by_key) + 1:06d}",
                        "input_entity_ids": input_ids,
                        "output_entity_ids": output_ids,
                        "trigger_rule_ids": trigger_rule_ids,
                        "target_entity_ids": target_ids,
                    }
                )
                if key not in actions_by_key:
                    actions_by_key[key] = updated
                    canonical_by_raw_name.setdefault(action.name, updated.name)
                    continue

                existing = actions_by_key[key]
                existing.source_chunks = _dedupe(
                    [*existing.source_chunks, *updated.source_chunks]
                )
                existing.attributes = {**updated.attributes, **existing.attributes}
                existing.trigger_rule_ids = _dedupe(
                    [*existing.trigger_rule_ids, *updated.trigger_rule_ids]
                )
                if not existing.executor and updated.executor:
                    existing.executor = updated.executor
                if not existing.action_plan and updated.action_plan:
                    existing.action_plan = updated.action_plan
                if not existing.feedback and updated.feedback:
                    existing.feedback = updated.feedback
                canonical_by_raw_name.setdefault(action.name, existing.name)
        return list(actions_by_key.values()), canonical_by_raw_name

    def _merge_instances(
        self,
        local_ontologies: list[LocalOntology],
        local_to_global: dict[tuple[str, str], str],
    ) -> tuple[list[OntologyInstance], dict[tuple[str, str], str]]:
        accumulators: dict[tuple[str, str], InstanceAccumulator] = {}
        local_to_global_instance: dict[tuple[str, str], str] = {}

        # Pass 1: identify each instance with a stable global id.
        for local in local_ontologies:
            for instance in local.instances:
                if instance.is_unbound:
                    continue
                class_id = local_to_global.get((local.chunk_id, instance.class_id))
                if not class_id:
                    continue
                key = (class_id, normalize_name(instance.name))
                if key not in accumulators:
                    global_id = f"inst_{len(accumulators) + 1:06d}"
                    seed = instance.model_copy(
                        update={
                            "id": global_id,
                            "class_id": class_id,
                            "attribute_values": [],
                            "link_values": [],
                            "source_chunks": [],
                        }
                    )
                    accumulators[key] = InstanceAccumulator(instance=seed)
                local_to_global_instance[(local.chunk_id, instance.id)] = (
                    accumulators[key].instance.id
                )

        # Pass 2: fold attribute_values / link_values / source_chunks into accumulators.
        for local in local_ontologies:
            for instance in local.instances:
                if instance.is_unbound:
                    continue
                class_id = local_to_global.get((local.chunk_id, instance.class_id))
                if not class_id:
                    continue
                key = (class_id, normalize_name(instance.name))
                accumulator = accumulators[key]
                merged = accumulator.instance

                merged.source_chunks = _dedupe(
                    [*merged.source_chunks, *instance.source_chunks]
                )
                merged.generated = merged.generated and instance.generated

                for attribute in instance.attribute_values:
                    existing = accumulator.attribute_index.get(attribute.attribute_name)
                    if existing is None:
                        accumulator.attribute_index[attribute.attribute_name] = (
                            attribute.model_copy()
                        )
                        continue
                    merged_source_chunks = _dedupe(
                        [*existing.source_chunks, *attribute.source_chunks]
                    )
                    if existing.value != attribute.value:
                        if attribute.confidence > existing.confidence:
                            logger.warning(
                                "instance %s attribute %r resolved to higher-confidence "
                                "value (kept %r conf=%.2f, replaced %r conf=%.2f from %s)",
                                merged.name,
                                attribute.attribute_name,
                                attribute.value,
                                attribute.confidence,
                                existing.value,
                                existing.confidence,
                                local.chunk_id,
                            )
                            accumulator.attribute_index[attribute.attribute_name] = (
                                attribute.model_copy(
                                    update={"source_chunks": merged_source_chunks}
                                )
                            )
                            continue
                        logger.warning(
                            "instance %s attribute %r has conflicting values "
                            "(kept %r conf=%.2f, dropped %r conf=%.2f from %s)",
                            merged.name,
                            attribute.attribute_name,
                            existing.value,
                            existing.confidence,
                            attribute.value,
                            attribute.confidence,
                            local.chunk_id,
                        )
                    existing.source_chunks = merged_source_chunks

                for link in instance.link_values:
                    remapped_target = local_to_global_instance.get(
                        (local.chunk_id, link.target_instance_id)
                    )
                    if not remapped_target:
                        continue
                    link_key = (link.predicate, remapped_target)
                    existing_link = accumulator.link_index.get(link_key)
                    if existing_link is None:
                        accumulator.link_index[link_key] = link.model_copy(
                            update={"target_instance_id": remapped_target}
                        )
                        continue
                    existing_link.confidence = max(existing_link.confidence, link.confidence)
                    existing_link.source_chunks = _dedupe(
                        [*existing_link.source_chunks, *link.source_chunks]
                    )

        instances: list[OntologyInstance] = []
        for accumulator in accumulators.values():
            accumulator.instance.attribute_values = list(
                accumulator.attribute_index.values()
            )
            accumulator.instance.link_values = list(accumulator.link_index.values())
            instances.append(accumulator.instance)
        return instances, local_to_global_instance

    def _merge_action_invocations(
        self,
        local_ontologies: list[LocalOntology],
        local_to_global_instance: dict[tuple[str, str], str],
        canonical_action_name: dict[str, str],
    ) -> list[ActionInvocation]:
        invocations_by_key: dict[tuple[Any, ...], ActionInvocation] = {}
        for local in local_ontologies:
            for invocation in local.action_invocations:
                inputs = _remap_ids(
                    invocation.input_instance_ids,
                    local.chunk_id,
                    local_to_global_instance,
                )
                outputs = _remap_ids(
                    invocation.output_instance_ids,
                    local.chunk_id,
                    local_to_global_instance,
                )
                canonical_name = canonical_action_name.get(
                    invocation.action_name, invocation.action_name
                )
                key = (
                    normalize_name(canonical_name),
                    tuple(inputs),
                    tuple(outputs),
                )
                updated = invocation.model_copy(
                    update={
                        "id": f"inv_{len(invocations_by_key) + 1:06d}",
                        "action_name": canonical_name,
                        "input_instance_ids": inputs,
                        "output_instance_ids": outputs,
                    }
                )
                if key not in invocations_by_key:
                    invocations_by_key[key] = updated
                    continue
                existing = invocations_by_key[key]
                existing.source_chunks = _dedupe(
                    [*existing.source_chunks, *updated.source_chunks]
                )
                existing.attributes = {**updated.attributes, **existing.attributes}
        return list(invocations_by_key.values())


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _remap_ids(
    entity_ids: list[str],
    chunk_id: str,
    local_to_global: dict[tuple[str, str], str],
) -> list[str]:
    return _dedupe(
        [
            global_id
            for entity_id in entity_ids
            if (global_id := local_to_global.get((chunk_id, entity_id)))
        ]
    )
