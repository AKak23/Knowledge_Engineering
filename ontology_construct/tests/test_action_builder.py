from ontology_construct.action_builder import build_actions_from_instances
from ontology_construct.config import ActionBuilderConfig
from ontology_construct.models import (
    ActionInvocation,
    Entity,
    GlobalOntology,
    InstanceLinkValue,
    OntologyAction,
    OntologyInstance,
)
from ontology_construct.output import build_canonical_ontology


def _event_ontology(extra_events: int = 0) -> GlobalOntology:
    """订单 / 工序 / 生产事件, where each event 关联s a process and an order."""
    instances = [
        OntologyInstance(id="inst_order", name="0001号订单", class_id="g_order"),
        OntologyInstance(id="inst_proc", name="开卷落料-工序01", class_id="g_proc"),
        OntologyInstance(
            id="inst_event",
            name="事件000009",
            class_id="g_event",
            link_values=[
                InstanceLinkValue(predicate="关联", target_instance_id="inst_proc"),
                InstanceLinkValue(predicate="关联", target_instance_id="inst_order"),
            ],
        ),
    ]
    for index in range(extra_events):
        instances.append(
            OntologyInstance(
                id=f"inst_event_{index}",
                name=f"事件{index:06d}",
                class_id="g_event",
                link_values=[
                    InstanceLinkValue(predicate="关联", target_instance_id="inst_proc"),
                    InstanceLinkValue(predicate="关联", target_instance_id="inst_order"),
                ],
            )
        )
    return GlobalOntology.from_entities_and_triples(
        [
            Entity(id="g_order", name="生产订单"),
            Entity(id="g_proc", name="加工工序"),
            Entity(id="g_event", name="生产事件"),
        ],
        triples=[],
        instances=instances,
    )


def test_derives_action_type_and_invocation_from_event() -> None:
    updated, stats = build_actions_from_instances(
        _event_ontology(), ActionBuilderConfig()
    )
    assert stats.enabled
    assert stats.source_event_instances == 1
    assert stats.actions_added == 1
    assert stats.events_added == 1

    action = updated.actions[0]
    assert action.name == "生产"  # 生产事件 -> strip 事件
    # actor = the order (non-target participant), target = the process (工序 keyword)
    assert action.input_entity_ids == ["g_order"]
    assert action.output_entity_ids == ["g_proc"]

    invocation = updated.action_invocations[0]
    assert invocation.action_name == "生产"
    assert invocation.input_instance_ids == ["inst_order"]
    assert invocation.output_instance_ids == ["inst_proc"]


def test_dedupes_action_type_across_many_events() -> None:
    updated, stats = build_actions_from_instances(
        _event_ontology(extra_events=4), ActionBuilderConfig()
    )
    # 5 event instances, all the same type -> one action type, but every event
    # links the same order/process pair, so invocations dedupe to one too.
    assert stats.source_event_instances == 5
    assert len([a for a in updated.actions if a.name == "生产"]) == 1


def test_distinct_targets_yield_distinct_events() -> None:
    ontology = _event_ontology()
    ontology.instances.append(
        OntologyInstance(id="inst_proc2", name="冲压-工序02", class_id="g_proc")
    )
    ontology.instances.append(
        OntologyInstance(
            id="inst_event2",
            name="事件000010",
            class_id="g_event",
            link_values=[
                InstanceLinkValue(predicate="关联", target_instance_id="inst_proc2"),
                InstanceLinkValue(predicate="关联", target_instance_id="inst_order"),
            ],
        )
    )
    updated, stats = build_actions_from_instances(ontology, ActionBuilderConfig())
    assert stats.events_added == 2
    targets = {inv.output_instance_ids[0] for inv in updated.action_invocations}
    assert targets == {"inst_proc", "inst_proc2"}


def test_disabled_is_noop() -> None:
    ontology = _event_ontology()
    updated, stats = build_actions_from_instances(
        ontology, ActionBuilderConfig(enabled=False)
    )
    assert not stats.enabled
    assert updated is ontology
    assert updated.actions == []


def test_no_event_instances_skips() -> None:
    ontology = GlobalOntology.from_entities_and_triples(
        [Entity(id="g_order", name="生产订单")],
        triples=[],
        instances=[OntologyInstance(id="i1", name="A", class_id="g_order")],
    )
    _, stats = build_actions_from_instances(ontology, ActionBuilderConfig())
    assert stats.skipped_reason == "no_event_instances"


def test_canonical_schema_covers_instance_actions() -> None:
    updated, _ = build_actions_from_instances(_event_ontology(), ActionBuilderConfig())
    export = build_canonical_ontology(
        updated, schema_id="s", scenario="x", sub_scenario="y"
    )
    schema_action_names = {a.name for a in export.schema_def.action_types}
    event_action_names = {e.action_type for e in export.graph.actions}
    # schema ⊇ instance actions, with actor/target resolved (not empty shells).
    assert event_action_names <= schema_action_names
    action = next(a for a in export.schema_def.action_types if a.name == "生产")
    assert action.actor_type == "生产订单"
    assert action.target_type == "加工工序"


def test_canonical_backfills_actor_target_from_invocations() -> None:
    """An action with no declared entity ids still gets actor/target from the
    instances its invocation touches (the postprocess empty-shell case)."""
    ontology = GlobalOntology.from_entities_and_triples(
        [Entity(id="g_order", name="生产订单"), Entity(id="g_proc", name="加工工序")],
        triples=[],
        actions=[OntologyAction(id="a1", name="生成工序实例")],  # no entity ids
        instances=[
            OntologyInstance(id="i_order", name="订单A", class_id="g_order"),
            OntologyInstance(id="i_proc", name="工序A", class_id="g_proc"),
        ],
        action_invocations=[
            ActionInvocation(
                id="inv1",
                action_name="生成工序实例",
                input_instance_ids=["i_order"],
                output_instance_ids=["i_proc"],
            )
        ],
    )
    schema = build_canonical_ontology(
        ontology, schema_id="s", scenario="x", sub_scenario="y"
    ).schema_def
    action = next(a for a in schema.action_types if a.name == "生成工序实例")
    assert action.actor_type == "生产订单"
    assert action.target_type == "加工工序"


def test_canonical_reconciles_orphan_instance_action() -> None:
    """An action event whose type was never declared gets an auto-created
    ActionType so the schema layer always covers the instance layer."""
    ontology = GlobalOntology.from_entities_and_triples(
        [Entity(id="g_order", name="生产订单"), Entity(id="g_proc", name="加工工序")],
        triples=[],
        actions=[],  # no action types declared at all
        instances=[
            OntologyInstance(id="i_order", name="订单A", class_id="g_order"),
            OntologyInstance(id="i_proc", name="工序A", class_id="g_proc"),
        ],
        action_invocations=[
            ActionInvocation(
                id="inv1",
                action_name="触发返工流程",
                input_instance_ids=["i_order"],
                output_instance_ids=["i_proc"],
            )
        ],
    )
    export = build_canonical_ontology(
        ontology, schema_id="s", scenario="x", sub_scenario="y"
    )
    names = {a.name for a in export.schema_def.action_types}
    assert "触发返工流程" in names
    action = next(a for a in export.schema_def.action_types if a.name == "触发返工流程")
    assert action.actor_type == "生产订单"
    assert action.target_type == "加工工序"
