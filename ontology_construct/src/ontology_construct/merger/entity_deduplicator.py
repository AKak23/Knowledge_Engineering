from __future__ import annotations

import math
from dataclasses import dataclass

from ontology_construct.embedding import EmbeddingClient
from ontology_construct.merger.normalizer import normalize_name
from ontology_construct.models import (
    ActionInvocation,
    Entity,
    GlobalOntology,
    OntologyInstance,
    Triple,
)


@dataclass(frozen=True)
class DuplicatePair:
    entity_a_id: str
    entity_b_id: str
    similarity: float


class EntityDeduplicator:
    def __init__(self, embedding_client: EmbeddingClient, threshold: float):
        self.embedding_client = embedding_client
        self.threshold = threshold

    async def deduplicate(self, ontology: GlobalOntology) -> GlobalOntology:
        entities = list(ontology.entities.values())
        if len(entities) < 2:
            return ontology

        pairs = await self.find_duplicate_pairs(entities)
        if not pairs:
            return ontology

        groups = _build_groups([entity.id for entity in entities], pairs)
        return merge_entity_groups(ontology, groups)

    async def find_duplicate_pairs(self, entities: list[Entity]) -> list[DuplicatePair]:
        vectors = await self.embedding_client.embed_texts([entity.name for entity in entities])
        pairs: list[DuplicatePair] = []
        for i, entity_a in enumerate(entities):
            for j in range(i + 1, len(entities)):
                similarity = cosine_similarity(vectors[i], vectors[j])
                if similarity >= self.threshold:
                    pairs.append(
                        DuplicatePair(
                            entity_a_id=entity_a.id,
                            entity_b_id=entities[j].id,
                            similarity=similarity,
                        )
                    )
        return pairs


