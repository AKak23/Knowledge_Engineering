from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import json
from typing import Any, Callable, TypeVar

from ontology_construct.concept.models import (
    ChunkConceptCandidates,
    ConceptBuildArtifacts,
    ConceptCandidate,
    ConceptCluster,
    ConceptInstanceCandidate,
    ConceptInstanceReport,
    ConceptQualityReport,
    ConceptRelationReport,
    ConceptSelectionReport,
    RelationCandidate,
)
from ontology_construct.concept.parser import (
    parse_concept_candidates,
    parse_concept_instances,
    parse_controlled_relations,
    parse_instance_relations,
)
from ontology_construct.concept.prompts import (
    render_chunk_instance_prompt,
    render_concept_instance_prompt,
    render_concept_generalize_prompt,
    render_concept_relation_prompt,
    render_instance_relation_prompt,
)
from ontology_construct.config import ConceptConfig
from ontology_construct.embedding import EmbeddingClient
from ontology_construct.llm.client import LLMClient
from ontology_construct.merger.entity_deduplicator import cosine_similarity
from ontology_construct.merger.normalizer import normalize_name
from ontology_construct.models import (
    Entity,
    GlobalOntology,
    InstanceAttributeValue,
    InstanceLinkValue,
    OntologyAction,
    OntologyInstance,
    OntologyRule,
    RuleCondition,
    Triple,
)


_T = TypeVar("_T")


@dataclass
class ConceptAccumulator:
    name: str
    aliases: list[str] = field(default_factory=list)
    definitions: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    source_chunks: list[str] = field(default_factory=list)
    abstraction_level: str = "medium"
    order: int = 0
    semantic_merged: bool = False


@dataclass
class InstanceRelationPair:
    source: ConceptInstanceCandidate
    predicate: str
    target: ConceptInstanceCandidate
    evidence: list[str] = field(default_factory=list)
    score: float = 0.0


@dataclass
class InstanceRelationPairSelection:
    pairs: list[InstanceRelationPair] = field(default_factory=list)
    candidate_count: int = 0
    truncated_count: int = 0
    instances_without_candidates: list[str] = field(default_factory=list)


