from datetime import datetime

from ontology_construct.models import (
    ActionInvocation,
    Entity,
    GlobalOntology,
    InstanceAttributeValue,
    InstanceLinkValue,
    OntologyAction,
    OntologyInstance,
    Triple,
)
from ontology_construct.output import format_global_ontology


def _build_ontology() -> GlobalOntology:
    return GlobalOntology.from_entities_and_triples(
        [
            Entity(
                id="g000001",
                name="用户",
                entity_type="BusinessObject",
                attributes={"用户名": "用户姓名"},
                source_chunks=["chunk_0001"],
            ),
            Entity(id="g000002", name="订单", entity_type="BusinessObject"),
        ],
        [
            Triple(
                subject_id="g000001",
                predicate="拥有",
                object_kind="entity",
                object_id="g000002",
                confidence=0.9,
                source_chunks=["chunk_0001"],
            ),
            Triple(
                subject_id="g000002",
                predicate="订单状态",
                object_kind="literal",
                object_value="状态值",
            ),
        ],
        actions=[
            OntologyAction(
                id="act_000001",
                name="创建订单",
                input_entity_ids=["g000001"],
                output_entity_ids=["g000002"],
            )
        ],
        instances=[
            OntologyInstance(
                id="inst_000001",
                name="张三",
                class_id="g000001",
                attribute_values=[
                    InstanceAttributeValue(
                        attribute_name="用户名",
                        value="张三",
                        data_type="string",
                    )
                ],
                link_values=[
                    InstanceLinkValue(
                        predicate="拥有",
                        target_instance_id="inst_000002",
                        confidence=0.95,
                    )
                ],
            ),
            OntologyInstance(
                id="inst_000002",
                name="订单A",
                class_id="g000002",
                attribute_values=[
                    InstanceAttributeValue(
                        attribute_name="金额",
                        value=100,
                        data_type="integer",
                    )
                ],
            ),
        ],
        action_invocations=[
            ActionInvocation(
                id="inv_000001",
                action_name="创建订单",
                input_instance_ids=["inst_000001"],
                output_instance_ids=["inst_000002"],
            )
        ],
    )


def test_format_global_ontology_emits_instance_attribute_values_and_links() -> None:
    formatted = format_global_ontology(
        _build_ontology(),
        ontology_name="用户行为本体",
        ontology_desc="用户行为分析相关实体",
        profile="business_process",
        timestamp=datetime(2024, 1, 1, 10, 0, 0),
    )

    assert formatted.ontology.ontologyName == "用户行为本体"
    assert [item.objectName for item in formatted.objects] == ["用户", "订单"]
    assert formatted.objects[0].attributes[0].attributeName == "用户名"
    # The 金额 attribute is auto-promoted from instance attribute_values.
    assert "金额" in [attribute.attributeName for attribute in formatted.objects[1].attributes]

    zhangsan = formatted.instances[0]
    assert zhangsan.instanceName == "张三"
    assert zhangsan.objectId == 1
    assert zhangsan.attributeValues[0].attributeName == "用户名"
    assert zhangsan.attributeValues[0].attributeId == 1
    assert zhangsan.attributeValues[0].value == "张三"

    assert zhangsan.linkValues[0].linkInfo == "拥有"
    assert zhangsan.linkValues[0].targetInstanceId == 2
    assert zhangsan.linkValues[0].targetObjectId == 2
    assert zhangsan.linkValues[0].linkId == 1

    order = formatted.instances[1]
    money_attribute = order.attributeValues[0]
    assert money_attribute.attributeName == "金额"
    assert money_attribute.dataType == "integer"
    assert money_attribute.value == 100
    assert money_attribute.attributeId is not None

    invocation = formatted.actionInvocations[0]
    assert invocation.actionName == "创建订单"
    assert invocation.actionId == formatted.actions[0].actionId
    assert invocation.inputInstances == [1]
    assert invocation.outputInstances == [2]
    assert invocation.inputInstanceNames == ["张三"]
    assert invocation.outputInstanceNames == ["订单A"]


