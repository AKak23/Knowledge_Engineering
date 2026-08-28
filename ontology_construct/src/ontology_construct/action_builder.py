"""Derive a Palantir-style action layer from event-like instances.

In schema-first builds the concept pipeline never extracts actions and the
instance builder clears ``action_invocations``; the only actions that survive
are a couple synthesised by the connectivity-repair fallback. Yet the corpus is
rich in actions that were modelled as *event objects* (e.g. 生产事件, 工序启动事件).

This module runs after instance build + connectivity repair, when instance ids
are stable, and turns those event instances into:

* ``OntologyAction`` (type layer) — one per derived action name, with
  ``input_entity_ids`` / ``output_entity_ids`` filled so the canonical export can
  resolve ``actor_type`` / ``target_type`` instead of leaving them empty.
* ``ActionInvocation`` (instance layer) — one per event, with
  ``input_instance_ids`` / ``output_instance_ids`` pointing at the concrete
  actor / target instances.

It is deterministic, additive (event objects are left in place) and dedupes both
layers so the type↔instance binding stays consistent.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import NAMESPACE_URL, uuid5

from ontology_construct.config import ActionBuilderConfig
from ontology_construct.models import (
    ActionInvocation,
    GlobalOntology,
    OntologyAction,
    OntologyInstance,
)


@dataclass(frozen=True)
class ActionBuildStats:
    enabled: bool
    source_event_instances: int = 0
    actions_added: int = 0
    events_added: int = 0
    skipped_reason: str = ""

    def as_dict(self) -> dict[str, int | str | bool]:
        return {
            "enabled": self.enabled,
            "sourceEventInstances": self.source_event_instances,
            "actionsAdded": self.actions_added,
            "eventsAdded": self.events_added,
            "skippedReason": self.skipped_reason,
        }


def build_actions_from_instances(
    ontology: GlobalOntology,
    config: ActionBuilderConfig,
) -> tuple[GlobalOntology, ActionBuildStats]:
    """Return a copy of ``ontology`` with event-derived actions appended."""
    if not config.enabled:
        return ontology, ActionBuildStats(enabled=False, skipped_reason="disabled")

    type_name_by_class = {
        entity_id: entity.name for entity_id, entity in ontology.entities.items()
    }
    instance_ids = {instance.id for instance in ontology.instances}
    class_by_instance = {
        instance.id: instance.class_id for instance in ontology.instances
    }

    def type_name_of(instance_id: str) -> str:
        return type_name_by_class.get(class_by_instance.get(instance_id, ""), "")

    event_instances = [
        instance
        for instance in ontology.instances
        if _is_event_type(type_name_by_class.get(instance.class_id, ""), config)
    ]
    if not event_instances:
        return ontology, ActionBuildStats(
            enabled=True, skipped_reason="no_event_instances"
        )

    action_by_name: dict[str, OntologyAction] = {
        action.name: action for action in ontology.actions
    }
    existing_invocation_ids = {
        invocation.id for invocation in ontology.action_invocations
    }
    new_actions: list[OntologyAction] = []
    new_invocations: list[ActionInvocation] = []
    actions_added = 0
    events_added = 0

    for event in event_instances:
        action_name = _action_name(
            type_name_by_class.get(event.class_id, ""), config
        )
        if not action_name:
            continue
        actor_id, target_id = _resolve_actor_target(
            event, instance_ids, type_name_of, config
        )
        if actor_id is None or target_id is None or actor_id == target_id:
            continue
        actor_class = class_by_instance.get(actor_id)
        target_class = class_by_instance.get(target_id)

        action = action_by_name.get(action_name)
        if action is None:
            action = OntologyAction(
                id=_stable_id("action", action_name),
                name=action_name,
                description=f"由事件实例派生的动作：{action_name}",
                input_entity_ids=[actor_class] if actor_class else [],
                output_entity_ids=[target_class] if target_class else [],
                source_chunks=[config.source_tag],
            )
            action_by_name[action_name] = action
            new_actions.append(action)
            actions_added += 1
        else:
            if actor_class and actor_class not in action.input_entity_ids:
                action.input_entity_ids.append(actor_class)
            if target_class and target_class not in action.output_entity_ids:
                action.output_entity_ids.append(target_class)

        invocation_id = _stable_id(
            "action_invocation", action_name, actor_id, target_id
        )
        if invocation_id in existing_invocation_ids:
            continue
        existing_invocation_ids.add(invocation_id)
        new_invocations.append(
            ActionInvocation(
                id=invocation_id,
                action_name=action_name,
                input_instance_ids=[actor_id],
                output_instance_ids=[target_id],
                attributes={"说明": f"{event.name}"} if event.name else {},
                source_chunks=[config.source_tag, *event.source_chunks[:1]],
            )
        )
        events_added += 1

    if not new_actions and not new_invocations:
        return ontology, ActionBuildStats(
            enabled=True,
            source_event_instances=len(event_instances),
            skipped_reason="no_actions_derived",
        )

    updated = ontology.model_copy(
        update={
            "actions": [*ontology.actions, *new_actions],
            "action_invocations": [*ontology.action_invocations, *new_invocations],
        }
    )
    return updated, ActionBuildStats(
        enabled=True,
        source_event_instances=len(event_instances),
        actions_added=actions_added,
        events_added=events_added,
    )


def _is_event_type(type_name: str, config: ActionBuilderConfig) -> bool:
    return any(keyword in type_name for keyword in config.event_type_keywords)


def _action_name(type_name: str, config: ActionBuilderConfig) -> str:
    name = type_name.strip()
    for suffix in config.name_strip_suffixes:
        if suffix and name.endswith(suffix) and len(name) > len(suffix):
            name = name[: -len(suffix)]
            break
    name = name.strip()
    return name or type_name.strip()


def _resolve_actor_target(
    event: OntologyInstance,
    instance_ids: set[str],
    type_name_of,
    config: ActionBuilderConfig,
) -> tuple[str | None, str | None]:
    """Pick (actor_id, target_id) from an event instance's out-links.

    Priority: directional predicates -> object-type keyword -> first participant.
    A single-participant event falls back to using the event instance itself as
    the actor so the action still has two distinct endpoints.
    """
    participants = [
        link.target_instance_id
        for link in event.link_values
        if link.target_instance_id in instance_ids
        and link.target_instance_id != event.id
    ]
    if not participants:
        return None, None

    actor_id = _first_by_predicate(event, instance_ids, config.actor_predicates)
    target_id = _first_by_predicate(event, instance_ids, config.target_predicates)

    if target_id is None:
        target_id = _first_by_type(participants, type_name_of, config.target_type_keywords)
    if target_id is None:
        target_id = participants[0]

    if actor_id is None:
        actor_id = next((pid for pid in participants if pid != target_id), None)
    if actor_id is None:
        # Single distinct participant: treat the event occurrence as the actor.
        actor_id = event.id
    return actor_id, target_id


def _first_by_predicate(
    event: OntologyInstance,
    instance_ids: set[str],
    predicates: list[str],
) -> str | None:
    for link in event.link_values:
        if link.predicate in predicates and link.target_instance_id in instance_ids:
            return link.target_instance_id
    return None


def _first_by_type(
    participants: list[str],
    type_name_of,
    keywords: list[str],
) -> str | None:
    for instance_id in participants:
        type_name = type_name_of(instance_id)
        if any(keyword in type_name for keyword in keywords):
            return instance_id
    return None


def _stable_id(prefix: str, *parts: str) -> str:
    raw = "::".join(parts)
    return f"{prefix}_{uuid5(NAMESPACE_URL, raw).hex[:12]}"