class ConceptGeneralizer:
    def __init__(
        self,
        llm_client: LLMClient,
        config: ConceptConfig,
        supplemental_prompt: str | None = None,
        stage_callback: Callable[[str], None] | None = None,
        max_concurrency: int = 8,
        chunk_texts: dict[str, str] | None = None,
        embedding_client: EmbeddingClient | None = None,
    ):
        self.llm_client = llm_client
        self.config = config
        self.supplemental_prompt = supplemental_prompt
        self.stage_callback = stage_callback
        self.max_concurrency = max(1, max_concurrency)
        self.instance_max_concurrency = max(1, config.instance_llm_concurrency)
        self.embedding_client = embedding_client
        # B1: map of chunk_id -> raw chunk text, used to feed full source text
        # (not just evidence snippets) into instance extraction.
        self.chunk_texts = chunk_texts or {}
        # Original per-chunk candidates, kept so the instance stage can reuse the
        # instances already extracted during candidate extraction (the 2N -> N
        # fold) instead of issuing a second per-chunk LLM pass.
        self._source_chunk_candidates: list[ChunkConceptCandidates] = []

    def _stage(self, name: str) -> None:
        if self.stage_callback is not None:
            self.stage_callback(name)

    async def generalize(
        self,
        chunks: list[ChunkConceptCandidates],
    ) -> GlobalOntology:
        return (await self.generalize_with_artifacts(chunks)).ontology

    async def generalize_with_artifacts(
        self,
        chunks: list[ChunkConceptCandidates],
    ) -> ConceptBuildArtifacts:
        # Keep the original per-chunk candidates for the folded instance stage.
        self._source_chunk_candidates = chunks
        self._stage("概念聚类与初筛")
        fallback = await self._deterministic_artifacts(
            chunks,
            controlled_relations=False,
            extract_instances=False,
            semantic_clustering=False,
        )
        if not fallback.ontology.entities:
            return fallback

        payload = self._candidate_payload(chunks)
        prompt = render_concept_generalize_prompt(
            payload,
            self.config,
            self.supplemental_prompt,
        )
        try:
            self._stage("全局概念归纳")
            raw_response = await self.llm_client.complete(prompt)
            parsed = parse_concept_candidates(
                raw_response,
                "concept_global",
                max_candidates=self.config.max_concepts,
                relation_vocab=self.config.relation_vocab,
            )
        except Exception:  # noqa: BLE001 - deterministic fallback is acceptable here.
            return await self._deterministic_artifacts(
                chunks,
                controlled_relations=False,
                extract_instances=True,
            )
        if not parsed.concepts:
            return await self._deterministic_artifacts(
                chunks,
                controlled_relations=False,
                extract_instances=True,
            )
        artifacts = await self._artifacts_from_candidates(parsed)
        artifacts.ontology = self._attach_candidate_rules_and_actions(
            artifacts.ontology,
            chunks,
        )
        self._restore_global_source_provenance(artifacts, chunks)
        return artifacts

    async def _deterministic_artifacts(
        self,
        chunks: list[ChunkConceptCandidates],
        *,
        controlled_relations: bool = True,
        extract_instances: bool = True,
        semantic_clustering: bool = True,
    ) -> ConceptBuildArtifacts:
        accumulators, name_to_key = self._cluster_candidates(chunks)
        semantic_merge_count = 0
        if semantic_clustering:
            accumulators, name_to_key, semantic_merge_count = (
                await self._merge_semantic_accumulators(accumulators)
            )
        clusters_by_key = self._build_cluster_models(
            accumulators,
            self._relation_degree_by_key(chunks, name_to_key),
        )
        selected_keys, rejected, dynamic_threshold = self._select_clusters(clusters_by_key)
        ontology, relation_report, instance_report = await self._ontology_from_cluster_selection(
            chunks,
            selected_keys,
            clusters_by_key,
            name_to_key,
            controlled_relations=controlled_relations,
            extract_instances=extract_instances,
        )
        selected = [clusters_by_key[key] for key in selected_keys]
        quality = self._quality_report(
            chunks,
            selected,
            rejected,
            ontology,
            dynamic_threshold=dynamic_threshold,
            semantic_merge_count=semantic_merge_count,
        )
        return ConceptBuildArtifacts(
            ontology=ontology,
            clusters=list(clusters_by_key.values()),
            selection=ConceptSelectionReport(selected=selected, rejected=rejected),
            relation_report=relation_report,
            instance_report=instance_report,
            quality=quality,
        )

    def _cluster_candidates(
        self,
        chunks: list[ChunkConceptCandidates],
    ) -> tuple[dict[str, ConceptAccumulator], dict[str, str]]:
        accumulators: dict[str, ConceptAccumulator] = {}
        name_to_key: dict[str, str] = {}
        order = 0

        for chunk in chunks:
            for concept in chunk.concepts:
                key = self._concept_key(concept, name_to_key, accumulators)
                accumulator = accumulators.setdefault(
                    key,
                    ConceptAccumulator(
                        name=concept.name,
                        abstraction_level=concept.abstraction_level,
                        order=order,
                    ),
                )
                if accumulator.order == order:
                    order += 1
                accumulator.aliases = _dedupe(
                    [*accumulator.aliases, concept.name, *concept.aliases]
                )
                if concept.definition:
                    accumulator.definitions = _dedupe(
                        [*accumulator.definitions, concept.definition]
                    )
                accumulator.evidence = _dedupe(
                    [*accumulator.evidence, *concept.evidence]
                )
                accumulator.source_chunks = _dedupe(
                    [*accumulator.source_chunks, chunk.chunk_id]
                )
                for name in [concept.name, *concept.aliases]:
                    name_to_key[normalize_name(name)] = key
        return accumulators, name_to_key

    async def _merge_semantic_accumulators(
        self,
        accumulators: dict[str, ConceptAccumulator],
    ) -> tuple[dict[str, ConceptAccumulator], dict[str, str], int]:
        if (
            not self.config.use_embedding_clustering
            or self.embedding_client is None
            or len(accumulators) < 2
        ):
            return accumulators, _name_to_key(accumulators), 0

        keys = sorted(accumulators, key=lambda key: accumulators[key].order)
        texts = [_embedding_text(accumulators[key]) for key in keys]
        try:
            vectors = await self.embedding_client.embed_texts(texts)
        except Exception:  # noqa: BLE001 - semantic clustering is optional.
            return accumulators, _name_to_key(accumulators), 0

        parent = {key: key for key in keys}

        def find(key: str) -> str:
            while parent[key] != key:
                parent[key] = parent[parent[key]]
                key = parent[key]
            return key

        def union(left: str, right: str) -> None:
            root_left = find(left)
            root_right = find(right)
            if root_left == root_right:
                return
            if accumulators[root_left].order <= accumulators[root_right].order:
                parent[root_right] = root_left
            else:
                parent[root_left] = root_right

        for left_index, left_key in enumerate(keys):
            for right_index in range(left_index + 1, len(keys)):
                similarity = cosine_similarity(vectors[left_index], vectors[right_index])
                if similarity >= self.config.embedding_similarity_threshold:
                    union(left_key, keys[right_index])

        groups_by_root: dict[str, list[str]] = {}
        for key in keys:
            groups_by_root.setdefault(find(key), []).append(key)
        groups = [group for group in groups_by_root.values() if len(group) > 1]
        if not groups:
            return accumulators, _name_to_key(accumulators), 0

        consumed: set[str] = set()
        merged: dict[str, ConceptAccumulator] = {}
        for group in groups:
            group = sorted(group, key=lambda key: accumulators[key].order)
            canonical_key = group[0]
            canonical = accumulators[canonical_key]
            for duplicate_key in group[1:]:
                duplicate = accumulators[duplicate_key]
                canonical.aliases = _dedupe(
                    [*canonical.aliases, duplicate.name, *duplicate.aliases]
                )
                canonical.definitions = _dedupe(
                    [*canonical.definitions, *duplicate.definitions]
                )
                canonical.evidence = _dedupe(
                    [*canonical.evidence, *duplicate.evidence]
                )
                canonical.source_chunks = _dedupe(
                    [*canonical.source_chunks, *duplicate.source_chunks]
                )
                canonical.abstraction_level = _higher_abstraction(
                    canonical.abstraction_level,
                    duplicate.abstraction_level,
                )
                canonical.semantic_merged = True
                consumed.add(duplicate_key)
            merged[canonical_key] = canonical
            consumed.add(canonical_key)

        for key in keys:
            if key not in consumed:
                merged[key] = accumulators[key]
        merged = dict(sorted(merged.items(), key=lambda item: item[1].order))
        return merged, _name_to_key(merged), sum(len(group) - 1 for group in groups)

    def _build_cluster_models(
        self,
        accumulators: dict[str, ConceptAccumulator],
        relation_degree_by_key: dict[str, int],
    ) -> dict[str, ConceptCluster]:
        clusters: dict[str, ConceptCluster] = {}
        for index, (key, accumulator) in enumerate(accumulators.items(), start=1):
            relation_degree = relation_degree_by_key.get(key, 0)
            score = _cluster_score(accumulator, relation_degree)
            clusters[key] = ConceptCluster(
                cluster_id=f"cluster_{index:06d}",
                canonical_name=accumulator.name,
                aliases=[alias for alias in accumulator.aliases if alias != accumulator.name],
                merged_names=accumulator.aliases,
                definitions=accumulator.definitions,
                evidence=accumulator.evidence,
                source_chunks=accumulator.source_chunks,
                abstraction_level=accumulator.abstraction_level,
                relation_degree=relation_degree,
                score=score,
                keep_reasons=_keep_reasons(accumulator, relation_degree),
            )
        return clusters

    def _select_clusters(
        self,
        clusters_by_key: dict[str, ConceptCluster],
    ) -> tuple[list[str], list[ConceptCluster], float]:
        ranked = sorted(
            clusters_by_key.items(),
            key=lambda item: (
                -item[1].score,
                -item[1].relation_degree,
                -len(item[1].source_chunks),
            ),
        )
        top_score = ranked[0][1].score if ranked else 0.0
        dynamic_threshold = max(
            self.config.selection_score_threshold,
            round(top_score * self.config.selection_score_ratio, 3),
        )
        selected_keys: list[str] = []
        rejected: list[ConceptCluster] = []
        for key, cluster in ranked:
            reject_reasons = _reject_reasons(cluster, self.config.min_evidence)
            if cluster.score < self.config.selection_score_threshold:
                reject_reasons.append("低于概念评分阈值")
            if cluster.score < dynamic_threshold:
                reject_reasons.append("低于动态概念评分阈值")
            if len(selected_keys) >= self.config.max_concepts:
                reject_reasons.append("超过最大概念数")
            if reject_reasons:
                rejected.append(cluster.model_copy(update={"reject_reasons": reject_reasons}))
                continue
            selected_keys.append(key)
        if not selected_keys and ranked:
            selected_keys = [ranked[0][0]]
            rejected = [
                cluster.model_copy(update={"reject_reasons": ["低分概念兜底裁剪"]})
                for key, cluster in ranked
                if key not in set(selected_keys)
            ]
        return selected_keys, rejected, dynamic_threshold

    async def _ontology_from_cluster_selection(
        self,
        chunks: list[ChunkConceptCandidates],
        selected_keys: list[str],
        clusters_by_key: dict[str, ConceptCluster],
        name_to_key: dict[str, str],
        controlled_relations: bool,
        extract_instances: bool,
    ) -> tuple[GlobalOntology, ConceptRelationReport, ConceptInstanceReport]:
        entities: list[Entity] = []
        id_by_key: dict[str, str] = {}
        for index, key in enumerate(selected_keys, start=1):
            cluster = clusters_by_key[key]
            entity_id = f"c{index:06d}"
            id_by_key[key] = entity_id
            entities.append(
                Entity(
                    id=entity_id,
                    name=cluster.canonical_name,
                    entity_type="Concept",
                    aliases=cluster.aliases,
                    attributes={
                        "说明": cluster.definitions[0] if cluster.definitions else "",
                        "抽象层级": cluster.abstraction_level,
                        "证据数量": len(cluster.evidence),
                        "关系度": cluster.relation_degree,
                        "评分": round(cluster.score, 3),
                        "合并名称": "、".join(cluster.merged_names[:8]),
                    },
                    source_chunks=cluster.source_chunks,
                )
            )

        triples, relation_report = await self._build_relations(
            chunks,
            selected_keys,
            clusters_by_key,
            name_to_key,
            id_by_key,
            controlled_relations=controlled_relations,
        )
        if extract_instances:
            self._stage("真实实例抽取")
            instances, instance_report = await self._build_instances(
                selected_keys,
                clusters_by_key,
                id_by_key,
                relation_report.relations,
            )
        else:
            instances, instance_report = [], ConceptInstanceReport(mode="deferred")
        ontology = GlobalOntology.from_entities_and_triples(
            entities,
            triples,
            instances=instances,
        )
        ontology = self._attach_candidate_rules_and_actions(ontology, chunks)
        return ontology, relation_report, instance_report

    def _attach_candidate_rules_and_actions(
        self,
        ontology: GlobalOntology,
        chunks: list[ChunkConceptCandidates],
    ) -> GlobalOntology:
        """Bind chunk-level rule/action candidates to final concept ids.

        Candidate extraction uses concept names while the final schema uses
        consolidated ids.  This pass resolves names/aliases after global
        generalization and keeps business rules separate from normal triples.
        """
        entity_id_by_name: dict[str, str] = {}
        for entity in ontology.entities.values():
            for name in [entity.name, *entity.aliases]:
                normalized = normalize_name(name)
                if normalized:
                    entity_id_by_name.setdefault(normalized, entity.id)

        rules_by_key: dict[tuple[object, ...], OntologyRule] = {}
        rule_id_by_local_ref: dict[tuple[str, str], str] = {}
        for existing in ontology.rules:
            key = _rule_semantic_key(existing)
            rules_by_key[key] = existing.model_copy()

        for chunk in chunks:
            for candidate in chunk.rules:
                scope_id = _candidate_entity_id(candidate.scope, entity_id_by_name)
                if not scope_id:
                    continue
                conditions = [
                    RuleCondition(
                        id=condition.id,
                        field=condition.field,
                        operator=condition.operator,
                        value=condition.value,
                        value_type=condition.value_type,
                    )
                    for condition in candidate.conditions
                ]
                rule = OntologyRule(
                    id="pending",
                    name=candidate.name,
                    description=candidate.description,
                    scope_entity_id=scope_id,
                    logic=candidate.logic,
                    conditions=conditions,
                    confidence=candidate.confidence,
                    source_chunks=[chunk.chunk_id],
                )
                key = _rule_semantic_key(rule)
                existing = rules_by_key.get(key)
                if existing is None:
                    existing = rule.model_copy(
                        update={"id": f"rule_{len(rules_by_key) + 1:06d}"}
                    )
                    rules_by_key[key] = existing
                else:
                    existing.confidence = max(
                        existing.confidence,
                        candidate.confidence,
                    )
                    existing.source_chunks = _dedupe(
                        [*existing.source_chunks, chunk.chunk_id]
                    )
                    if not existing.description and candidate.description:
                        existing.description = candidate.description
                rule_id_by_local_ref[(chunk.chunk_id, candidate.id)] = existing.id
                rule_id_by_local_ref[
                    (chunk.chunk_id, normalize_name(candidate.name))
                ] = existing.id

        actions_by_key: dict[tuple[object, ...], OntologyAction] = {}
        for existing in ontology.actions:
            key = (
                normalize_name(existing.name),
                tuple(existing.input_entity_ids),
                tuple(existing.output_entity_ids),
                tuple(existing.target_entity_ids),
            )
            actions_by_key[key] = existing.model_copy()

        for chunk in chunks:
            for candidate in chunk.actions:
                inputs = _candidate_entity_ids(candidate.inputs, entity_id_by_name)
                outputs = _candidate_entity_ids(candidate.outputs, entity_id_by_name)
                targets = _candidate_entity_ids(candidate.targets, entity_id_by_name)
                trigger_rule_ids = _dedupe(
                    [
                        rule_id
                        for reference in candidate.trigger_rules
                        if (
                            rule_id := rule_id_by_local_ref.get(
                                (chunk.chunk_id, reference)
                            )
                            or rule_id_by_local_ref.get(
                                (chunk.chunk_id, normalize_name(reference))
                            )
                        )
                    ]
                )
                key = (
                    normalize_name(candidate.name),
                    tuple(inputs),
                    tuple(outputs),
                    tuple(targets),
                )
                existing = actions_by_key.get(key)
                if existing is None:
                    existing = OntologyAction(
                        id=f"act_{len(actions_by_key) + 1:06d}",
                        name=candidate.name,
                        description=candidate.description,
                        input_entity_ids=inputs,
                        output_entity_ids=outputs,
                        trigger_rule_ids=trigger_rule_ids,
                        target_entity_ids=targets,
                        action_type=candidate.action_type,
                        executor=candidate.executor,
                        action_plan=candidate.action_plan,
                        feedback=candidate.feedback,
                        source_chunks=[chunk.chunk_id],
                    )
                    actions_by_key[key] = existing
                    continue
                existing.trigger_rule_ids = _dedupe(
                    [*existing.trigger_rule_ids, *trigger_rule_ids]
                )
                existing.source_chunks = _dedupe(
                    [*existing.source_chunks, chunk.chunk_id]
                )
                if not existing.description and candidate.description:
                    existing.description = candidate.description
                if not existing.executor and candidate.executor:
                    existing.executor = candidate.executor
                if not existing.action_plan and candidate.action_plan:
                    existing.action_plan = candidate.action_plan
                if not existing.feedback and candidate.feedback:
                    existing.feedback = candidate.feedback

        return ontology.model_copy(
            update={
                "rules": list(rules_by_key.values()),
                "actions": list(actions_by_key.values()),
            }
        )

    def _concept_key(
        self,
        concept: ConceptCandidate,
        name_to_key: dict[str, str],
        accumulators: dict[str, ConceptAccumulator],
    ) -> str:
        for name in [concept.name, *concept.aliases]:
            normalized = normalize_name(name)
            if normalized in name_to_key:
                return name_to_key[normalized]
        similar_key = _similar_concept_key(concept, accumulators)
        if similar_key:
            return similar_key
        return normalize_name(concept.name)

    def _relation_degree_by_key(
        self,
        chunks: list[ChunkConceptCandidates],
        name_to_key: dict[str, str],
    ) -> dict[str, int]:
        degree: dict[str, int] = {}
        for chunk in chunks:
            for relation in chunk.relations:
                source_key = name_to_key.get(normalize_name(relation.source))
                target_key = name_to_key.get(normalize_name(relation.target))
                if not source_key or not target_key or source_key == target_key:
                    continue
                degree[source_key] = degree.get(source_key, 0) + 1
                degree[target_key] = degree.get(target_key, 0) + 1
        return degree

    async def _build_relations(
        self,
        chunks: list[ChunkConceptCandidates],
        selected_keys: list[str],
        clusters_by_key: dict[str, ConceptCluster],
        name_to_key: dict[str, str],
        id_by_key: dict[str, str],
        controlled_relations: bool,
    ) -> tuple[list[Triple], ConceptRelationReport]:
        fallback_relations = self._candidate_relations_for_selected(
            chunks,
            selected_keys,
            clusters_by_key,
            name_to_key,
        )
        fallback_triples = self._relations_to_triples(
            fallback_relations,
            selected_keys,
            clusters_by_key,
            id_by_key,
            source_chunks=["candidate_relations"],
        )
        fallback_report = ConceptRelationReport(
            relations=fallback_relations,
            mode="candidate_fallback",
        )
        if (
            not controlled_relations
            or not self.config.controlled_relation_build
            or not selected_keys
        ):
            return fallback_triples, fallback_report

        payload = self._relation_payload(
            selected_keys,
            clusters_by_key,
            fallback_relations,
        )
        prompt = render_concept_relation_prompt(
            payload,
            self.config,
            self.supplemental_prompt,
        )
        try:
            self._stage("概念关系构建")
            raw_response = await self.llm_client.complete(prompt)
            controlled_relations = parse_controlled_relations(
                raw_response,
                allowed_concepts=[
                    clusters_by_key[key].canonical_name for key in selected_keys
                ],
                relation_vocab=self.config.relation_vocab,
            )
        except Exception as exc:  # noqa: BLE001 - fallback preserves build completion.
            fallback_report.error = str(exc)
            return fallback_triples, fallback_report
        if not controlled_relations:
            fallback_report.error = "controlled relation build returned no relations"
            return fallback_triples, fallback_report
        controlled_triples = self._relations_to_triples(
            controlled_relations,
            selected_keys,
            clusters_by_key,
            id_by_key,
            source_chunks=["controlled_relation_build"],
        )
        if not controlled_triples:
            fallback_report.error = "controlled relation build produced no valid triples"
            return fallback_triples, fallback_report
        return controlled_triples, ConceptRelationReport(
            relations=controlled_relations,
            mode="controlled_llm",
        )

    def _candidate_relations_for_selected(
        self,
        chunks: list[ChunkConceptCandidates],
        selected_keys: list[str],
        clusters_by_key: dict[str, ConceptCluster],
        name_to_key: dict[str, str],
    ) -> list[RelationCandidate]:
        selected = set(selected_keys)
        relations_by_key: dict[tuple[str, str, str], RelationCandidate] = {}
        for chunk in chunks:
            for relation in chunk.relations:
                source_key = name_to_key.get(normalize_name(relation.source))
                target_key = name_to_key.get(normalize_name(relation.target))
                if (
                    not source_key
                    or not target_key
                    or source_key == target_key
                    or source_key not in selected
                    or target_key not in selected
                ):
                    continue
                source = clusters_by_key[source_key].canonical_name
                target = clusters_by_key[target_key].canonical_name
                key = (source, relation.predicate, target)
                existing = relations_by_key.get(key)
                if existing is None:
                    relations_by_key[key] = relation.model_copy(
                        update={"source": source, "target": target}
                    )
                    continue
                existing.evidence = _dedupe([*existing.evidence, *relation.evidence])
                existing.confidence = max(existing.confidence, relation.confidence)
        return list(relations_by_key.values())

    def _relations_to_triples(
        self,
        relations: list[RelationCandidate],
        selected_keys: list[str],
        clusters_by_key: dict[str, ConceptCluster],
        id_by_key: dict[str, str],
        *,
        source_chunks: list[str],
    ) -> list[Triple]:
        key_by_name = {
            clusters_by_key[key].canonical_name: key for key in selected_keys
        }
        triples_by_key: dict[tuple[str, str, str], Triple] = {}
        for relation in relations:
            source_key = key_by_name.get(relation.source)
            target_key = key_by_name.get(relation.target)
            if not source_key or not target_key or source_key == target_key:
                continue
            source_id = id_by_key.get(source_key)
            target_id = id_by_key.get(target_key)
            if not source_id or not target_id:
                continue
            key = (source_id, relation.predicate, target_id)
            if key not in triples_by_key:
                triples_by_key[key] = Triple(
                    subject_id=source_id,
                    predicate=relation.predicate,
                    object_kind="entity",
                    object_id=target_id,
                    confidence=relation.confidence,
                    source_chunks=source_chunks,
                )
                continue
            triples_by_key[key].confidence = max(
                triples_by_key[key].confidence,
                relation.confidence,
            )
        return list(triples_by_key.values())

    def _relation_payload(
        self,
        selected_keys: list[str],
        clusters_by_key: dict[str, ConceptCluster],
        candidate_relations: list[RelationCandidate],
    ) -> dict[str, Any]:
        return {
            "final_concepts": [
                {
                    "name": clusters_by_key[key].canonical_name,
                    "definition": clusters_by_key[key].definitions[0]
                    if clusters_by_key[key].definitions
                    else "",
                    "aliases": clusters_by_key[key].aliases[:8],
                    "evidence": clusters_by_key[key].evidence[:5],
                    "score": clusters_by_key[key].score,
                }
                for key in selected_keys
            ],
            "candidate_relations": [
                {
                    "source": relation.source,
                    "predicate": relation.predicate,
                    "target": relation.target,
                    "evidence": relation.evidence[:3],
                    "confidence": relation.confidence,
                }
                for relation in candidate_relations
            ],
            "relation_vocab": self.config.relation_vocab,
        }

    async def _build_instances(
        self,
        selected_keys: list[str],
        clusters_by_key: dict[str, ConceptCluster],
        id_by_key: dict[str, str],
        concept_relations: list[RelationCandidate],
    ) -> tuple[list[OntologyInstance], ConceptInstanceReport]:
        if (
            not self.config.extract_real_instances
            or self.config.max_instances_per_concept <= 0
            or not selected_keys
        ):
            return [], ConceptInstanceReport(mode="disabled")

        candidates, errors = await self._collect_instance_candidates(
            selected_keys, clusters_by_key
        )
        if errors and not candidates:
            return [], ConceptInstanceReport(mode="failed", error=" | ".join(errors[:5]))
        if not candidates:
            return [], ConceptInstanceReport(mode="real_llm", instances=[])

        key_by_name = {
            clusters_by_key[key].canonical_name: key for key in selected_keys
        }
        count_by_concept: dict[str, int] = {}
        seen: set[tuple[str, str]] = set()
        instances: list[OntologyInstance] = []
        kept_candidates = []
        instance_id_by_name: dict[str, str] = {}
        for candidate in candidates:
            concept_key = key_by_name.get(candidate.concept)
            class_id = id_by_key.get(concept_key or "")
            if not class_id:
                continue
            instance_key = (candidate.concept, normalize_name(candidate.name))
            if instance_key in seen:
                continue
            concept_count = count_by_concept.get(candidate.concept, 0)
            if concept_count >= self.config.max_instances_per_concept:
                continue
            seen.add(instance_key)
            count_by_concept[candidate.concept] = concept_count + 1
            kept_candidates.append(candidate)
            instance_id = f"real_inst_{len(instances) + 1:06d}"
            instance_id_by_name[candidate.name] = instance_id
            instances.append(
                OntologyInstance(
                    id=instance_id,
                    name=candidate.name,
                    class_id=class_id,
                    attribute_values=[
                        InstanceAttributeValue(
                            attribute_name=str(name),
                            value=value,
                            data_type=_infer_instance_data_type(value),
                            confidence=candidate.confidence,
                            source_chunks=["real_instance_extraction"],
                        )
                        for name, value in candidate.attributes.items()
                    ],
                    generated=False,
                    source_chunks=["real_instance_extraction"],
                )
            )
        instance_by_id = {instance.id: instance for instance in instances}
        # In per_chunk mode the chunk-level extraction already emits inline links
        # for co-occurring instances, so the (expensive) relation pass only needs
        # to try connecting instances that are still isolated. This both speeds
        # up the build and targets exactly the remaining isolated nodes.
        focus_names: set[str] | None = None
        if self.config.instance_extraction_mode == "per_chunk":
            inline_linked: set[str] = set()
            for candidate in kept_candidates:
                for link in candidate.links:
                    if (
                        candidate.name in instance_id_by_name
                        and link.target in instance_id_by_name
                    ):
                        inline_linked.add(candidate.name)
                        inline_linked.add(link.target)
            focus_names = {
                candidate.name
                for candidate in kept_candidates
                if candidate.name not in inline_linked
            }
        (
            relation_links,
            relation_mode,
            relation_error,
            relation_pair_selection,
        ) = await self._build_instance_relations(
            kept_candidates,
            concept_relations,
            focus_names=focus_names,
        )
        kept_by_name = {candidate.name: candidate for candidate in kept_candidates}
        link_key_seen: set[tuple[str, str, str]] = set()
        for candidate in kept_candidates:
            source_id = instance_id_by_name.get(candidate.name)
            source_instance = instance_by_id.get(source_id or "")
            if source_instance is None:
                continue
            for link in candidate.links:
                target_id = instance_id_by_name.get(link.target)
                if not target_id or target_id == source_instance.id:
                    continue
                link_key = (source_instance.id, link.predicate, target_id)
                if link_key in link_key_seen:
                    continue
                link_key_seen.add(link_key)
                source_instance.link_values.append(
                    InstanceLinkValue(
                        predicate=link.predicate,
                        target_instance_id=target_id,
                        confidence=link.confidence,
                        source_chunks=["real_instance_extraction"],
                    )
                )
        for link in relation_links:
            source_id = instance_id_by_name.get(link.source)
            target_id = instance_id_by_name.get(link.target)
            source_instance = instance_by_id.get(source_id or "")
            if (
                source_instance is None
                or not target_id
                or target_id == source_instance.id
            ):
                continue
            link_key = (source_instance.id, link.predicate, target_id)
            if link_key in link_key_seen:
                continue
            link_key_seen.add(link_key)
            source_instance.link_values.append(
                InstanceLinkValue(
                    predicate=link.predicate,
                    target_instance_id=target_id,
                    confidence=link.confidence,
                    source_chunks=["instance_relation_build"],
                )
            )
            report_candidate = kept_by_name.get(link.source)
            if report_candidate is not None:
                report_candidate.links.append(link)
        linked_names = _linked_instance_names(kept_candidates)
        isolated_names = [
            candidate.name
            for candidate in kept_candidates
            if candidate.name not in linked_names
        ]
        mode = "real_llm_partial" if errors else "real_llm"
        return instances, ConceptInstanceReport(
            instances=kept_candidates,
            mode=mode,
            error=" | ".join(errors[:5]),
            relation_mode=relation_mode,
            relation_error=relation_error,
            relation_count=len(relation_links),
            relation_fallback_count=sum(
                1
                for relation in relation_links
                if relation.evidence and "heuristic_instance_relation" in relation.evidence
            ),
            instance_count=len(kept_candidates),
            linked_instance_count=len(linked_names),
            isolated_instance_count=len(isolated_names),
            isolated_instance_names=isolated_names[:50],
            relation_pair_candidate_count=relation_pair_selection.candidate_count,
            relation_pair_selected_count=len(relation_pair_selection.pairs),
            relation_pair_truncated_count=relation_pair_selection.truncated_count,
            relation_pair_max=self.config.max_instance_relation_pairs,
            relation_pair_min_per_instance=self.config.min_relation_pairs_per_instance,
            relation_pair_instances_without_candidates=(
                relation_pair_selection.instances_without_candidates[:50]
            ),
        )

    async def _collect_instance_candidates(
        self,
        selected_keys: list[str],
        clusters_by_key: dict[str, ConceptCluster],
    ) -> tuple[list[ConceptInstanceCandidate], list[str]]:
        """Extract instance candidates using the configured strategy.

        per_chunk first tries the fold: reuse the instances already extracted
        during candidate extraction (no extra LLM calls, 2N -> N). If those
        candidates carry no instances (e.g. old cached runs), it falls back to a
        dedicated per-chunk LLM pass; per_concept is the legacy concept-batched
        approach.
        """
        if self.config.instance_extraction_mode == "per_chunk":
            folded = self._folded_instance_candidates(selected_keys, clusters_by_key)
            if folded is not None:
                return folded, []
            if self.chunk_texts:
                return await self._collect_per_chunk(selected_keys, clusters_by_key)
        return await self._collect_per_concept(selected_keys, clusters_by_key)

    def _folded_instance_candidates(
        self,
        selected_keys: list[str],
        clusters_by_key: dict[str, ConceptCluster],
    ) -> list[ConceptInstanceCandidate] | None:
        """Reuse instances extracted during candidate extraction, remapping each
        instance's candidate-concept name to the final concept it was clustered
        into. Returns None when no candidate carries instances (signal to fall
        back to an LLM pass)."""
        source = self._source_chunk_candidates
        if not source or not any(chunk.instances for chunk in source):
            return None
        # Map every final concept's name/alias to its canonical name.
        name_to_canonical: dict[str, str] = {}
        for key in selected_keys:
            cluster = clusters_by_key[key]
            for raw_name in [
                cluster.canonical_name,
                *cluster.aliases,
                *cluster.merged_names,
            ]:
                normalized = normalize_name(raw_name)
                if normalized:
                    name_to_canonical.setdefault(normalized, cluster.canonical_name)
        collected: list[ConceptInstanceCandidate] = []
        for chunk in source:
            for instance in chunk.instances:
                canonical = name_to_canonical.get(normalize_name(instance.concept))
                if not canonical:
                    continue
                collected.append(instance.model_copy(update={"concept": canonical}))
        return collected

    async def _collect_per_concept(
        self,
        selected_keys: list[str],
        clusters_by_key: dict[str, ConceptCluster],
    ) -> tuple[list[ConceptInstanceCandidate], list[str]]:
        semaphore = asyncio.Semaphore(self.instance_max_concurrency)

        async def extract_instance_batch(batch_keys: list[str]):
            allowed_concepts = [
                clusters_by_key[key].canonical_name for key in batch_keys
            ]
            payload = self._instance_payload(batch_keys, clusters_by_key)
            prompt = render_concept_instance_prompt(
                payload,
                self.config,
                self.supplemental_prompt,
            )
            async with semaphore:
                try:
                    raw_response = await self.llm_client.complete(prompt)
                    return parse_concept_instances(
                        raw_response,
                        allowed_concepts=allowed_concepts,
                    ), None
                except Exception as exc:  # noqa: BLE001 - instance extraction is optional.
                    return None, "; ".join(allowed_concepts) + f": {exc}"

        batch_results = await asyncio.gather(
            *(
                extract_instance_batch(batch_keys)
                for batch_keys in _batches(
                    selected_keys, self.config.instance_batch_size
                )
            )
        )
        candidates: list[ConceptInstanceCandidate] = []
        errors: list[str] = []
        for batch_candidates, error in batch_results:
            if error is not None:
                errors.append(error)
                continue
            candidates.extend(batch_candidates)
        return candidates, errors

    async def _collect_per_chunk(
        self,
        selected_keys: list[str],
        clusters_by_key: dict[str, ConceptCluster],
    ) -> tuple[list[ConceptInstanceCandidate], list[str]]:
        # Group the final concepts by the chunks they appeared in, then extract
        # instances + links per chunk (standard-style) so co-occurring instances
        # get linked while the LLM reads the full text.
        concept_keys_by_chunk: dict[str, list[str]] = {}
        for key in selected_keys:
            for chunk_id in clusters_by_key[key].source_chunks:
                if chunk_id in self.chunk_texts:
                    concept_keys_by_chunk.setdefault(chunk_id, []).append(key)
        if not concept_keys_by_chunk:
            # No chunk text available for the selected concepts; fall back.
            return await self._collect_per_concept(selected_keys, clusters_by_key)

        # Batch several chunks per call to cut the instance pass from N to N/k
        # calls. Instance-only output stays small, so this is safe (unlike
        # folding instances into the concept-extraction call).
        chunk_batches = _batches(
            list(concept_keys_by_chunk.items()),
            max(1, self.config.instance_chunk_batch_size),
        )
        semaphore = asyncio.Semaphore(self.instance_max_concurrency)

        async def extract_chunk_batch(batch: list[tuple[str, list[str]]]):
            union_keys = list(
                dict.fromkeys(key for _chunk_id, keys in batch for key in keys)
            )
            allowed_concepts = [
                clusters_by_key[key].canonical_name for key in union_keys
            ]
            combined_text = "\n\n".join(
                self.chunk_texts[chunk_id] for chunk_id, _keys in batch
            )
            payload = self._chunk_instance_payload(
                union_keys,
                clusters_by_key,
                combined_text,
                text_char_limit=self.config.instance_source_char_limit * len(batch),
            )
            prompt = render_chunk_instance_prompt(
                payload,
                self.config,
                self.supplemental_prompt,
            )
            async with semaphore:
                try:
                    raw_response = await self.llm_client.complete(prompt)
                    return parse_concept_instances(
                        raw_response,
                        allowed_concepts=allowed_concepts,
                    ), None
                except Exception as exc:  # noqa: BLE001 - instance extraction is optional.
                    chunk_ids = ", ".join(chunk_id for chunk_id, _keys in batch)
                    return None, f"{chunk_ids}: {exc}"

        batch_results = await asyncio.gather(
            *(extract_chunk_batch(batch) for batch in chunk_batches)
        )
        candidates: list[ConceptInstanceCandidate] = []
        errors: list[str] = []
        for chunk_candidates, error in batch_results:
            if error is not None:
                errors.append(error)
                continue
            candidates.extend(chunk_candidates)
        return candidates, errors

    def _chunk_instance_payload(
        self,
        concept_keys: list[str],
        clusters_by_key: dict[str, ConceptCluster],
        source_text: str,
        text_char_limit: int | None = None,
    ) -> dict[str, Any]:
        limit = text_char_limit or self.config.instance_source_char_limit
        return {
            "final_concepts": [
                {
                    "name": clusters_by_key[key].canonical_name,
                    "definition": clusters_by_key[key].definitions[0]
                    if clusters_by_key[key].definitions
                    else "",
                    "aliases": clusters_by_key[key].aliases[:8],
                }
                for key in concept_keys
            ],
            "source_text": " ".join(source_text.split())[:limit],
            "max_instances_per_concept": self.config.max_instances_per_concept,
        }

    async def _build_instance_relations(
        self,
        instances: list[ConceptInstanceCandidate],
        concept_relations: list[RelationCandidate],
        focus_names: set[str] | None = None,
    ) -> tuple[list[RelationCandidate], str, str, InstanceRelationPairSelection]:
        empty_selection = InstanceRelationPairSelection()
        if (
            not self.config.extract_instance_relations
            or len(instances) < 2
        ):
            return [], "disabled", "", empty_selection
        # focus_names set but empty => everything already linked inline, skip.
        if focus_names is not None and not focus_names:
            return [], "skipped_all_linked", "", empty_selection
        pair_selection = self._select_instance_relation_pairs(
            instances, concept_relations, focus_names=focus_names
        )
        candidate_pairs = pair_selection.pairs
        fallback_relations = self._fallback_relations_from_pairs(candidate_pairs)
        if not candidate_pairs:
            return fallback_relations, "heuristic_fallback", "", pair_selection
        relations_by_key: dict[tuple[str, str, str], RelationCandidate] = {}
        for relation in fallback_relations:
            relations_by_key[(relation.source, relation.predicate, relation.target)] = relation
        errors: list[str] = []
        self._stage("实例关系构建")
        semaphore = asyncio.Semaphore(self.instance_max_concurrency)

        async def build_relation_batch(batch: list[InstanceRelationPair]):
            payload = self._instance_relation_payload(batch)
            prompt = render_instance_relation_prompt(
                payload,
                self.config,
                self.supplemental_prompt,
            )
            allowed_instances = sorted(
                {pair.source.name for pair in batch}
                | {pair.target.name for pair in batch}
            )
            async with semaphore:
                try:
                    raw_response = await self.llm_client.complete(prompt)
                    return parse_instance_relations(
                        raw_response,
                        allowed_instances=allowed_instances,
                        relation_vocab=self.config.relation_vocab,
                    ), None
                except Exception as exc:  # noqa: BLE001 - instance links are optional.
                    return None, str(exc)

        # Pair batches are independent; run them concurrently and merge results
        # in batch order to keep the output deterministic.
        batch_results = await asyncio.gather(
            *(
                build_relation_batch(batch)
                for batch in _batches(
                    candidate_pairs, self.config.instance_relation_batch_size
                )
            )
        )
        for relations, error in batch_results:
            if error is not None:
                errors.append(error)
                continue
            for relation in relations:
                if relation.source == relation.target:
                    continue
                key = (relation.source, relation.predicate, relation.target)
                existing = relations_by_key.get(key)
                if existing is None:
                    relations_by_key[key] = relation
                    continue
                existing.evidence = _dedupe([*existing.evidence, *relation.evidence])
                existing.confidence = max(existing.confidence, relation.confidence)
        if errors and not relations_by_key:
            return [], "failed", " | ".join(errors[:5]), pair_selection
        if errors and fallback_relations and len(relations_by_key) == len(fallback_relations):
            mode = "heuristic_fallback"
        elif errors:
            mode = "instance_relation_llm_partial"
        elif fallback_relations:
            mode = "instance_relation_llm_with_fallback"
        else:
            mode = "instance_relation_llm"
        return list(relations_by_key.values()), mode, " | ".join(errors[:5]), pair_selection

    def _instance_payload(
        self,
        selected_keys: list[str],
        clusters_by_key: dict[str, ConceptCluster],
    ) -> dict[str, Any]:
        return {
            "final_concepts": [
                {
                    "name": clusters_by_key[key].canonical_name,
                    "definition": clusters_by_key[key].definitions[0]
                    if clusters_by_key[key].definitions
                    else "",
                    "aliases": clusters_by_key[key].aliases[:8],
                    "evidence": [
                        _truncate_text(
                            evidence,
                            self.config.instance_evidence_char_limit,
                        )
                        for evidence in clusters_by_key[key].evidence[
                            : self.config.instance_evidence_per_concept
                        ]
                    ],
                    "source_text": self._concept_source_text(clusters_by_key[key]),
                }
                for key in selected_keys
            ],
            "max_instances_per_concept": self.config.max_instances_per_concept,
        }

    def _concept_source_text(self, cluster: ConceptCluster) -> str:
        """Concatenate the raw text of the chunks a concept appeared in, capped
        at instance_source_char_limit, so instance extraction can mine the full
        source rather than only the short evidence snippets."""
        limit = self.config.instance_source_char_limit
        if limit <= 0 or not self.chunk_texts:
            return ""
        parts: list[str] = []
        total = 0
        for chunk_id in cluster.source_chunks:
            text = self.chunk_texts.get(chunk_id)
            if not text:
                continue
            normalized = " ".join(text.split())
            if not normalized:
                continue
            parts.append(normalized)
            total += len(normalized)
            if total >= limit:
                break
        return "\n".join(parts)[:limit]

    def _instance_relation_payload(
        self,
        pairs: list[InstanceRelationPair],
    ) -> dict[str, Any]:
        instances_by_name = {
            pair.source.name: pair.source
            for pair in pairs
        }
        instances_by_name.update({pair.target.name: pair.target for pair in pairs})
        return {
            "instances": [
                {
                    "name": instance.name,
                    "concept": instance.concept,
                    "attributes": instance.attributes,
                    "evidence": [
                        _truncate_text(
                            evidence,
                            self.config.instance_evidence_char_limit,
                        )
                        for evidence in instance.evidence[:2]
                    ],
                    "source_text": self._instance_source_text(
                        instance.name,
                        self.config.instance_relation_source_char_limit,
                    ),
                }
                for instance in instances_by_name.values()
            ],
            "candidate_pairs": [
                {
                    "source": pair.source.name,
                    "source_concept": pair.source.concept,
                    "predicate": pair.predicate,
                    "target": pair.target.name,
                    "target_concept": pair.target.concept,
                    "evidence": [
                        _truncate_text(
                            evidence,
                            self.config.instance_evidence_char_limit,
                        )
                        for evidence in pair.evidence[:2]
                    ],
                    "score": round(pair.score, 3),
                }
                for pair in pairs
            ],
            "relation_vocab": self.config.relation_vocab,
        }

    def _select_instance_relation_pairs(
        self,
        instances: list[ConceptInstanceCandidate],
        concept_relations: list[RelationCandidate],
        focus_names: set[str] | None = None,
    ) -> InstanceRelationPairSelection:
        pairs = self._instance_relation_pairs(
            instances,
            concept_relations,
            focus_names=focus_names,
        )
        target_names = (
            set(focus_names)
            if focus_names is not None
            else {instance.name for instance in instances}
        )
        pair_names_by_instance: dict[str, int] = {name: 0 for name in target_names}
        for pair in pairs:
            if pair.source.name in pair_names_by_instance:
                pair_names_by_instance[pair.source.name] += 1
            if pair.target.name in pair_names_by_instance:
                pair_names_by_instance[pair.target.name] += 1

        selected_by_key: dict[tuple[str, str, str], InstanceRelationPair] = {}
        max_pairs = self.config.max_instance_relation_pairs
        min_pairs = self.config.min_relation_pairs_per_instance
        if min_pairs > 0:
            selected_count_by_name: dict[str, int] = {name: 0 for name in target_names}
            for name in sorted(target_names):
                if len(selected_by_key) >= max_pairs:
                    break
                for pair in pairs:
                    if pair.source.name != name and pair.target.name != name:
                        continue
                    key = _instance_relation_pair_key(pair)
                    if key in selected_by_key:
                        continue
                    selected_by_key[key] = pair
                    if pair.source.name in selected_count_by_name:
                        selected_count_by_name[pair.source.name] += 1
                    if pair.target.name in selected_count_by_name:
                        selected_count_by_name[pair.target.name] += 1
                    if selected_count_by_name[name] >= min_pairs:
                        break
                    if len(selected_by_key) >= max_pairs:
                        break

        for pair in pairs:
            if len(selected_by_key) >= max_pairs:
                break
            selected_by_key.setdefault(_instance_relation_pair_key(pair), pair)

        selected_pairs = list(selected_by_key.values())
        selected_pairs.sort(key=lambda pair: (-pair.score, pair.source.name, pair.target.name))
        return InstanceRelationPairSelection(
            pairs=selected_pairs,
            candidate_count=len(pairs),
            truncated_count=max(0, len(pairs) - len(selected_pairs)),
            instances_without_candidates=sorted(
                name for name, count in pair_names_by_instance.items() if count == 0
            ),
        )

    def _instance_relation_pairs(
        self,
        instances: list[ConceptInstanceCandidate],
        concept_relations: list[RelationCandidate],
        focus_names: set[str] | None = None,
    ) -> list[InstanceRelationPair]:
        concept_relation_by_pair: dict[tuple[str, str], list[RelationCandidate]] = {}
        for relation in concept_relations:
            concept_relation_by_pair.setdefault((relation.source, relation.target), []).append(
                relation
            )
        # Instances co-occurring in the same source chunk are strong relation
        # candidates even when their concept types have no schema relation and
        # their short evidence snippets do not overlap. This is the main lever
        # against isolated instance nodes.
        chunks_by_name = self._instance_chunk_index(instances)
        pairs: list[InstanceRelationPair] = []
        for source in instances:
            source_chunks = chunks_by_name.get(source.name, set())
            for target in instances:
                if source.name == target.name:
                    continue
                # When focusing on still-isolated instances, only keep pairs that
                # could connect one of them.
                if focus_names is not None and (
                    source.name not in focus_names and target.name not in focus_names
                ):
                    continue
                concept_relations_for_pair = concept_relation_by_pair.get(
                    (source.concept, target.concept),
                    [],
                )
                evidence = _pair_evidence(source, target)
                shared_chunks = source_chunks & chunks_by_name.get(target.name, set())
                if not concept_relations_for_pair and not evidence and not shared_chunks:
                    continue
                predicate = (
                    concept_relations_for_pair[0].predicate
                    if concept_relations_for_pair
                    else "关联"
                )
                score = 0.0
                if concept_relations_for_pair:
                    score += 2.0
                score += min(len(evidence), 2) * 2.0
                score += min(len(shared_chunks), 3) * 2.5
                score += _name_evidence_score(source, target)
                pairs.append(
                    InstanceRelationPair(
                        source=source,
                        predicate=predicate,
                        target=target,
                        evidence=evidence
                        or (
                            concept_relations_for_pair[0].evidence[:2]
                            if concept_relations_for_pair
                            else []
                        ),
                        score=score,
                    )
                )
        pairs.sort(key=lambda pair: (-pair.score, pair.source.name, pair.target.name))
        return pairs

    def _instance_chunk_index(
        self,
        instances: list[ConceptInstanceCandidate],
    ) -> dict[str, set[str]]:
        """Map each instance name to the set of chunk ids whose text mentions it,
        used to pair up instances that co-occur in the source."""
        if not self.chunk_texts:
            return {}
        index: dict[str, set[str]] = {}
        for instance in instances:
            name = instance.name
            # Skip 1-char names to avoid spurious substring co-occurrence.
            if not name or len(name) < 2 or name in index:
                continue
            index[name] = {
                chunk_id
                for chunk_id, text in self.chunk_texts.items()
                if name in text
            }
        return index

    def _instance_source_text(self, name: str, limit: int) -> str:
        """Source-chunk text mentioning an instance, capped, so the relation
        judge can confirm links from real context instead of tiny snippets."""
        if limit <= 0 or not self.chunk_texts or not name:
            return ""
        parts: list[str] = []
        total = 0
        for text in self.chunk_texts.values():
            if name not in text:
                continue
            normalized = " ".join(text.split())
            if not normalized:
                continue
            parts.append(normalized)
            total += len(normalized)
            if total >= limit:
                break
        return "\n".join(parts)[:limit]

    def _fallback_relations_from_pairs(
        self,
        pairs: list[InstanceRelationPair],
    ) -> list[RelationCandidate]:
        fallback: dict[tuple[str, str, str], RelationCandidate] = {}
        for pair in pairs:
            if not pair.evidence or pair.score < 3 or pair.predicate == "关联":
                continue
            key = (pair.source.name, pair.predicate, pair.target.name)
            if key in fallback:
                continue
            fallback[key] = RelationCandidate(
                source=pair.source.name,
                predicate=pair.predicate,
                target=pair.target.name,
                evidence=[*pair.evidence[:2], "heuristic_instance_relation"],
                confidence=0.72,
            )
        return list(fallback.values())

    def _deterministic_relations(
        self,
        chunks: list[ChunkConceptCandidates],
        name_to_key: dict[str, str],
        id_by_key: dict[str, str],
    ) -> list[Triple]:
        triples_by_key: dict[tuple[str, str, str], Triple] = {}
        for chunk in chunks:
            for relation in chunk.relations:
                source_key = name_to_key.get(normalize_name(relation.source))
                target_key = name_to_key.get(normalize_name(relation.target))
                source_id = id_by_key.get(source_key or "")
                target_id = id_by_key.get(target_key or "")
                if not source_id or not target_id or source_id == target_id:
                    continue
                key = (source_id, relation.predicate, target_id)
                if key not in triples_by_key:
                    triples_by_key[key] = Triple(
                        subject_id=source_id,
                        predicate=relation.predicate,
                        object_kind="entity",
                        object_id=target_id,
                        confidence=relation.confidence,
                        source_chunks=[chunk.chunk_id],
                    )
                    continue
                existing = triples_by_key[key]
                existing.confidence = max(existing.confidence, relation.confidence)
                existing.source_chunks = _dedupe([*existing.source_chunks, chunk.chunk_id])
        return list(triples_by_key.values())

    def _candidate_payload(self, chunks: list[ChunkConceptCandidates]) -> dict[str, Any]:
        concepts: list[dict[str, Any]] = []
        relations: list[dict[str, Any]] = []
        for chunk in chunks:
            for concept in chunk.concepts:
                concepts.append(
                    {
                        "chunk_id": chunk.chunk_id,
                        "name": concept.name,
                        "definition": concept.definition,
                        "abstraction_level": concept.abstraction_level,
                        "aliases": concept.aliases,
                        "evidence": concept.evidence[:3],
                    }
                )
            for relation in chunk.relations:
                relations.append(
                    {
                        "chunk_id": chunk.chunk_id,
                        "source": relation.source,
                        "predicate": relation.predicate,
                        "target": relation.target,
                        "evidence": relation.evidence[:2],
                    }
                )
        return {
            "concepts": concepts,
            "relations": relations,
            "limits": {
                "max_concepts": self.config.max_concepts,
                "min_evidence": self.config.min_evidence,
                "abstraction_level": self.config.abstraction_level,
                "relation_vocab": self.config.relation_vocab,
            },
        }

    async def _artifacts_from_candidates(
        self,
        candidates: ChunkConceptCandidates,
    ) -> ConceptBuildArtifacts:
        artifacts = await self._deterministic_artifacts([candidates])
        if artifacts.ontology.entities:
            return artifacts
        return ConceptBuildArtifacts(ontology=GlobalOntology())

    def _restore_global_source_provenance(
        self,
        artifacts: ConceptBuildArtifacts,
        source_chunks: list[ChunkConceptCandidates],
    ) -> None:
        """Replace the synthetic ``concept_global`` marker with real chunk ids.

        The global-generalization response is parsed as one synthetic candidate
        batch, which otherwise makes every schema node appear to originate from
        ``concept_global``.  Reconcile global concepts against their original
        names/aliases/evidence so the canvas can show the source document chunks.
        """
        valid_chunk_ids = {chunk.chunk_id for chunk in source_chunks}
        selected_clusters = artifacts.selection.selected

        for cluster in [*selected_clusters, *artifacts.selection.rejected]:
            cluster_names = {
                normalize_name(name)
                for name in [
                    cluster.canonical_name,
                    *cluster.aliases,
                    *cluster.merged_names,
                ]
                if normalize_name(name)
            }
            cluster_evidence = {
                normalize_name(item) for item in cluster.evidence if normalize_name(item)
            }
            matched_chunks: list[str] = []
            for source_chunk in source_chunks:
                matched = False
                for concept in source_chunk.concepts:
                    concept_names = {
                        normalize_name(name)
                        for name in [concept.name, *concept.aliases]
                        if normalize_name(name)
                    }
                    name_match = bool(cluster_names & concept_names) or any(
                        _is_near_name(left, right)
                        for left in cluster_names
                        for right in concept_names
                    )
                    concept_evidence = {
                        normalize_name(item)
                        for item in concept.evidence
                        if normalize_name(item)
                    }
                    if name_match or bool(cluster_evidence & concept_evidence):
                        matched = True
                        break
                if matched:
                    matched_chunks.append(source_chunk.chunk_id)
            if matched_chunks:
                cluster.source_chunks = _dedupe(matched_chunks)

        entity_clusters: dict[str, ConceptCluster] = {}
        for cluster in selected_clusters:
            for name in [
                cluster.canonical_name,
                *cluster.aliases,
                *cluster.merged_names,
            ]:
                if normalized := normalize_name(name):
                    entity_clusters.setdefault(normalized, cluster)

        entities = list(artifacts.ontology.entities.values())
        for index, entity in enumerate(entities):
            cluster = next(
                (
                    entity_clusters[normalized]
                    for name in [entity.name, *entity.aliases]
                    if (normalized := normalize_name(name)) in entity_clusters
                ),
                None,
            )
            if cluster is None and index < len(selected_clusters):
                # Entity construction preserves selected-cluster order.  This
                # fallback covers a genuinely renamed global concept.
                cluster = selected_clusters[index]
            if cluster is not None and cluster.source_chunks:
                entity.source_chunks = list(cluster.source_chunks)

        for triple in artifacts.ontology.triples:
            subject = artifacts.ontology.entities.get(triple.subject_id)
            target = artifacts.ontology.entities.get(triple.object_id or "")
            resolved = _dedupe(
                [
                    *(subject.source_chunks if subject is not None else []),
                    *(target.source_chunks if target is not None else []),
                ]
            )
            if resolved:
                triple.source_chunks = resolved

        for rule in artifacts.ontology.rules:
            resolved = [
                chunk_id
                for chunk_id in rule.source_chunks
                if chunk_id in valid_chunk_ids
            ]
            scope = artifacts.ontology.entities.get(rule.scope_entity_id)
            if not resolved and scope is not None:
                resolved = list(scope.source_chunks)
            rule.source_chunks = _dedupe(resolved)

        for action in artifacts.ontology.actions:
            resolved = [
                chunk_id
                for chunk_id in action.source_chunks
                if chunk_id in valid_chunk_ids
            ]
            if not resolved:
                for entity_id in [
                    *action.input_entity_ids,
                    *action.output_entity_ids,
                    *action.target_entity_ids,
                ]:
                    entity = artifacts.ontology.entities.get(entity_id)
                    if entity is not None:
                        resolved.extend(entity.source_chunks)
            action.source_chunks = _dedupe(resolved)

        for instance in artifacts.ontology.instances:
            resolved = [
                chunk_id
                for chunk_id in instance.source_chunks
                if chunk_id in valid_chunk_ids
            ]
            if not resolved:
                entity = artifacts.ontology.entities.get(instance.class_id)
                if entity is not None:
                    resolved = list(entity.source_chunks)
            instance.source_chunks = _dedupe(resolved)

    def _quality_report(
        self,
        chunks: list[ChunkConceptCandidates],
        selected: list[ConceptCluster],
        rejected: list[ConceptCluster],
        ontology: GlobalOntology,
        *,
        dynamic_threshold: float,
        semantic_merge_count: int,
    ) -> ConceptQualityReport:
        predicate_distribution: dict[str, int] = {}
        degree_by_entity: dict[str, int] = {entity_id: 0 for entity_id in ontology.entities}
        for triple in ontology.triples:
            predicate_distribution[triple.predicate] = (
                predicate_distribution.get(triple.predicate, 0) + 1
            )
            degree_by_entity[triple.subject_id] = degree_by_entity.get(triple.subject_id, 0) + 1
            if triple.object_id:
                degree_by_entity[triple.object_id] = degree_by_entity.get(triple.object_id, 0) + 1
        rejection_reason_counts: dict[str, int] = {}
        for cluster in rejected:
            for reason in cluster.reject_reasons:
                rejection_reason_counts[reason] = rejection_reason_counts.get(reason, 0) + 1
        isolated_count = sum(1 for degree in degree_by_entity.values() if degree == 0)
        average_degree = (
            round(sum(degree_by_entity.values()) / len(degree_by_entity), 3)
            if degree_by_entity
            else 0.0
        )
        instance_link_count = sum(
            len(instance.link_values) for instance in ontology.instances
        )
        linked_instance_ids: set[str] = set()
        instance_ids = {instance.id for instance in ontology.instances}
        for instance in ontology.instances:
            for link in instance.link_values:
                linked_instance_ids.add(instance.id)
                if link.target_instance_id in instance_ids:
                    linked_instance_ids.add(link.target_instance_id)
        isolated_instance_count = len(instance_ids - linked_instance_ids)
        return ConceptQualityReport(
            candidate_concept_count=sum(len(chunk.concepts) for chunk in chunks),
            candidate_relation_count=sum(len(chunk.relations) for chunk in chunks),
            cluster_count=len(selected) + len(rejected),
            semantic_cluster_merge_count=semantic_merge_count,
            selected_concept_count=len(selected),
            rejected_concept_count=len(rejected),
            final_relation_count=len(ontology.triples),
            isolated_concept_count=isolated_count,
            average_relation_degree=average_degree,
            instance_count=len(ontology.instances),
            instance_link_count=instance_link_count,
            isolated_instance_count=isolated_instance_count,
            isolated_instance_ratio=(
                round(isolated_instance_count / len(ontology.instances), 3)
                if ontology.instances
                else 0.0
            ),
            relation_predicate_distribution=predicate_distribution,
            rejection_reason_counts=rejection_reason_counts,
            max_concepts=self.config.max_concepts,
            min_evidence=self.config.min_evidence,
            selection_score_threshold=self.config.selection_score_threshold,
            dynamic_score_threshold=dynamic_threshold,
        )


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _rule_semantic_key(rule: OntologyRule) -> tuple[object, ...]:
    return (
        normalize_name(rule.name),
        rule.scope_entity_id,
        rule.logic,
        tuple(
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
        ),
    )