def test_format_global_ontology_promotes_instance_links_to_class_schema() -> None:
    # A class-level triple between 用户 and 订单 does not exist, but the
    # instances reference it via link_values. The formatter should backfill the
    # missing FormattedLink and the instance link should resolve to its linkId.
    ontology = GlobalOntology.from_entities_and_triples(
        [
            Entity(id="g000001", name="用户"),
            Entity(id="g000002", name="订单"),
        ],
        triples=[],
        instances=[
            OntologyInstance(
                id="inst_000001",
                name="张三",
                class_id="g000001",
                link_values=[
                    InstanceLinkValue(
                        predicate="下单",
                        target_instance_id="inst_000002",
                    )
                ],
            ),
            OntologyInstance(
                id="inst_000002",
                name="PO-001",
                class_id="g000002",
            ),
        ],
    )

    formatted = format_global_ontology(
        ontology,
        ontology_name="测试",
        ontology_desc="",
        timestamp=datetime(2024, 1, 1, 10, 0, 0),
    )

    assert len(formatted.links) == 1
    promoted_link = formatted.links[0]
    assert promoted_link.linkInfo == "下单"
    assert promoted_link.sourceObjectName == "用户"
    assert promoted_link.targetObjectName == "订单"

    instance_link = formatted.instances[0].linkValues[0]
    assert instance_link.linkId == promoted_link.linkId


def test_format_global_ontology_deduplicates_codes_globally() -> None:
    # Two different objects pick the same name after normalization (rare but
    # possible after dedup); the second occurrence should suffix `_2` so codes
    # remain unique.
    ontology = GlobalOntology.from_entities_and_triples(
        [
            Entity(id="g000001", name="订单"),
            Entity(id="g000002", name="订单"),
        ],
        triples=[],
        actions=[
            OntologyAction(id="act_000001", name="审批"),
            OntologyAction(id="act_000002", name="审批"),
        ],
    )

    formatted = format_global_ontology(
        ontology,
        ontology_name="X",
        ontology_desc="",
        timestamp=datetime(2024, 1, 1, 10, 0, 0),
    )

    codes = [item.objectCode for item in formatted.objects]
    assert len(set(codes)) == len(codes)
    action_codes = [item.actionCode for item in formatted.actions]
    assert len(set(action_codes)) == len(action_codes)


def test_format_global_ontology_uses_majority_vote_for_data_type() -> None:
    ontology = GlobalOntology.from_entities_and_triples(
        [Entity(id="g000001", name="订单")],
        triples=[],
        instances=[
            OntologyInstance(
                id=f"inst_{i:06d}",
                name=f"PO-{i:03d}",
                class_id="g000001",
                attribute_values=[
                    InstanceAttributeValue(
                        attribute_name="金额",
                        value=value,
                        data_type=data_type,
                    )
                ],
            )
            for i, (data_type, value) in enumerate(
                # Three integer signals, one stray string. Majority wins.
                [("string", "壹仟"), ("integer", 1000), ("integer", 2000), ("integer", 3000)],
                start=1,
            )
        ],
    )

    formatted = format_global_ontology(
        ontology,
        ontology_name="X",
        ontology_desc="",
        timestamp=datetime(2024, 1, 1, 10, 0, 0),
    )

    amount_attr = next(
        attribute
        for attribute in formatted.objects[0].attributes
        if attribute.attributeName == "金额"
    )
    assert amount_attr.dataType == "integer"


def test_format_global_ontology_keeps_value_in_sample_value_not_description() -> None:
    ontology = GlobalOntology.from_entities_and_triples(
        [
            Entity(
                id="g000001",
                name="用户",
                attributes={"用户名": "张三"},
            )
        ],
        triples=[],
    )

    formatted = format_global_ontology(
        ontology,
        ontology_name="X",
        ontology_desc="",
        timestamp=datetime(2024, 1, 1, 10, 0, 0),
    )

    attribute = formatted.objects[0].attributes[0]
    assert attribute.attributeName == "用户名"
    assert attribute.description == ""
    assert attribute.sampleValue == "张三"


def test_format_global_ontology_generates_instances_with_schema_values() -> None:
    formatted = format_global_ontology(
        _build_ontology(),
        ontology_name="用户行为本体",
        ontology_desc="...",
        profile="business_process",
        generated_instance_count=2,
        timestamp=datetime(2024, 1, 1, 10, 0, 0),
    )

    generated = [instance for instance in formatted.instances if instance.generated]
    assert len(generated) == 2
    # Each generated instance attaches to a class and includes that class's attributes.
    for instance in generated:
        attribute_names = {av.attributeName for av in instance.attributeValues}
        if instance.objectName == "用户":
            assert "用户名" in attribute_names
        if instance.objectName == "订单":
            assert "金额" in attribute_names or "订单状态" in attribute_names
