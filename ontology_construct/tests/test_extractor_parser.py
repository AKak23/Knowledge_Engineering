import pytest

from ontology_construct.extractor import OntologyParseError, parse_local_ontology


def test_parse_local_ontology_accepts_fenced_json_and_infers_literal() -> None:
    raw = """
    ```json
    {
      "entities": [
        {"id": "e1", "name": "苹果公司", "type": "Organization", "aliases": ["Apple"]},
        {"id": "e2", "name": "史蒂夫乔布斯", "entity_type": "Person"}
      ],
      "triples": [
        {"subject": "e1", "predicate": "创始人", "object": "e2", "confidence": 0.95},
        {"subject": "e1", "predicate": "成立年份", "object": 1976, "object_type": "literal"}
      ]
    }
    ```
    """

    ontology = parse_local_ontology(raw, "chunk_0001")

    assert ontology.chunk_id == "chunk_0001"
    assert ontology.entities[0].entity_type == "Organization"
    assert ontology.entities[0].source_chunks == ["chunk_0001"]
    assert ontology.triples[0].object_kind == "entity"
    assert ontology.triples[0].object_id == "e2"
    assert ontology.triples[1].object_kind == "literal"
    assert ontology.triples[1].object_value == 1976


def test_parse_local_ontology_extracts_primary_key_and_cardinality() -> None:
    raw = """
    {
      "entities": [
        {"id": "e1", "name": "用户", "primary_key": "用户ID"},
        {"id": "e2", "name": "订单", "primaryKey": "订单编号"}
      ],
      "triples": [
        {"subject": "e1", "predicate": "拥有", "object": "e2", "object_type": "entity", "cardinality": "1:N"}
      ]
    }
    """

    ontology = parse_local_ontology(raw, "chunk_0001")

    assert ontology.entities[0].primary_key == "用户ID"
    assert ontology.entities[1].primary_key == "订单编号"
    assert ontology.triples[0].cardinality == "ONE_TO_MANY"


def test_parse_local_ontology_defaults_missing_primary_key_and_cardinality() -> None:
    raw = """
    {
      "entities": [{"id": "e1", "name": "用户"}, {"id": "e2", "name": "订单"}],
      "triples": [
        {"subject": "e1", "predicate": "拥有", "object": "e2", "object_type": "entity"}
      ]
    }
    """

    ontology = parse_local_ontology(raw, "chunk_0001")

    assert ontology.entities[0].primary_key is None
    assert ontology.triples[0].cardinality is None


def test_parse_local_ontology_rejects_invalid_json() -> None:
    with pytest.raises(OntologyParseError, match="invalid JSON response"):
        parse_local_ontology("not json", "chunk_0001")


def test_parse_local_ontology_skips_invalid_triple_payload() -> None:
    raw = """
    {
      "entities": [{"id": "e1", "name": "苹果公司"}],
      "triples": [
        {"subject": "e1", "predicate": "关系", "object_type": "entity"},
        {"subject": "未知实体", "predicate": "成立年份", "object": 1976, "object_type": "literal"},
        {"subject": "e1", "predicate": "成立年份", "object": 1976, "object_type": "literal"}
      ]
    }
    """

    ontology = parse_local_ontology(raw, "chunk_0001")

    # The first two triples are dropped (entity-typed without target, then
    # unresolved subject). The third valid literal triple survives.
    assert len(ontology.triples) == 1
    assert ontology.triples[0].subject_id == "e1"
    assert ontology.triples[0].object_value == 1976


def test_parse_local_ontology_extracts_json_from_surrounding_text_and_repairs_commas() -> None:
    raw = """
    以下是抽取结果：
    {
      "entities": [
        {"id": "e1", "name": "苹果公司", "aliases": "Apple"},
      ],
      "triples": [
        {"subject": "苹果公司", "predicate": "成立年份", "object": 1976, "type": "attribute"},
      ],
    }
    以上。
    """

    ontology = parse_local_ontology(raw, "chunk_0001")

    assert ontology.entities[0].aliases == ["Apple"]
    assert ontology.triples[0].subject_id == "e1"
    assert ontology.triples[0].object_kind == "literal"
    assert ontology.triples[0].object_value == 1976


