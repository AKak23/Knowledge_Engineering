import asyncio
import json

from ontology_construct.config import InstancePostprocessConfig
from ontology_construct.instance_postprocess import (
    needs_instance_connectivity_repair,
    repair_instance_connectivity,
)
from ontology_construct.models import (
    Entity,
    GlobalOntology,
    InstanceLinkValue,
    OntologyInstance,
    Triple,
)


class FakeRepairClient:
    def __init__(self, payload: dict | str):
        self.payload = payload

    async def complete(self, _prompt: str, *, system_prompt: str | None = None) -> str:
        if isinstance(self.payload, str):
            return self.payload
        return json.dumps(self.payload, ensure_ascii=False)


def _ontology() -> GlobalOntology:
    return GlobalOntology.from_entities_and_triples(
        [
            Entity(id="order", name="订单"),
            Entity(id="equipment", name="设备"),
        ],
        [
            Triple(
                subject_id="equipment",
                predicate="服务订单",
                object_id="order",
            )
        ],
        instances=[
            OntologyInstance(
                id="eq1",
                name="1号设备",
                class_id="equipment",
                link_values=[
                    InstanceLinkValue(predicate="服务订单", target_instance_id="ord1")
                ],
            ),
            OntologyInstance(id="ord1", name="订单1", class_id="order"),
            OntologyInstance(id="eq2", name="2号设备", class_id="equipment"),
            OntologyInstance(id="noise", name="临时噪声", class_id="equipment"),
        ],
    )


def test_repair_instance_connectivity_applies_llm_links_and_drops() -> None:
    ontology = _ontology()
    client = FakeRepairClient(
        {
            "drop_instance_ids": ["noise"],
            "links": [
                {
                    "source_instance_id": "eq2",
                    "predicate": "服务订单",
                    "target_instance_id": "ord1",
                    "confidence": 0.9,
                }
            ],
            "actions": [
                {
                    "name": "设备服务订单",
                    "description": "设备参与订单生产",
                    "input_instance_ids": ["eq2"],
                    "output_instance_ids": ["ord1"],
                }
            ],
        }
    )

    repaired, stats = asyncio.run(
        repair_instance_connectivity(
            ontology,
            client,
            InstancePostprocessConfig(max_drop_ratio=0.5),
        )
    )

    assert "noise" not in {instance.id for instance in repaired.instances}
    eq2 = next(instance for instance in repaired.instances if instance.id == "eq2")
    assert eq2.link_values[0].predicate == "服务订单"
    assert eq2.link_values[0].target_instance_id == "ord1"
    assert repaired.action_invocations[0].action_name == "设备服务订单"
    assert stats.isolated_before == 2
    assert stats.isolated_after == 0
    assert stats.llm_links_added == 1
    assert stats.instances_dropped == 1
    assert stats.actions_added == 1


def test_repair_instance_connectivity_fallback_connects_unhandled_isolates() -> None:
    ontology = _ontology()
    client = FakeRepairClient({"links": [], "drop_instance_ids": []})

    repaired, stats = asyncio.run(
        repair_instance_connectivity(ontology, client, InstancePostprocessConfig())
    )

    assert stats.isolated_before == 2
    assert stats.isolated_after == 0
    assert stats.fallback_links_added == 2
    by_id = {instance.id: instance for instance in repaired.instances}
    assert by_id["eq2"].link_values
    assert by_id["noise"].link_values


def test_repair_instance_connectivity_ignores_invalid_llm_references() -> None:
    ontology = _ontology()
    client = FakeRepairClient(
        {
            "drop_instance_ids": ["ord1"],
            "links": [
                {
                    "source_instance_id": "missing",
                    "predicate": "伪造关系",
                    "target_instance_id": "eq2",
                    "confidence": 1,
                },
                {
                    "source_instance_id": "eq2",
                    "predicate": "低置信关系",
                    "target_instance_id": "ord1",
                    "confidence": 0.1,
                },
            ],
        }
    )

    repaired, stats = asyncio.run(
        repair_instance_connectivity(ontology, client, InstancePostprocessConfig())
    )

    assert "ord1" in {instance.id for instance in repaired.instances}
    assert stats.llm_links_added == 0
    assert stats.instances_dropped == 0
    assert stats.fallback_links_added == 2
    assert stats.isolated_after == 0


def test_needs_instance_connectivity_repair_detects_isolated_instances() -> None:
    assert needs_instance_connectivity_repair(_ontology(), InstancePostprocessConfig())
    assert not needs_instance_connectivity_repair(
        _ontology(),
        InstancePostprocessConfig(enabled=False),
    )


def test_drop_degenerate_instances_removes_pipe_named_nodes():
    from ontology_construct.instance_postprocess import drop_degenerate_instances
    from ontology_construct.models import GlobalOntology, InstanceLinkValue, OntologyInstance

    ontology = GlobalOntology(
        instances=[
            OntologyInstance(id="i1", name="|", class_id="c1"),
            OntologyInstance(
                id="i2",
                name="1号加工中心",
                class_id="c2",
                link_values=[
                    InstanceLinkValue(predicate="执行于", target_instance_id="i1"),
                    InstanceLinkValue(predicate="执行于", target_instance_id="i3"),
                ],
            ),
            OntologyInstance(id="i3", name="0011号生产工单", class_id="c3"),
        ],
    )
    cleaned, dropped = drop_degenerate_instances(ontology)
    assert dropped == 1
    assert {i.id for i in cleaned.instances} == {"i2", "i3"}
    # the dangling link to the dropped "|" node is pruned; the good one stays
    i2 = next(i for i in cleaned.instances if i.id == "i2")
    assert [lv.target_instance_id for lv in i2.link_values] == ["i3"]


def test_drop_degenerate_keeps_code_like_names():
    from ontology_construct.instance_postprocess import drop_degenerate_instances
    from ontology_construct.models import GlobalOntology, OntologyInstance

    ontology = GlobalOntology(
        instances=[OntologyInstance(id="i1", name="001", class_id="c1")],
    )
    _cleaned, dropped = drop_degenerate_instances(ontology)
    assert dropped == 0  # "001" has alphanumerics — a real (if terse) name