def _candidate_entity_ids(
    names: list[str],
    entity_id_by_name: dict[str, str],
) -> list[str]:
    return _dedupe(
        [
            entity_id
            for name in names
            if (entity_id := _candidate_entity_id(name, entity_id_by_name))
        ]
    )


def _candidate_entity_id(
    name: str,
    entity_id_by_name: dict[str, str],
) -> str | None:
    normalized = normalize_name(name)
    if not normalized:
        return None
    exact = entity_id_by_name.get(normalized)
    if exact:
        return exact
    matches = {
        entity_id
        for candidate_name, entity_id in entity_id_by_name.items()
        if normalized in candidate_name or candidate_name in normalized
    }
    return next(iter(matches)) if len(matches) == 1 else None


def _instance_relation_pair_key(
    pair: InstanceRelationPair,
) -> tuple[str, str, str]:
    return (pair.source.name, pair.predicate, pair.target.name)


def _linked_instance_names(
    instances: list[ConceptInstanceCandidate],
) -> set[str]:
    names = {instance.name for instance in instances}
    linked: set[str] = set()
    for instance in instances:
        for link in instance.links:
            linked.add(instance.name)
            if link.target in names:
                linked.add(link.target)
    return linked


def _name_to_key(accumulators: dict[str, ConceptAccumulator]) -> dict[str, str]:
    name_to_key: dict[str, str] = {}
    for key, accumulator in accumulators.items():
        for name in [accumulator.name, *accumulator.aliases]:
            normalized = normalize_name(name)
            if normalized:
                name_to_key[normalized] = key
    return name_to_key