def test_parse_local_ontology_accepts_relations_and_entity_names() -> None:
    raw = """
    {
      "nodes": [
        {"id": "e1", "name": "苹果公司", "type": "Organization"},
        {"id": "e2", "name": "史蒂夫乔布斯", "type": "Person"}
      ],
      "relations": [
        {"subject": "苹果公司", "predicate": "创始人", "object": "史蒂夫乔布斯", "object_type": "relation"}
      ]
    }
    """

    ontology = parse_local_ontology(raw, "chunk_0001")

    assert ontology.triples[0].subject_id == "e1"
    assert ontology.triples[0].object_kind == "entity"
    assert ontology.triples[0].object_id == "e2"


def test_parse_local_ontology_accepts_actions_and_instances() -> None:
    raw = """
    {
      "entities": [
        {"id": "e1", "name": "用户", "type": "BusinessObject"},
        {"id": "e2", "name": "订单", "type": "BusinessObject"}
      ],
      "triples": [],
      "actions": [
        {"id": "a1", "name": "创建订单", "inputs": ["用户"], "outputs": ["订单"]}
      ],
      "instances": [
        {
          "id": "i1",
          "name": "张三",
          "class": "用户",
          "attribute_values": [{"name": "等级", "value": "VIP"}],
          "link_values": [{"predicate": "拥有", "target": "i2"}]
        },
        {
          "id": "i2",
          "name": "订单A",
          "class": "订单",
          "attributes": {"金额": 100}
        }
      ],
      "action_invocations": [
        {"id": "ai1", "action": "创建订单", "inputs": ["张三"], "outputs": ["i2"]}
      ]
    }
    """

    ontology = parse_local_ontology(raw, "chunk_0001")

    assert ontology.actions[0].input_entity_ids == ["e1"]
    assert ontology.actions[0].output_entity_ids == ["e2"]
    assert {instance.name: instance.class_id for instance in ontology.instances} == {
        "张三": "e1",
        "订单A": "e2",
    }
    zhangsan = next(item for item in ontology.instances if item.name == "张三")
    assert zhangsan.attribute_values[0].attribute_name == "等级"
    assert zhangsan.attribute_values[0].value == "VIP"
    assert zhangsan.link_values[0].predicate == "拥有"
    assert zhangsan.link_values[0].target_instance_id == "i2"

    order = next(item for item in ontology.instances if item.name == "订单A")
    assert order.attribute_values[0].attribute_name == "金额"
    assert order.attribute_values[0].value == 100
    assert order.attribute_values[0].data_type == "integer"

    assert ontology.action_invocations[0].action_name == "创建订单"
    assert ontology.action_invocations[0].input_instance_ids == ["i1"]
    assert ontology.action_invocations[0].output_instance_ids == ["i2"]


def test_parse_local_ontology_extracts_structured_rules_and_triggered_actions() -> None:
    raw = """
    {
      "entities": [{"id": "e1", "name": "设备"}],
      "triples": [],
      "rules": [
        {
          "id": "r1",
          "name": "高温冷却规则",
          "scope": "设备",
          "logic": "and",
          "conditions": [
            {"id": "c1", "field": "温度", "operator": "≥", "value": 30, "value_type": "number"}
          ],
          "confidence": 0.96
        }
      ],
      "actions": [
        {
          "id": "a1",
          "name": "启动水冷",
          "inputs": ["设备"],
          "targets": ["e1"],
          "trigger_rules": ["高温冷却规则"],
          "action_type": "control",
          "executor": "plc.start_water_cooling",
          "action_plan": "启动水冷"
        }
      ]
    }
    """

    ontology = parse_local_ontology(raw, "chunk_0001")

    assert len(ontology.rules) == 1
    rule = ontology.rules[0]
    assert rule.scope_entity_id == "e1"
    assert rule.logic == "AND"
    assert rule.conditions[0].operator == ">="
    assert rule.conditions[0].value == 30
    action = ontology.actions[0]
    assert action.trigger_rule_ids == ["r1"]
    assert action.target_entity_ids == ["e1"]
    assert action.executor == "plc.start_water_cooling"


