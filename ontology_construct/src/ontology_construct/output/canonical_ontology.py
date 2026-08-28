"""Convert an extracted GlobalOntology into the Palantir-aligned canonical
schema (type layer) + graph (instance layer), the format consumed directly by
cot_generation / ontology_generation. See ../../../../ontology_schema_spec.md.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

from ontology_construct.models import GlobalOntology, OntologyInstance


# --- Canonical models (lenient, mirror the cross-module contract) ----------


class PropertyDefinition(BaseModel):
    name: str
    type: str = "string"
    required: bool = False
    enum_values: list[str] | None = None
    description: str = ""


class ObjectType(BaseModel):
    name: str
    display_name: str = ""
    primary_key: str
    title_property: str = ""
    domain: str = ""
    parent_type: str | None = None
    properties: list[PropertyDefinition] = Field(default_factory=list)
    description: str = ""


class LinkType(BaseModel):
    name: str
    source_type: str
    target_type: str
    cardinality: str = "MANY_TO_MANY"
    source_to_target: str = ""
    target_to_source: str = ""
    description: str = ""


class ActionType(BaseModel):
    name: str
    actor_type: str
    target_type: str
    parameters: list[PropertyDefinition] = Field(default_factory=list)
    pre_conditions: list[dict[str, Any]] = Field(default_factory=list)
    effects: list[dict[str, Any]] = Field(default_factory=list)
    description: str = ""


class CanonicalSchema(BaseModel):
    schema_id: str
    scenario: str
    sub_scenario: str
    version: str = "1.0"
    object_types: list[ObjectType] = Field(default_factory=list)
    link_types: list[LinkType] = Field(default_factory=list)
    action_types: list[ActionType] = Field(default_factory=list)
    type_hierarchy: list[dict[str, str]] = Field(default_factory=list)


class ObjectInstance(BaseModel):
    instance_id: str
    object_type: str
    attributes: dict[str, Any] = Field(default_factory=dict)
    description: str = ""


class LinkInstance(BaseModel):
    link_id: str
    source_id: str
    target_id: str
    link_type: str


class ActionEvent(BaseModel):
    event_id: str
    action_type: str
    actor_id: str
    target_id: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    before_state: dict[str, Any] = Field(default_factory=dict)
    after_state: dict[str, Any] = Field(default_factory=dict)
    state_changes: list[dict[str, Any]] = Field(default_factory=list)


class CanonicalGraph(BaseModel):
    graph_id: str
    schema_id: str
    scenario: str = ""
    sub_scenario: str = ""
    objects: list[ObjectInstance] = Field(default_factory=list)
    links: list[LinkInstance] = Field(default_factory=list)
    actions: list[ActionEvent] = Field(default_factory=list)
    stats: dict[str, Any] = Field(default_factory=dict)


class CanonicalExport(BaseModel):
    schema_def: CanonicalSchema
    graph: CanonicalGraph


class ConstraintOptions(BaseModel):
    """Thresholds for deterministically deriving Palantir-style constraints
    (required / enum_values on properties, effects on actions) from instance
    data. Defaults are conservative so synthetic / sparse data stays untouched."""

    enabled: bool = True
    # A type needs at least this many instances before any constraint is inferred.
    min_instances: int = 5
    # A property present (non-null) in >= this fraction of a type's instances is
    # marked required.
    required_coverage: float = 0.95
    # A string property is treated as an enum when its distinct value count is in
    # [min_enum_values, max_enum_values] and values actually repeat (distinct <=
    # present * max_enum_distinct_ratio).
    min_enum_values: int = 2
    max_enum_values: int = 12
    max_enum_distinct_ratio: float = 0.5
    # When true, an action gets a "modifies" effect on its target type's
    # status-like property (if one exists). No effect is fabricated otherwise.
    derive_effects: bool = True
    status_keywords: list[str] = Field(
        default_factory=lambda: ["状态", "status", "state", "lifecycle", "结果", "阶段"]
    )


@dataclass
class _ConstraintSignals:
    count_by_class: dict[str, int] = field(default_factory=dict)
    present_by_class: dict[str, dict[str, int]] = field(default_factory=dict)
    values_by_class: dict[str, dict[str, list[Any]]] = field(default_factory=dict)


# --- Helpers ---------------------------------------------------------------


_DESCRIPTION_KEYS = {"说明", "description", "desc"}
_PRIMARY_KEY_EXACT = {"id", "ID", "主键", "编号", "编码", "代码", "code"}
_PRIMARY_KEY_SUFFIXES = ("_id", "编号", "编码", "id", "code")
_TITLE_KEYS = ("名称", "name", "title", "名字")


def _canonical_data_type(data_type: str | None, value: Any = None) -> str:
    if data_type == "number":
        return "float"
    if data_type in {"string", "integer", "float", "boolean", "date", "timestamp"}:
        return data_type
    return _infer_data_type(value)


def _infer_data_type(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "float"
    return "string"


def _infer_primary_key(property_names: list[str]) -> str | None:
    for name in property_names:
        if name in _PRIMARY_KEY_EXACT:
            return name
    for name in property_names:
        lowered = name.lower()
        if any(lowered.endswith(suffix.lower()) for suffix in _PRIMARY_KEY_SUFFIXES):
            return name
    return property_names[0] if property_names else None


def _infer_title_property(property_names: list[str], primary_key: str) -> str:
    for key in _TITLE_KEYS:
        if key in property_names:
            return key
    return primary_key


def _cardinality(max_out: int, max_in: int) -> str:
    out_many = max_out > 1
    in_many = max_in > 1
    if not out_many and not in_many:
        return "ONE_TO_ONE"
    if out_many and not in_many:
        return "ONE_TO_MANY"
    if not out_many and in_many:
        return "MANY_TO_ONE"
    return "MANY_TO_MANY"


def _slug(value: str, fallback: str) -> str:
    normalized = re.sub(r"[^0-9A-Za-z_]+", "_", value.strip().lower()).strip("_")
    return normalized[:64] if normalized else fallback


# --- Conversion ------------------------------------------------------------


def build_canonical_ontology(
    ontology: GlobalOntology,
    *,
    schema_id: str,
    scenario: str,
    sub_scenario: str,
    constraints: ConstraintOptions | None = None,
) -> CanonicalExport:
    options = constraints or ConstraintOptions()
    entities = list(ontology.entities.values())
    name_by_entity_id = {entity.id: entity.name for entity in entities}

    object_types = _build_object_types(ontology, entities)
    link_name_by_pair, link_types = _build_link_types(ontology, name_by_entity_id)
    action_types = _build_action_types(ontology, name_by_entity_id)

    objects, type_by_instance_id = _build_objects(ontology, name_by_entity_id)
    links = _build_links(ontology, type_by_instance_id, link_name_by_pair)
    actions = _build_actions(ontology, type_by_instance_id)
    # Every instance action must resolve to a schema action type (schema ⊇
    # instance actions). Backfill any action event whose type was not declared so
    # the type and instance layers stay consistent.
    action_types = _reconcile_action_types(action_types, actions, type_by_instance_id)

    # Deterministically derive constraints (required / enum_values / effects) from
    # instance data as a post-pass so it does not perturb type/link/action shape.
    if options.enabled:
        _apply_constraints(object_types, action_types, ontology, options)

    schema_def = CanonicalSchema(
        schema_id=schema_id,
        scenario=scenario,
        sub_scenario=sub_scenario,
        object_types=object_types,
        link_types=link_types,
        action_types=action_types,
    )

    graph = CanonicalGraph(
        graph_id=f"{schema_id}_g1",
        schema_id=schema_id,
        scenario=scenario,
        sub_scenario=sub_scenario,
        objects=objects,
        links=links,
        actions=actions,
        stats={"objects": len(objects), "links": len(links), "actions": len(actions)},
    )
    return CanonicalExport(schema_def=schema_def, graph=graph)


def _literal_predicates_by_subject(ontology: GlobalOntology) -> dict[str, list[tuple[str, Any]]]:
    result: dict[str, list[tuple[str, Any]]] = {}
    for triple in ontology.triples:
        if triple.object_kind == "literal":
            result.setdefault(triple.subject_id, []).append((triple.predicate, triple.object_value))
    return result


def _instance_signals_by_class(
    instances: list[OntologyInstance],
) -> dict[str, list[tuple[str, str | None, Any]]]:
    signals: dict[str, list[tuple[str, str | None, Any]]] = {}
    for instance in instances:
        for attribute in instance.attribute_values:
            signals.setdefault(instance.class_id, []).append(
                (attribute.attribute_name, attribute.data_type, attribute.value)
            )
    return signals


# --- Constraints (Palantir's 5th element) ----------------------------------


def _constraint_signals(instances: list[OntologyInstance]) -> _ConstraintSignals:
    signals = _ConstraintSignals()
    for instance in instances:
        signals.count_by_class[instance.class_id] = (
            signals.count_by_class.get(instance.class_id, 0) + 1
        )
        present = signals.present_by_class.setdefault(instance.class_id, {})
        values = signals.values_by_class.setdefault(instance.class_id, {})
        seen_in_instance: set[str] = set()
        for attribute in instance.attribute_values:
            if attribute.value is None:
                continue
            name = attribute.attribute_name
            values.setdefault(name, []).append(attribute.value)
            if name in seen_in_instance:
                continue
            seen_in_instance.add(name)
            present[name] = present.get(name, 0) + 1
    return signals


def _apply_constraints(
    object_types: list[ObjectType],
    action_types: list[ActionType],
    ontology: GlobalOntology,
    options: ConstraintOptions,
) -> None:
    signals = _constraint_signals(ontology.instances)
    id_by_name: dict[str, str] = {}
    for entity in ontology.entities.values():
        id_by_name.setdefault(entity.name, entity.id)

    for object_type in object_types:
        entity_id = id_by_name.get(object_type.name)
        for prop in object_type.properties:
            if prop.name == object_type.primary_key:
                prop.required = True  # a primary key is required by definition.
            if entity_id is not None:
                _apply_property_constraints(prop, entity_id, options, signals)

    if options.derive_effects:
        props_by_type = {ot.name: ot.properties for ot in object_types}
        for action_type in action_types:
            if not action_type.effects:
                action_type.effects = _action_effects(
                    action_type.target_type, props_by_type, options
                )


def _apply_property_constraints(
    prop: PropertyDefinition,
    entity_id: str,
    options: ConstraintOptions,
    signals: _ConstraintSignals,
) -> None:
    total = signals.count_by_class.get(entity_id, 0)
    if total < options.min_instances:
        return
    present = signals.present_by_class.get(entity_id, {}).get(prop.name, 0)
    if present > 0 and present >= options.required_coverage * total:
        prop.required = True
    if prop.type == "string":
        raw = signals.values_by_class.get(entity_id, {}).get(prop.name, [])
        if len(raw) >= options.min_instances:
            distinct = list(dict.fromkeys(str(value) for value in raw))
            if (
                options.min_enum_values <= len(distinct) <= options.max_enum_values
                and len(distinct) <= len(raw) * options.max_enum_distinct_ratio
            ):
                prop.enum_values = sorted(distinct)


def _status_property(
    props: list[PropertyDefinition],
    options: ConstraintOptions,
) -> str | None:
    for prop in props:
        lowered = prop.name.lower()
        if any(keyword in lowered for keyword in options.status_keywords):
            return prop.name
    return None


def _action_effects(
    target_type: str,
    props_by_type: dict[str, list[PropertyDefinition]],
    options: ConstraintOptions,
) -> list[dict[str, Any]]:
    if not target_type:
        return []
    status_prop = _status_property(props_by_type.get(target_type, []), options)
    if not status_prop:
        return []
    return [{"type": "modifies", "object_type": target_type, "property": status_prop}]


def _build_object_types(ontology: GlobalOntology, entities: list) -> list[ObjectType]:
    literal_attrs = _literal_predicates_by_subject(ontology)
    instance_signals = _instance_signals_by_class(ontology.instances)

    object_types: list[ObjectType] = []
    for entity in entities:
        seen: dict[str, PropertyDefinition] = {}

        def add(name: str, data_type: str, value: Any = None) -> None:
            if name in _DESCRIPTION_KEYS or name in seen:
                return
            seen[name] = PropertyDefinition(
                name=name, type=_canonical_data_type(data_type, value)
            )

        for name, value in entity.attributes.items():
            add(str(name), _infer_data_type(value), value)
        for name, value in literal_attrs.get(entity.id, []):
            add(name, _infer_data_type(value), value)
        for name, data_type, value in instance_signals.get(entity.id, []):
            add(name, data_type or _infer_data_type(value), value)

        property_names = list(seen.keys())
        # Prefer the LLM-suggested primary key when it names a real property;
        # otherwise fall back to the heuristic.
        primary_key = entity.primary_key if entity.primary_key in seen else None
        if primary_key is None and entity.primary_key:
            # Hint names a key that was not surfaced as a property; add it so the
            # schema stays self-consistent.
            seen = {
                entity.primary_key: PropertyDefinition(name=entity.primary_key, type="string"),
                **seen,
            }
            primary_key = entity.primary_key
            property_names = list(seen.keys())
        if primary_key is None:
            primary_key = _infer_primary_key(property_names)
        if primary_key is None:
            primary_key = "id"
            seen = {"id": PropertyDefinition(name="id", type="string"), **seen}
            property_names = list(seen.keys())

        object_types.append(
            ObjectType(
                name=entity.name,
                primary_key=primary_key,
                title_property=_infer_title_property(property_names, primary_key),
                domain=entity.entity_type or "",
                properties=list(seen.values()),
                description=str(entity.attributes.get("说明") or entity.entity_type or ""),
            )
        )
    return object_types


def _build_link_types(
    ontology: GlobalOntology,
    name_by_entity_id: dict[str, str],
) -> tuple[dict[tuple[str, str, str], str], list[LinkType]]:
    # Collect (source_type, predicate, target_type) triples and resolve a unique
    # link_type name for each. A predicate maps to its bare name when it occurs
    # for a single type pair; otherwise it is disambiguated with the type pair.
    pairs: list[tuple[str, str, str]] = []
    seen_pairs: set[tuple[str, str, str]] = set()
    predicate_pair_count: dict[str, int] = {}
    cardinality_hint_by_pair: dict[tuple[str, str, str], str] = {}
    for triple in ontology.triples:
        if triple.object_kind != "entity" or not triple.object_id:
            continue
        source = name_by_entity_id.get(triple.subject_id)
        target = name_by_entity_id.get(triple.object_id)
        if source is None or target is None:
            continue
        key = (source, triple.predicate, target)
        if triple.cardinality and key not in cardinality_hint_by_pair:
            cardinality_hint_by_pair[key] = triple.cardinality
        if key in seen_pairs:
            continue
        seen_pairs.add(key)
        pairs.append(key)
        predicate_pair_count[triple.predicate] = predicate_pair_count.get(triple.predicate, 0) + 1

    name_by_pair: dict[tuple[str, str, str], str] = {}
    used_names: set[str] = set()
    for source, predicate, target in pairs:
        base = predicate if predicate_pair_count[predicate] == 1 else f"{predicate}_{source}_{target}"
        candidate = base
        suffix = 2
        while candidate in used_names:
            candidate = f"{base}_{suffix}"
            suffix += 1
        used_names.add(candidate)
        name_by_pair[(source, predicate, target)] = candidate

    # LLM-supplied cardinality (the schema designer's intent) wins; otherwise
    # fall back to what the instance data empirically shows; otherwise default.
    inferred = _infer_link_cardinalities(ontology, name_by_entity_id, name_by_pair)

    link_types = []
    for source, predicate, target in pairs:
        link_name = name_by_pair[(source, predicate, target)]
        cardinality = (
            cardinality_hint_by_pair.get((source, predicate, target))
            or inferred.get(link_name)
            or "MANY_TO_MANY"
        )
        link_types.append(
            LinkType(
                name=link_name,
                source_type=source,
                target_type=target,
                cardinality=cardinality,
                source_to_target=predicate,
                description=predicate,
            )
        )
    return name_by_pair, link_types


def _infer_link_cardinalities(
    ontology: GlobalOntology,
    name_by_entity_id: dict[str, str],
    name_by_pair: dict[tuple[str, str, str], str],
) -> dict[str, str]:
    class_by_instance = {inst.id: inst.class_id for inst in ontology.instances}
    type_by_instance = {
        inst.id: name_by_entity_id.get(inst.class_id) for inst in ontology.instances
    }
    out_targets: dict[str, dict[str, set[str]]] = {}
    in_sources: dict[str, dict[str, set[str]]] = {}
    for instance in ontology.instances:
        source_type = type_by_instance.get(instance.id)
        if source_type is None:
            continue
        for link in instance.link_values:
            target_type = type_by_instance.get(link.target_instance_id)
            if target_type is None:
                continue
            link_name = name_by_pair.get((source_type, link.predicate, target_type))
            if link_name is None:
                continue
            out_targets.setdefault(link_name, {}).setdefault(instance.id, set()).add(
                link.target_instance_id
            )
            in_sources.setdefault(link_name, {}).setdefault(link.target_instance_id, set()).add(
                instance.id
            )

    cardinalities: dict[str, str] = {}
    for link_name in set(out_targets) | set(in_sources):
        max_out = max((len(t) for t in out_targets.get(link_name, {}).values()), default=0)
        max_in = max((len(s) for s in in_sources.get(link_name, {}).values()), default=0)
        cardinalities[link_name] = _cardinality(max_out, max_in)
    return cardinalities


def _build_action_types(
    ontology: GlobalOntology,
    name_by_entity_id: dict[str, str],
) -> list[ActionType]:
    # When an action declares no actor/target entity types (e.g. fallback or
    # event-derived actions), recover them from the object types of the instances
    # its invocations actually touch, so the schema layer is not an empty shell.
    actor_by_action, target_by_action = _action_participant_types(
        ontology, name_by_entity_id
    )
    action_types: list[ActionType] = []
    seen: set[str] = set()
    for action in ontology.actions:
        if action.name in seen:
            continue
        seen.add(action.name)
        actor_type = next(
            (name_by_entity_id[eid] for eid in action.input_entity_ids if eid in name_by_entity_id),
            "",
        ) or actor_by_action.get(action.name, "")
        target_type = (
            next(
                (name_by_entity_id[eid] for eid in action.output_entity_ids if eid in name_by_entity_id),
                "",
            )
            or target_by_action.get(action.name, "")
            or actor_type
        )
        action_types.append(
            ActionType(
                name=action.name,
                actor_type=actor_type,
                target_type=target_type,
                description=action.description,
            )
        )
    return action_types


def _action_participant_types(
    ontology: GlobalOntology,
    name_by_entity_id: dict[str, str],
) -> tuple[dict[str, str], dict[str, str]]:
    type_by_instance = {
        instance.id: name_by_entity_id.get(instance.class_id)
        for instance in ontology.instances
    }
    actor_by_action: dict[str, str] = {}
    target_by_action: dict[str, str] = {}
    for invocation in ontology.action_invocations:
        if invocation.action_name not in actor_by_action:
            actor_type = next(
                (
                    type_by_instance[iid]
                    for iid in invocation.input_instance_ids
                    if type_by_instance.get(iid)
                ),
                None,
            )
            if actor_type:
                actor_by_action[invocation.action_name] = actor_type
        if invocation.action_name not in target_by_action:
            target_type = next(
                (
                    type_by_instance[iid]
                    for iid in invocation.output_instance_ids
                    if type_by_instance.get(iid)
                ),
                None,
            )
            if target_type:
                target_by_action[invocation.action_name] = target_type
    return actor_by_action, target_by_action


def _reconcile_action_types(
    action_types: list[ActionType],
    actions: list[ActionEvent],
    type_by_instance_id: dict[str, str],
) -> list[ActionType]:
    known = {action_type.name for action_type in action_types}
    added: dict[str, ActionType] = {}
    for event in actions:
        if event.action_type in known or event.action_type in added:
            continue
        added[event.action_type] = ActionType(
            name=event.action_type,
            actor_type=type_by_instance_id.get(event.actor_id, ""),
            target_type=type_by_instance_id.get(event.target_id, ""),
            description="",
        )
    return [*action_types, *added.values()]


def _build_objects(
    ontology: GlobalOntology,
    name_by_entity_id: dict[str, str],
) -> tuple[list[ObjectInstance], dict[str, str]]:
    objects: list[ObjectInstance] = []
    type_by_instance_id: dict[str, str] = {}
    for instance in ontology.instances:
        object_type = name_by_entity_id.get(instance.class_id)
        if object_type is None:
            continue
        type_by_instance_id[instance.id] = object_type
        objects.append(
            ObjectInstance(
                instance_id=instance.id,
                object_type=object_type,
                attributes={
                    attribute.attribute_name: attribute.value
                    for attribute in instance.attribute_values
                },
                description=instance.name,
            )
        )
    return objects, type_by_instance_id


def _build_links(
    ontology: GlobalOntology,
    type_by_instance_id: dict[str, str],
    name_by_pair: dict[tuple[str, str, str], str],
) -> list[LinkInstance]:
    links: list[LinkInstance] = []
    index = 1
    for instance in ontology.instances:
        source_type = type_by_instance_id.get(instance.id)
        if source_type is None:
            continue
        for link in instance.link_values:
            target_type = type_by_instance_id.get(link.target_instance_id)
            if target_type is None:
                continue
            link_name = name_by_pair.get((source_type, link.predicate, target_type))
            if link_name is None:
                # Instance link without a matching type-level triple; surface the
                # bare predicate so the edge is still represented.
                link_name = link.predicate
            links.append(
                LinkInstance(
                    link_id=f"LNK-{index:08d}",
                    source_id=instance.id,
                    target_id=link.target_instance_id,
                    link_type=link_name,
                )
            )
            index += 1
    return links


def _build_actions(
    ontology: GlobalOntology,
    type_by_instance_id: dict[str, str],
) -> list[ActionEvent]:
    actions: list[ActionEvent] = []
    for invocation in ontology.action_invocations:
        inputs = [iid for iid in invocation.input_instance_ids if iid in type_by_instance_id]
        outputs = [iid for iid in invocation.output_instance_ids if iid in type_by_instance_id]
        actor_id = inputs[0] if inputs else (outputs[0] if outputs else None)
        target_id = outputs[0] if outputs else (inputs[0] if inputs else None)
        if actor_id is None or target_id is None:
            continue
        actions.append(
            ActionEvent(
                event_id=invocation.id,
                action_type=invocation.action_name,
                actor_id=actor_id,
                target_id=target_id,
            )
        )
    return actions