def _embedding_text(accumulator: ConceptAccumulator) -> str:
    parts = [
        f"概念名称：{accumulator.name}",
        f"别名：{'、'.join(accumulator.aliases[:8])}",
        f"定义：{'；'.join(accumulator.definitions[:3])}",
        f"证据：{'；'.join(accumulator.evidence[:5])}",
        f"抽象层级：{accumulator.abstraction_level}",
    ]
    return "\n".join(part for part in parts if part and not part.endswith("："))


def _higher_abstraction(left: str, right: str) -> str:
    rank = {"low": 0, "medium": 1, "high": 2}
    return left if rank.get(left, 1) >= rank.get(right, 1) else right


def _batches(values: list[_T], size: int) -> list[list[_T]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


def _pair_evidence(
    source: ConceptInstanceCandidate,
    target: ConceptInstanceCandidate,
) -> list[str]:
    evidence: list[str] = []
    source_evidence = source.evidence[:3]
    target_evidence = target.evidence[:3]
    for item in [*source_evidence, *target_evidence]:
        if source.name in item and target.name in item:
            evidence.append(item)
    source_set = set(source_evidence)
    for item in target_evidence:
        if item in source_set:
            evidence.append(item)
    for item in source_evidence:
        if target.name in item:
            evidence.append(item)
    for item in target_evidence:
        if source.name in item:
            evidence.append(item)
    return _dedupe(evidence)


def _name_evidence_score(
    source: ConceptInstanceCandidate,
    target: ConceptInstanceCandidate,
) -> float:
    score = 0.0
    all_evidence = [*source.evidence[:3], *target.evidence[:3]]
    if any(source.name in item and target.name in item for item in all_evidence):
        score += 1.5
    if any(target.name in item for item in source.evidence[:3]):
        score += 1.0
    if any(source.name in item for item in target.evidence[:3]):
        score += 1.0
    return score


def _truncate_text(value: str, limit: int) -> str:
    text = " ".join(str(value).split())
    if len(text) <= limit:
        return text
    return f"{text[:limit].rstrip()}..."


def _similar_concept_key(
    concept: ConceptCandidate,
    accumulators: dict[str, ConceptAccumulator],
) -> str | None:
    candidate_names = [concept.name, *concept.aliases]
    normalized_candidates = [normalize_name(name) for name in candidate_names if name]
    for key, accumulator in accumulators.items():
        existing_names = [accumulator.name, *accumulator.aliases]
        for candidate in normalized_candidates:
            for existing in [normalize_name(name) for name in existing_names if name]:
                if _is_near_name(candidate, existing):
                    return key
    return None


def _is_near_name(left: str, right: str) -> bool:
    if not left or not right or left == right:
        return left == right
    shorter, longer = (left, right) if len(left) <= len(right) else (right, left)
    if len(shorter) < 3:
        return False
    return shorter in longer and len(longer) - len(shorter) <= 6


def _cluster_score(accumulator: ConceptAccumulator, relation_degree: int) -> float:
    score = 0.0
    score += min(len(accumulator.source_chunks), 8) * 3.0
    score += min(len(accumulator.evidence), 10) * 1.5
    score += min(relation_degree, 8) * 2.0
    score += min(len(accumulator.aliases), 6) * 0.5
    if accumulator.abstraction_level == "high":
        score += 2.0
    elif accumulator.abstraction_level == "medium":
        score += 1.0
    if _looks_like_instance(accumulator.name):
        score -= 4.0
    return round(score, 3)


def _keep_reasons(
    accumulator: ConceptAccumulator,
    relation_degree: int,
) -> list[str]:
    reasons: list[str] = []
    if len(accumulator.source_chunks) > 1:
        reasons.append("跨 chunk 出现")
    if len(accumulator.evidence) > 0:
        reasons.append("有文本证据")
    if relation_degree > 0:
        reasons.append("参与概念关系")
    if accumulator.abstraction_level == "high":
        reasons.append("高抽象层级")
    if accumulator.semantic_merged:
        reasons.append("embedding 语义合并")
    return reasons


def _reject_reasons(cluster: ConceptCluster, min_evidence: int) -> list[str]:
    reasons: list[str] = []
    if (
        len(cluster.evidence) < min_evidence
        and len(cluster.source_chunks) <= 1
        and cluster.relation_degree == 0
    ):
        reasons.append("证据不足且无关系")
    if _looks_like_instance(cluster.canonical_name):
        reasons.append("疑似具体实例")
    return reasons


def _looks_like_instance(name: str) -> bool:
    stripped = name.strip()
    if not stripped:
        return False
    digit_count = sum(1 for char in stripped if char.isdigit())
    if digit_count >= 2:
        return True
    instance_markers = ("有限公司", "股份", "先生", "女士", "第", "号", "年", "月", "日")
    return any(marker in stripped for marker in instance_markers)


def _infer_instance_data_type(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    return "string"
