from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import json
from typing import Any, Literal

from ontology_construct.config import BuildQualityConfig
from ontology_construct.instance_builder import InstanceBuildReport
from ontology_construct.models import Entity, GlobalOntology


CoverageStatus = Literal["grounded", "uncovered", "orphan", "quarantined"]
QualityStatus = Literal["passed", "warning", "failed"]


@dataclass(frozen=True)
class EntityCoverage:
    entity_id: str
    entity_name: str
    real_instance_count: int
    generated_instance_count: int
    schema_relation_degree: int
    rule_reference_count: int
    action_reference_count: int
    source_chunks: list[str]
    status: CoverageStatus

    def as_dict(self) -> dict[str, Any]:
        return {
            "entityId": self.entity_id,
            "entityName": self.entity_name,
            "realInstanceCount": self.real_instance_count,
            "generatedInstanceCount": self.generated_instance_count,
            "schemaRelationDegree": self.schema_relation_degree,
            "ruleReferenceCount": self.rule_reference_count,
            "actionReferenceCount": self.action_reference_count,
            "sourceChunks": self.source_chunks,
            "status": self.status,
        }


@dataclass
class BuildQualityReport:
    status: QualityStatus
    publishable: bool
    total_chunk_count: int
    failed_chunk_count: int
    chunk_success_ratio: float
    entity_count_before: int
    entity_count_after: int
    real_instance_count: int
    covered_entity_count: int
    uncovered_entity_count: int
    orphan_entity_count: int
    quarantined_entity_count: int
    instance_build_failed_batch_count: int = 0
    coverage: list[EntityCoverage] = field(default_factory=list)
    quarantined_entities: list[EntityCoverage] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "publishable": self.publishable,
            "totalChunkCount": self.total_chunk_count,
            "failedChunkCount": self.failed_chunk_count,
            "chunkSuccessRatio": self.chunk_success_ratio,
            "entityCountBefore": self.entity_count_before,
            "entityCountAfter": self.entity_count_after,
            "realInstanceCount": self.real_instance_count,
            "coveredEntityCount": self.covered_entity_count,
            "uncoveredEntityCount": self.uncovered_entity_count,
            "orphanEntityCount": self.orphan_entity_count,
            "quarantinedEntityCount": self.quarantined_entity_count,
            "instanceBuildFailedBatchCount": self.instance_build_failed_batch_count,
            "coverage": [item.as_dict() for item in self.coverage],
            "quarantinedEntities": [
                item.as_dict() for item in self.quarantined_entities
            ],
            "warnings": self.warnings,
        }


def reconcile_build_quality(
    ontology: GlobalOntology,
    *,
    config: BuildQualityConfig,
    total_chunk_count: int,
    failed_chunk_count: int,
    instance_build_report: InstanceBuildReport | None = None,
) -> tuple[GlobalOntology, BuildQualityReport]:
    """Assess class grounding and quarantine truly orphan schema classes.

    A class is an orphan only when it has no concrete instance and is not
    referenced by another class relation, rule, or action. Connected schema-only
    classes are retained and reported as uncovered instead of being deleted.
    """

    initial_coverage = _entity_coverage(ontology)
    orphan_ids = {
        item.entity_id for item in initial_coverage if item.status == "orphan"
    }
    orphan_candidates = [
        item
        for item in initial_coverage
        if item.entity_id in orphan_ids
    ]
    quarantined: list[EntityCoverage] = []
    reconciled = ontology
    if config.quarantine_orphan_entities and orphan_ids:
        reconciled = _without_entities(ontology, orphan_ids)
        quarantined = [
            EntityCoverage(**{**item.__dict__, "status": "quarantined"})
            for item in orphan_candidates
        ]

    coverage = _entity_coverage(reconciled)
    covered = [item for item in coverage if item.status == "grounded"]
    uncovered = [item for item in coverage if item.status == "uncovered"]
    remaining_orphans = [item for item in coverage if item.status == "orphan"]
    instance_failures = (
        instance_build_report.failed_batch_count if instance_build_report else 0
    )
    warnings: list[str] = []
    if failed_chunk_count:
        warnings.append(f"仍有 {failed_chunk_count} 个 chunk 抽取失败")
    if instance_failures:
        warnings.append(f"仍有 {instance_failures} 个实例补全批次失败")
    if quarantined:
        names = "、".join(item.entity_name for item in quarantined[:10])
        warnings.append(f"{len(quarantined)} 个孤立类已隔离：{names}")
    if remaining_orphans:
        names = "、".join(item.entity_name for item in remaining_orphans[:10])
        warnings.append(f"{len(remaining_orphans)} 个孤立类待核验：{names}")
    if config.warn_on_uncovered_entities and uncovered:
        names = "、".join(item.entity_name for item in uncovered[:10])
        warnings.append(f"{len(uncovered)} 个类没有真实实例：{names}")

    publishable = not (config.require_all_chunks and failed_chunk_count)
    status: QualityStatus
    if not publishable:
        status = "failed"
    elif warnings:
        status = "warning"
    else:
        status = "passed"
    successful_chunks = max(total_chunk_count - failed_chunk_count, 0)
    success_ratio = (
        round(successful_chunks / total_chunk_count, 4)
        if total_chunk_count
        else 1.0
    )
    report = BuildQualityReport(
        status=status,
        publishable=publishable,
        total_chunk_count=total_chunk_count,
        failed_chunk_count=failed_chunk_count,
        chunk_success_ratio=success_ratio,
        entity_count_before=len(ontology.entities),
        entity_count_after=len(reconciled.entities),
        real_instance_count=sum(not item.generated for item in reconciled.instances),
        covered_entity_count=len(covered),
        uncovered_entity_count=len(uncovered),
        orphan_entity_count=len(remaining_orphans),
        quarantined_entity_count=len(quarantined),
        instance_build_failed_batch_count=instance_failures,
        coverage=coverage,
        quarantined_entities=quarantined,
        warnings=warnings,
    )
    return reconciled, report


