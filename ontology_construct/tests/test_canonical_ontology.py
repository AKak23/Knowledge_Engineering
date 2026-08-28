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
from ontology_construct.output import build_canonical_ontology


def _build_ontology() -> GlobalOntology:
    return GlobalOntology.from_entities_and_triples(
        [
            Entity(
                id="g000001",
                name="用户",
                entity_type="BusinessObject",
                attributes={"用户名": "用户姓名"},
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
                    InstanceAttributeValue(attribute_name="用户名", value="张三"),
                ],
                link_values=[
                    InstanceLinkValue(predicate="拥有", target_instance_id="inst_000002"),
                ],
            ),
            OntologyInstance(
                id="inst_000002",
                name="订单A",
                class_id="g000002",
                attribute_values=[
                    InstanceAttributeValue(
                        attribute_name="金额", value=100, data_type="integer"
                    ),
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


def _export():
    return build_canonical_ontology(
        _build_ontology(), schema_id="user_behavior", scenario="biz", sub_scenario="behavior"
    )


def _by_name(schema, name):
    return next(obj for obj in schema.object_types if obj.name == name)


def test_schema_layer_object_types_and_properties() -> None:
    schema = _export().schema_def
    assert schema.schema_id == "user_behavior"
    names = [obj.name for obj in schema.object_types]
    assert names == ["用户", "订单"]
    # 用户 picks up attribute key 用户名; 订单 promotes literal triple + instance attr.
    user = _by_name(schema, "用户")
    assert [p.name for p in user.properties] == ["用户名"]
    order = _by_name(schema, "订单")
    order_props = {p.name for p in order.properties}
    assert {"订单状态", "金额"} <= order_props
    # data type from instance signal is normalized.
    assert next(p for p in order.properties if p.name == "金额").type == "integer"


def test_primary_key_is_inferred() -> None:
    schema = _export().schema_def
    # No id-like property on these entities, so the first property is chosen.
    assert _by_name(schema, "用户").primary_key == "用户名"


def test_primary_key_prefers_id_pattern() -> None:
    ontology = GlobalOntology.from_entities_and_triples(
        [Entity(id="g1", name="订单", attributes={"名称": "x", "order_id": "PO-1"})],
        triples=[],
    )
    schema = build_canonical_ontology(
        ontology, schema_id="s", scenario="x", sub_scenario="y"
    ).schema_def
    obj = _by_name(schema, "订单")
    assert obj.primary_key == "order_id"
    assert obj.title_property == "名称"


def test_link_types_from_triples_with_cardinality() -> None:
    schema = _export().schema_def
    assert len(schema.link_types) == 1
    link = schema.link_types[0]
    assert (link.name, link.source_type, link.target_type) == ("拥有", "用户", "订单")
    # One source links one target → ONE_TO_ONE inferred from instances.
    assert link.cardinality == "ONE_TO_ONE"


def test_action_types_resolve_actor_and_target() -> None:
    schema = _export().schema_def
    assert len(schema.action_types) == 1
    action = schema.action_types[0]
    assert action.name == "创建订单"
    assert action.actor_type == "用户"
    assert action.target_type == "订单"


def test_graph_layer_objects_links_actions() -> None:
    graph = _export().graph
    assert graph.schema_id == "user_behavior"
    objs = {o.instance_id: o for o in graph.objects}
    assert objs["inst_000001"].object_type == "用户"
    assert objs["inst_000001"].attributes == {"用户名": "张三"}

    assert len(graph.links) == 1
    link = graph.links[0]
    assert (link.source_id, link.target_id, link.link_type) == (
        "inst_000001",
        "inst_000002",
        "拥有",
    )

    assert len(graph.actions) == 1
    event = graph.actions[0]
    assert event.action_type == "创建订单"
    assert event.actor_id == "inst_000001"
    assert event.target_id == "inst_000002"


def test_primary_key_hint_is_honored_over_heuristic() -> None:
    # 用户名 would be the heuristic pick (first property), but the LLM hinted 工号.
    ontology = GlobalOntology.from_entities_and_triples(
        [
            Entity(
                id="g1",
                name="员工",
                attributes={"用户名": "x"},
                primary_key="工号",
            )
        ],
        triples=[],
    )
    schema = build_canonical_ontology(
        ontology, schema_id="s", scenario="x", sub_scenario="y"
    ).schema_def
    obj = _by_name(schema, "员工")
    assert obj.primary_key == "工号"
    # The hinted key is surfaced as a property so the schema stays consistent.
    assert "工号" in [p.name for p in obj.properties]


def test_cardinality_hint_overrides_instance_inference() -> None:
    # Instances would imply ONE_TO_ONE (1 source -> 1 target), but the LLM
    # declared the type-level relation as ONE_TO_MANY.
    ontology = GlobalOntology.from_entities_and_triples(
        [Entity(id="g1", name="用户"), Entity(id="g2", name="订单")],
        [
            Triple(
                subject_id="g1",
                predicate="拥有",
                object_kind="entity",
                object_id="g2",
                cardinality="ONE_TO_MANY",
            )
        ],
        instances=[
            OntologyInstance(
                id="i1",
                name="张三",
                class_id="g1",
                link_values=[InstanceLinkValue(predicate="拥有", target_instance_id="i2")],
            ),
            OntologyInstance(id="i2", name="PO-1", class_id="g2"),
        ],
    )
    schema = build_canonical_ontology(
        ontology, schema_id="s", scenario="x", sub_scenario="y"
    ).schema_def
    assert schema.link_types[0].cardinality == "ONE_TO_MANY"


def test_instance_link_without_triple_falls_back_to_predicate() -> None:
    ontology = GlobalOntology.from_entities_and_triples(
        [Entity(id="g1", name="用户"), Entity(id="g2", name="订单")],
        triples=[],
        instances=[
            OntologyInstance(
                id="i1",
                name="张三",
                class_id="g1",
                link_values=[InstanceLinkValue(predicate="下单", target_instance_id="i2")],
            ),
            OntologyInstance(id="i2", name="PO-1", class_id="g2"),
        ],
    )
    graph = build_canonical_ontology(
        ontology, schema_id="s", scenario="x", sub_scenario="y"
    ).graph
    assert len(graph.links) == 1
    assert graph.links[0].link_type == "下单"


def _constraint_ontology() -> GlobalOntology:
    statuses = ["待开始", "进行中", "已完成", "已完成", "进行中", "待开始"]
    instances = [
        OntologyInstance(
            id=f"i{index}",
            name=f"工序{index}",
            class_id="g_proc",
            attribute_values=[
                InstanceAttributeValue(attribute_name="编号", value=f"P-{index}"),
                InstanceAttributeValue(attribute_name="状态", value=status),
            ],
        )
        for index, status in enumerate(statuses)
    ]
    return GlobalOntology.from_entities_and_triples(
        [Entity(id="g_proc", name="工序", primary_key="编号")],
        triples=[],
        actions=[OntologyAction(id="a1", name="完成工序")],
        instances=instances,
        action_invocations=[
            ActionInvocation(
                id="inv1",
                action_name="完成工序",
                input_instance_ids=["i0"],
                output_instance_ids=["i1"],
            )
        ],
    )


def _proc(schema):
    return _by_name(schema, "工序")


def test_required_inferred_from_full_coverage() -> None:
    schema = build_canonical_ontology(
        _constraint_ontology(), schema_id="s", scenario="x", sub_scenario="y"
    ).schema_def
    props = {p.name: p for p in _proc(schema).properties}
    # 状态 is present on every instance -> required; primary key 编号 is required.
    assert props["状态"].required is True
    assert props["编号"].required is True


def test_enum_values_inferred_from_low_cardinality() -> None:
    schema = build_canonical_ontology(
        _constraint_ontology(), schema_id="s", scenario="x", sub_scenario="y"
    ).schema_def
    props = {p.name: p for p in _proc(schema).properties}
    # 状态 repeats over a small value set -> enum; 编号 is unique -> not an enum.
    assert set(props["状态"].enum_values or []) == {"待开始", "进行中", "已完成"}
    assert props["编号"].enum_values is None


def test_action_effect_targets_status_property() -> None:
    schema = build_canonical_ontology(
        _constraint_ontology(), schema_id="s", scenario="x", sub_scenario="y"
    ).schema_def
    action = next(a for a in schema.action_types if a.name == "完成工序")
    # target 工序 has a 状态 property -> a modifies effect is derived.
    assert action.effects == [
        {"type": "modifies", "object_type": "工序", "property": "状态"}
    ]


def test_constraints_skipped_below_min_instances() -> None:
    ontology = GlobalOntology.from_entities_and_triples(
        [Entity(id="g_proc", name="工序", primary_key="编号")],
        triples=[],
        instances=[
            OntologyInstance(
                id="i0",
                name="工序0",
                class_id="g_proc",
                attribute_values=[
                    InstanceAttributeValue(attribute_name="状态", value="进行中"),
                ],
            )
        ],
    )
    schema = build_canonical_ontology(
        ontology, schema_id="s", scenario="x", sub_scenario="y"
    ).schema_def
    props = {p.name: p for p in _proc(schema).properties}
    # Only 1 instance (< min_instances): no value-based constraint, but the
    # primary key is still required structurally.
    assert props["状态"].required is False
    assert props["状态"].enum_values is None
