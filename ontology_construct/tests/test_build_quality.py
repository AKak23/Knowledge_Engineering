from ontology_construct.build_quality import reconcile_build_quality
from ontology_construct.config import BuildQualityConfig
from ontology_construct.models import (
    Entity,
    GlobalOntology,
    OntologyAction,
    OntologyInstance,
    Triple,
)


def _quality_fixture() -> GlobalOntology:
    return GlobalOntology.from_entities_and_triples(
        [
            Entity(id="device", name="冷却设备", source_chunks=["chunk_1"]),
            Entity(id="controller", name="控制器", source_chunks=["chunk_1"]),
            Entity(id="work_order", name="检修工单", source_chunks=["chunk_1"]),
            Entity(id="orphan", name="孤立候选", source_chunks=["chunk_2"]),
        ],
        [Triple(subject_id="controller", predicate="控制", object_id="device")],
        actions=[OntologyAction(id="a1", name="创建工单", target_entity_ids=["work_order"])],
        instances=[
            OntologyInstance(id="d1", name="冷却机组01", class_id="device"),
            OntologyInstance(id="c1", name="控制器01", class_id="controller"),
        ],
    )


def test_build_quality_quarantines_only_true_orphans() -> None:
    ontology, report = reconcile_build_quality(
        _quality_fixture(),
        config=BuildQualityConfig(),
        total_chunk_count=2,
        failed_chunk_count=0,
    )

    assert "orphan" not in ontology.entities
    assert "work_order" in ontology.entities
    assert report.status == "warning"
    assert report.quarantined_entity_count == 1
    assert report.uncovered_entity_count == 1
    coverage = {item.entity_id: item.status for item in report.coverage}
    assert coverage == {
        "device": "grounded",
        "controller": "grounded",
        "work_order": "uncovered",
    }


def test_build_quality_rejects_partial_chunk_results() -> None:
    _ontology, report = reconcile_build_quality(
        _quality_fixture(),
        config=BuildQualityConfig(require_all_chunks=True),
        total_chunk_count=2,
        failed_chunk_count=1,
    )

    assert report.status == "failed"
    assert report.publishable is False
    assert report.chunk_success_ratio == 0.5


def test_build_quality_can_report_orphan_without_quarantining_it() -> None:
    ontology, report = reconcile_build_quality(
        _quality_fixture(),
        config=BuildQualityConfig(quarantine_orphan_entities=False),
        total_chunk_count=1,
        failed_chunk_count=0,
    )

    assert "orphan" in ontology.entities
    assert report.orphan_entity_count == 1
    assert report.quarantined_entity_count == 0