def cosine_similarity(vector_a: list[float], vector_b: list[float]) -> float:
    if len(vector_a) != len(vector_b):
        raise ValueError("embedding vectors must have the same dimension")
    dot = sum(a * b for a, b in zip(vector_a, vector_b))
    norm_a = math.sqrt(sum(a * a for a in vector_a))
    norm_b = math.sqrt(sum(b * b for b in vector_b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def merge_entity_groups(
    ontology: GlobalOntology,
    groups: list[list[str]],
) -> GlobalOntology:
    canonical_by_id: dict[str, str] = {}
    for group in groups:
        if not group:
            continue
        canonical_id = group[0]
        for entity_id in group:
            canonical_by_id[entity_id] = canonical_id

    merged_entities: dict[str, Entity] = {}
    for entity in ontology.entities.values():
        canonical_id = canonical_by_id.get(entity.id, entity.id)
        if canonical_id not in merged_entities:
            merged_entities[canonical_id] = entity.model_copy(update={"id": canonical_id})
            continue

        canonical = merged_entities[canonical_id]
        canonical.aliases = _dedupe([*canonical.aliases, entity.name, *entity.aliases])
        canonical.source_chunks = _dedupe(
            [*canonical.source_chunks, *entity.source_chunks]
        )
        canonical.attributes = {**entity.attributes, **canonical.attributes}

    remapped_triples = [
        _remap_triple(triple, canonical_by_id) for triple in ontology.triples
    ]
    deduped_triples = _dedupe_triples(remapped_triples)
    remapped_actions = [
        action.model_copy(
            update={
                "input_entity_ids": _remap_ids(action.input_entity_ids, canonical_by_id),
                "output_entity_ids": _remap_ids(
                    action.output_entity_ids,
                    canonical_by_id,
                ),
            }
        )
        for action in ontology.actions
    ]
    remapped_instances = [
        instance.model_copy(
            update={
                "class_id": canonical_by_id.get(instance.class_id, instance.class_id),
            }
        )
        for instance in ontology.instances
    ]
    merged_instances, old_to_new_instance_id = _remerge_instances(remapped_instances)
    remapped_invocations = _remap_action_invocations(
        ontology.action_invocations, old_to_new_instance_id
    )
    return GlobalOntology.from_entities_and_triples(
        list(merged_entities.values()),
        deduped_triples,
        actions=remapped_actions,
        instances=merged_instances,
        action_invocations=remapped_invocations,
    )


def _remerge_instances(
    instances: list[OntologyInstance],
) -> tuple[list[OntologyInstance], dict[str, str]]:
    accumulators: dict[tuple[str, str], OntologyInstance] = {}
    old_to_new: dict[str, str] = {}

    # Pass 1: identity — merge by (class_id, normalized name) and pick the first
    # encountered id as canonical so downstream remapping is deterministic.
    for instance in instances:
        key = (instance.class_id, normalize_name(instance.name))
        if key not in accumulators:
            accumulators[key] = instance.model_copy(
                update={
                    "attribute_values": [],
                    "link_values": [],
                    "source_chunks": [],
                }
            )
        old_to_new[instance.id] = accumulators[key].id

    # Pass 2: fold attribute_values / link_values / source_chunks, remapping
    # link targets through the same old→new instance id table.
    for instance in instances:
        key = (instance.class_id, normalize_name(instance.name))
        merged = accumulators[key]
        merged.source_chunks = _dedupe(
            [*merged.source_chunks, *instance.source_chunks]
        )
        merged.generated = merged.generated and instance.generated
        existing_attribute_names = {
            attribute.attribute_name for attribute in merged.attribute_values
        }
        for attribute in instance.attribute_values:
            if attribute.attribute_name in existing_attribute_names:
                continue
            merged.attribute_values.append(attribute.model_copy())
            existing_attribute_names.add(attribute.attribute_name)

        existing_link_keys = {
            (link.predicate, link.target_instance_id) for link in merged.link_values
        }
        for link in instance.link_values:
            remapped_target = old_to_new.get(link.target_instance_id)
            if not remapped_target:
                continue
            link_key = (link.predicate, remapped_target)
            if link_key in existing_link_keys:
                continue
            merged.link_values.append(
                link.model_copy(update={"target_instance_id": remapped_target})
            )
            existing_link_keys.add(link_key)

    return list(accumulators.values()), old_to_new


def _remap_action_invocations(
    invocations: list[ActionInvocation],
    old_to_new_instance_id: dict[str, str],
) -> list[ActionInvocation]:
    remapped: list[ActionInvocation] = []
    seen_keys: set[tuple[object, ...]] = set()
    for invocation in invocations:
        inputs = _dedupe(
            [
                old_to_new_instance_id.get(instance_id, instance_id)
                for instance_id in invocation.input_instance_ids
            ]
        )
        outputs = _dedupe(
            [
                old_to_new_instance_id.get(instance_id, instance_id)
                for instance_id in invocation.output_instance_ids
            ]
        )
        key = (invocation.action_name, tuple(inputs), tuple(outputs))
        if key in seen_keys:
            continue
        seen_keys.add(key)
        remapped.append(
            invocation.model_copy(
                update={
                    "input_instance_ids": inputs,
                    "output_instance_ids": outputs,
                }
            )
        )
    return remapped


def _build_groups(entity_ids: list[str], pairs: list[DuplicatePair]) -> list[list[str]]:
    parent = {entity_id: entity_id for entity_id in entity_ids}

    def find(entity_id: str) -> str:
        while parent[entity_id] != entity_id:
            parent[entity_id] = parent[parent[entity_id]]
            entity_id = parent[entity_id]
        return entity_id

    def union(a: str, b: str) -> None:
        root_a = find(a)
        root_b = find(b)
        if root_a != root_b:
            parent[root_b] = root_a

    for pair in pairs:
        union(pair.entity_a_id, pair.entity_b_id)

    groups_by_root: dict[str, list[str]] = {}
    for entity_id in entity_ids:
        groups_by_root.setdefault(find(entity_id), []).append(entity_id)
    return [group for group in groups_by_root.values() if len(group) > 1]


def _remap_triple(triple: Triple, canonical_by_id: dict[str, str]) -> Triple:
    object_id = triple.object_id
    if triple.object_kind == "entity" and object_id is not None:
        object_id = canonical_by_id.get(object_id, object_id)
    return triple.model_copy(
        update={
            "subject_id": canonical_by_id.get(triple.subject_id, triple.subject_id),
            "object_id": object_id,
        }
    )


def _dedupe_triples(triples: list[Triple]) -> list[Triple]:
    triples_by_key: dict[tuple[object, ...], Triple] = {}
    for triple in triples:
        key = (
            triple.subject_id,
            triple.predicate,
            triple.object_kind,
            triple.object_id,
            triple.object_value,
        )
        if key not in triples_by_key:
            triples_by_key[key] = triple
            continue
        existing = triples_by_key[key]
        existing.confidence = max(existing.confidence, triple.confidence)
        existing.source_chunks = _dedupe([*existing.source_chunks, *triple.source_chunks])
    return list(triples_by_key.values())


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _remap_ids(values: list[str], canonical_by_id: dict[str, str]) -> list[str]:
    return _dedupe([canonical_by_id.get(value, value) for value in values])