def save_build_quality_report(report: BuildQualityReport, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "build_quality.json").write_text(
        json.dumps(report.as_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _entity_coverage(ontology: GlobalOntology) -> list[EntityCoverage]:
    relation_degree = {entity_id: 0 for entity_id in ontology.entities}
    for triple in ontology.triples:
        if triple.object_kind != "entity" or not triple.object_id:
            continue
        if triple.subject_id in relation_degree:
            relation_degree[triple.subject_id] += 1
        if triple.object_id in relation_degree:
            relation_degree[triple.object_id] += 1

    real_instances = {entity_id: 0 for entity_id in ontology.entities}
    generated_instances = {entity_id: 0 for entity_id in ontology.entities}
    for instance in ontology.instances:
        target = generated_instances if instance.generated else real_instances
        if instance.class_id in target:
            target[instance.class_id] += 1

    rule_refs = {entity_id: 0 for entity_id in ontology.entities}
    for rule in ontology.rules:
        if rule.scope_entity_id in rule_refs:
            rule_refs[rule.scope_entity_id] += 1

    action_refs = {entity_id: 0 for entity_id in ontology.entities}
    for action in ontology.actions:
        for entity_id in {
            *action.input_entity_ids,
            *action.output_entity_ids,
            *action.target_entity_ids,
        }:
            if entity_id in action_refs:
                action_refs[entity_id] += 1

    coverage: list[EntityCoverage] = []
    for entity in ontology.entities.values():
        structural_refs = (
            relation_degree[entity.id]
            + rule_refs[entity.id]
            + action_refs[entity.id]
        )
        if real_instances[entity.id] > 0:
            status: CoverageStatus = "grounded"
        elif structural_refs == 0:
            status = "orphan"
        else:
            status = "uncovered"
        coverage.append(
            EntityCoverage(
                entity_id=entity.id,
                entity_name=entity.name,
                real_instance_count=real_instances[entity.id],
                generated_instance_count=generated_instances[entity.id],
                schema_relation_degree=relation_degree[entity.id],
                rule_reference_count=rule_refs[entity.id],
                action_reference_count=action_refs[entity.id],
                source_chunks=entity.source_chunks,
                status=status,
            )
        )
    return coverage


def _without_entities(
    ontology: GlobalOntology,
    entity_ids: set[str],
) -> GlobalOntology:
    entities: dict[str, Entity] = {
        entity_id: entity
        for entity_id, entity in ontology.entities.items()
        if entity_id not in entity_ids
    }
    triples = [
        triple
        for triple in ontology.triples
        if triple.subject_id not in entity_ids
        and not (triple.object_kind == "entity" and triple.object_id in entity_ids)
    ]
    name_to_id = {
        name: entity_id
        for name, entity_id in ontology.name_to_id.items()
        if entity_id in entities
    }
    return ontology.model_copy(
        update={
            "entities": entities,
            "triples": triples,
            "name_to_id": name_to_id,
        }
    )
