from ontology_construct.models import (
    Entity,
    GlobalOntology,
    LocalOntology,
    OntologyInstance,
    RuleCondition,
    Triple,
)
from ontology_construct.storage import load_json_model, save_json_model
import pytest


def test_local_ontology_serializes_round_trip(tmp_path) -> None:
    ontology = LocalOntology(
        chunk_id="chunk_0001",
        entities=[
            Entity(
                id="e1",
                name="苹果公司",
                entity_type="Organization",
                aliases=["Apple", "Apple"],
                source_chunks=["chunk_0001", "chunk_0001"],
            )
        ],
        triples=[
            Triple(
                subject_id="e1",
                predicate="成立年份",
                object_kind="literal",
                object_value=1976,
                confidence=0.9,
                source_chunks=["chunk_0001", "chunk_0001"],
            )
        ],
    )
    path = tmp_path / "local.json"

    save_json_model(ontology, path)
    loaded = load_json_model(LocalOntology, path)

    assert loaded == ontology
    assert loaded.entities[0].aliases == ["Apple"]
    assert loaded.triples[0].source_chunks == ["chunk_0001"]


def test_global_ontology_builds_name_index() -> None:
    entity = Entity(id="e1", name="苹果公司", aliases=["Apple Inc."])
    ontology = GlobalOntology.from_entities_and_triples([entity], [])

    assert ontology.entities["e1"] == entity
    assert ontology.name_to_id["苹果公司"] == "e1"
    assert ontology.name_to_id["Apple Inc."] == "e1"


def test_triple_requires_matching_object_payload() -> None:
    with pytest.raises(ValueError, match="entity triple requires object_id"):
        Triple(subject_id="e1", predicate="创始人")

    with pytest.raises(ValueError, match="literal triple requires object_value"):
        Triple(subject_id="e1", predicate="成立年份", object_kind="literal")


def test_ontology_instance_migrates_legacy_attributes_dict() -> None:
    legacy = {
        "id": "inst_000001",
        "name": "订单A",
        "class_id": "g000002",
        "attributes": {"金额": 100, "状态": "已支付"},
    }

    instance = OntologyInstance.model_validate(legacy)

    names = {value.attribute_name for value in instance.attribute_values}
    assert names == {"金额", "状态"}
    by_name = {value.attribute_name: value.value for value in instance.attribute_values}
    assert by_name["金额"] == 100
    assert by_name["状态"] == "已支付"


def test_ontology_instance_marks_legacy_unbound_payload() -> None:
    legacy = {"id": "inst_000002", "name": "孤儿"}

    instance = OntologyInstance.model_validate(legacy)

    assert instance.is_unbound is True


def test_rule_condition_requires_value_except_for_null_operators() -> None:
    with pytest.raises(ValueError, match="requires value"):
        RuleCondition(id="c1", field="温度", operator=">")

    condition = RuleCondition(id="c2", field="告警码", operator="为空")

    assert condition.value is None