def test_parse_local_ontology_skips_name_index_on_collision() -> None:
    # Two instances share the name "张三" but belong to different classes.
    # The third instance references "张三" by name in its link_values — without
    # a deterministic resolution, the link must be dropped instead of silently
    # pointing at the wrong instance.
    raw = """
    {
      "entities": [
        {"id": "e1", "name": "人物"},
        {"id": "e2", "name": "用户"},
        {"id": "e3", "name": "订单"}
      ],
      "triples": [],
      "instances": [
        {"id": "i1", "name": "张三", "class": "人物"},
        {"id": "i2", "name": "张三", "class": "用户"},
        {
          "id": "i3",
          "name": "PO-001",
          "class": "订单",
          "link_values": [
            {"predicate": "属于", "target": "张三"},
            {"predicate": "下单人", "target": "i1"}
          ]
        }
      ]
    }
    """

    ontology = parse_local_ontology(raw, "chunk_0001")

    po = next(item for item in ontology.instances if item.id == "i3")
    targets = [link.target_instance_id for link in po.link_values]
    # Name lookup is intentionally ambiguous, so only the id-based link survives.
    assert "i1" in targets
    assert "i2" not in targets
    # The colliding name does not silently resolve to the second instance.
    assert all(link.predicate != "属于" for link in po.link_values)


def test_parse_local_ontology_keeps_invocation_with_unmatched_action() -> None:
    raw = """
    {
      "entities": [
        {"id": "e1", "name": "用户"},
        {"id": "e2", "name": "订单"}
      ],
      "triples": [],
      "actions": [
        {"id": "a1", "name": "创建订单", "inputs": ["用户"], "outputs": ["订单"]}
      ],
      "instances": [
        {"id": "i1", "name": "张三", "class": "用户"},
        {"id": "i2", "name": "PO-001", "class": "订单"}
      ],
      "action_invocations": [
        {"id": "ai1", "action": "  创建订单 ", "inputs": ["i1"], "outputs": ["i2"]},
        {"id": "ai2", "action": "未知行动", "inputs": ["i1"], "outputs": ["i2"]}
      ]
    }
    """

    ontology = parse_local_ontology(raw, "chunk_0001")

    invocations = {invocation.action_name for invocation in ontology.action_invocations}
    # The fuzzy variant resolves back to the canonical action name.
    assert "创建订单" in invocations
    # Unrecognised action names are kept verbatim instead of being dropped.
    assert "未知行动" in invocations


def test_parse_local_ontology_strips_whitespace_around_predicates_and_action_names() -> None:
    raw = """
    {
      "entities": [
        {"id": "e1", "name": "用户"},
        {"id": "e2", "name": "订单"}
      ],
      "triples": [],
      "actions": [
        {"id": "a1", "name": "  创建订单  ", "inputs": ["用户"], "outputs": ["订单"]}
      ],
      "instances": [
        {
          "id": "i1",
          "name": "张三",
          "class": "用户",
          "link_values": [{"predicate": "  下单 ", "target": "i2"}]
        },
        {"id": "i2", "name": "PO-001", "class": "订单"}
      ]
    }
    """

    ontology = parse_local_ontology(raw, "chunk_0001")

    assert ontology.actions[0].name == "创建订单"
    zhangsan = next(item for item in ontology.instances if item.name == "张三")
    assert zhangsan.link_values[0].predicate == "下单"


def test_parse_local_ontology_logs_dropped_items(caplog) -> None:
    raw = """
    {
      "entities": [{"id": "e1", "name": "苹果公司"}],
      "triples": [
        {"subject": "missing_subject", "predicate": "成立年份", "object": 1976, "object_type": "literal"}
      ],
      "instances": [
        {"id": "i1", "name": "孤儿"}
      ]
    }
    """

    with caplog.at_level("WARNING", logger="ontology_construct.extractor.parser"):
        ontology = parse_local_ontology(raw, "chunk_0001")

    assert ontology.triples == []
    assert ontology.instances == []
    warnings = [
        record.message for record in caplog.records if record.levelname == "WARNING"
    ]
    assert any("chunk_0001" in message for message in warnings)
    assert any("triples=1" in message and "instances=1" in message for message in warnings)


def test_parse_local_ontology_skips_instance_without_resolvable_class() -> None:
    raw = """
    {
      "entities": [{"id": "e1", "name": "用户"}],
      "triples": [],
      "instances": [
        {"id": "i1", "name": "孤儿", "class": "未知类"},
        {"id": "i2", "name": "张三", "class": "用户"}
      ]
    }
    """

    ontology = parse_local_ontology(raw, "chunk_0001")

    assert [instance.name for instance in ontology.instances] == ["张三"]


def test_parse_local_ontology_rejects_non_list_sections() -> None:
    raw = '{"entities": {"id": "e1", "name": "苹果公司"}, "triples": []}'

    with pytest.raises(OntologyParseError, match="entities must be a list"):
        parse_local_ontology(raw, "chunk_0001")
