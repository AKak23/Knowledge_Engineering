import asyncio

import pytest

from ontology_construct.structured_builder import (
    StructuredBuildConfig,
    StructuredColumn,
    StructuredMetadata,
    StructuredRelationMap,
    StructuredTable,
    build_structured_ontology,
    load_structured_build_config,
    polish_structured_schema,
)


class FakeStructuredRepository:
    def __init__(self) -> None:
        self.metadata = StructuredMetadata(
            tables={
                "orders": StructuredTable(
                    table_name="orders",
                    kind="entity",
                    entity_type="order",
                    description="订单",
                ),
                "work_orders": StructuredTable(
                    table_name="work_orders",
                    kind="entity",
                    entity_type="work_order",
                    description="生产工单",
                ),
                "equipment": StructuredTable(
                    table_name="equipment",
                    kind="reference",
                    entity_type="equipment",
                    description="设备",
                ),
                "process_instances": StructuredTable(
                    table_name="process_instances",
                    kind="entity",
                    entity_type="process_instance",
                    description="工序实例",
                ),
                "relations": StructuredTable(
                    table_name="relations",
                    kind="relation",
                    description="显式关系",
                ),
            },
            columns={
                "orders": [
                    StructuredColumn("orders", "id", role="pk"),
                    StructuredColumn("orders", "name_cn", role="label"),
                    StructuredColumn("orders", "quantity", data_type="integer", role="measure"),
                ],
                "work_orders": [
                    StructuredColumn("work_orders", "id", role="pk"),
                    StructuredColumn("work_orders", "name_cn", role="label"),
                    StructuredColumn("work_orders", "order_id", role="fk", ref_table="orders", ref_column="id"),
                ],
                "equipment": [
                    StructuredColumn("equipment", "id", role="pk"),
                    StructuredColumn("equipment", "name_cn", role="label"),
                    StructuredColumn("equipment", "resource_type", role="attribute"),
                ],
                "process_instances": [
                    StructuredColumn("process_instances", "id", role="pk"),
                    StructuredColumn("process_instances", "name_cn", role="label"),
                    StructuredColumn("process_instances", "order_id", role="fk", ref_table="orders", ref_column="id"),
                ],
                "relations": [
                    StructuredColumn("relations", "rel_type", role="attribute"),
                    StructuredColumn("relations", "src_id", role="attribute"),
                    StructuredColumn("relations", "dst_id", role="attribute"),
                ],
            },
            entity_map={
                "orders": "order",
                "work_orders": "work_order",
                "equipment": "equipment",
                "process_instances": "process_instance",
            },
            relation_map=[
                StructuredRelationMap(
                    rel_type="使用设备",
                    src_entity_types=["process_instance"],
                    dst_entity_types=["equipment"],
                    holder_table="relations",
                    src_col="src_id",
                    dst_col="dst_id",
                )
            ],
        )
        self.rows = {
            "orders": [{"id": "ORD-001", "name_cn": "0001号订单", "quantity": 20}],
            "work_orders": [{"id": "WO-001", "name_cn": "0001号工单", "order_id": "ORD-001"}],
            "equipment": [{"id": "EQ-001", "name_cn": "1号设备", "resource_type": "press"}],
            "process_instances": [{"id": "PROC-001", "name_cn": "冲压工序实例", "order_id": "ORD-001"}],
        }
        self.relations = [{"rel_type": "使用设备", "src_id": "PROC-001", "dst_id": "EQ-001"}]

    def load_metadata(self, _config: StructuredBuildConfig) -> StructuredMetadata:
        return self.metadata

    def sample_rows(self, table_name: str, _columns: list[StructuredColumn], _limit: int):
        if table_name == "relations":
            return self.relations
        return self.rows.get(table_name, [])

    def sample_relation_rows(
        self,
        _table_name: str,
        _type_column: str,
        _source_column: str,
        _target_column: str,
        _per_type: int,
    ):
        return self.relations

    def fetch_rows_by_values(
        self,
        table_name: str,
        _columns: list[StructuredColumn],
        key_column: str,
        values: list[str],
    ):
        wanted = {str(value) for value in values}
        return [
            row
            for row in self.rows.get(table_name, [])
            if str(row.get(key_column)) in wanted
        ]


class FakePolishClient:
    async def complete(self, _prompt: str, *, system_prompt: str | None = None) -> str:
        return (
            '{"objects": ['
            '{"id": "orders", "name": "制造订单", "description": "来自订单表的制造订单"},'
            '{"id": "unknown", "name": "未知对象", "description": "应被忽略"}'
            ']}'
        )


class FakeMappingClient:
    async def complete(self, _prompt: str, *, system_prompt: str | None = None) -> str:
        return (
            '{"classes": ['
            '{"class_id": "orders", "name": "制造订单", "description": "订单映射",'
            '"source_tables": ["orders"], "evidence": ["llm"], "confidence": 0.93},'
            '{"class_id": "missing", "name": "非法", "source_tables": ["missing_table"],'
            '"evidence": ["llm"], "confidence": 0.99}'
            '], "relations": ['
            '{"predicate": "生产工单属于订单", "source_class": "work_orders",'
            '"target_class": "orders", "source_table": "work_orders", "target_table": "orders",'
            '"source_columns": ["order_id"], "target_columns": ["id"],'
            '"join": {"type": "llm_verified"}, "evidence": ["llm"], "confidence": 0.9},'
            '{"predicate": "非法关系", "source_class": "work_orders",'
            '"target_class": "missing", "source_table": "work_orders", "target_table": "missing",'
            '"source_columns": ["order_id"], "target_columns": ["id"],'
            '"evidence": ["llm"], "confidence": 0.9}'
            ']}'
        )


class FakeSemanticRelationClient:
    async def complete(self, _prompt: str, *, system_prompt: str | None = None) -> str:
        return (
            '{"relations": ['
            '{"predicate": "关联订单", "source_class": "process_instance",'
            '"target_class": "manufacturing_order", "source_table": "process_instances",'
            '"target_table": "orders", "source_column": "order_id", "target_column": "id",'
            '"join_type": "llm_verified", "confidence": 0.92, "inverse_predicate": "关联工序"},'
            '{"predicate": "非法建议", "source_class": "process_instance",'
            '"target_class": "equipment", "source_table": "process_instances",'
            '"target_table": "equipment", "source_column": "missing", "target_column": "id",'
            '"join_type": "llm_verified", "confidence": 0.95}'
            ']}'
        )


class FakeEmbeddingClient:
    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [[float(index + 1), 1.0] for index, _text in enumerate(texts)]


class SparseSemanticRepository(FakeStructuredRepository):
    def sample_rows(self, table_name: str, columns: list[StructuredColumn], limit: int):
        if table_name == "equipment" and limit == 0:
            return []
        return super().sample_rows(table_name, columns, limit)


def _config() -> StructuredBuildConfig:
    return load_structured_build_config(
        {
            "version": 1,
            "source": {
                "type": "postgresql",
                "connection": {
                    "host": "localhost",
                    "database": "factory",
                    "user": "readonly",
                },
            },
            "ontology": {"name": "结构化测试本体", "profile": "industrial_process"},
        }
    )


def test_structured_config_accepts_legacy_database_top_level() -> None:
    config = load_structured_build_config(
        {
            "database": {
                "type": "postgresql",
                "host": "localhost",
                "database": "factory",
                "user": "readonly",
            }
        }
    )

    assert config.source.type == "postgresql"
    assert config.source.connection.database == "factory"
    assert config.construction.mode == "hybrid"
    assert config.construction.use_llm is True
    assert config.construction.use_embedding is True


def test_structured_config_rejects_plaintext_password() -> None:
    with pytest.raises(ValueError, match="Plaintext database password"):
        load_structured_build_config(
            {
                "source": {
                    "type": "postgresql",
                    "connection": {
                        "host": "localhost",
                        "database": "factory",
                        "user": "readonly",
                        "password": "secret",
                    },
                }
            }
        )


def test_structured_config_rejects_non_postgresql() -> None:
    with pytest.raises(ValueError, match="postgresql"):
        load_structured_build_config(
            {
                "source": {
                    "type": "mysql",
                    "connection": {
                        "host": "localhost",
                        "database": "factory",
                        "user": "readonly",
                    },
                }
            }
        )


def test_structured_builder_creates_schema_instances_and_links() -> None:
    ontology, stats = build_structured_ontology(
        _config(),
        FakeStructuredRepository(),
        embedding_client=FakeEmbeddingClient(),
        llm_client=FakeMappingClient(),
    )

    assert {entity.name for entity in ontology.entities.values()} >= {
        "制造订单",
        "生产工单",
        "设备",
        "工序实例",
    }
    assert any(triple.predicate == "使用设备" for triple in ontology.triples)
    assert any(triple.predicate == "关联订单" for triple in ontology.triples)
    by_name = {instance.name: instance for instance in ontology.instances}
    assert by_name["0001号工单"].link_values[0].target_instance_id == "orders:ORD-001"
    assert any(
        link.predicate == "使用设备"
        and link.target_instance_id == "equipment:EQ-001"
        for link in by_name["冲压工序实例"].link_values
    )
    assert stats.object_count == 4
    assert stats.instance_count == 4
    assert stats.instance_link_count >= 2
    assert stats.profile_table_count == 5
    assert stats.candidate_class_count == 4
    assert stats.candidate_relation_count >= 2
    assert stats.mapped_class_count == 4
    assert stats.mapped_relation_count >= 2
    assert stats.database_profile["tables"]
    assert stats.structured_candidates["classes"]
    assert stats.structured_mapping["classes"]
    assert "missing" not in {entity.id for entity in ontology.entities.values()}
    assert any(triple.predicate == "生产工单属于订单" for triple in ontology.triples)


def test_schema_polish_updates_existing_object_text_only() -> None:
    ontology, _stats = build_structured_ontology(
        _config(),
        FakeStructuredRepository(),
        embedding_client=FakeEmbeddingClient(),
        llm_client=FakeMappingClient(),
    )
    original_triples = list(ontology.triples)
    original_instances = list(ontology.instances)

    polished = asyncio.run(polish_structured_schema(ontology, FakePolishClient()))

    assert polished.entities["orders"].name == "制造订单"
    assert polished.entities["orders"].attributes["说明"] == "来自订单表的制造订单"
    assert "unknown" not in polished.entities
    assert polished.triples == original_triples
    assert polished.instances == original_instances


def _semantic_config() -> StructuredBuildConfig:
    return load_structured_build_config(
        {
            "version": 1,
            "source": {
                "type": "postgresql",
                "connection": {
                    "host": "localhost",
                    "database": "factory",
                    "user": "readonly",
                },
            },
            "construction": {"use_llm": False, "use_embedding": False},
            "semantic_mapping": {
                "sampling": {
                    "default_per_class": 1,
                    "class_limits": {"equipment": 0},
                    "auto_include_related": True,
                },
                "classes": [
                    {
                        "id": "manufacturing_order",
                        "name": "制造订单",
                        "source_tables": ["orders"],
                        "primary_table": "orders",
                        "primary_key": "id",
                        "label_column": "name_cn",
                        "attributes": [
                            {"source_column": "quantity", "name": "数量"},
                        ],
                    },
                    {
                        "id": "work_order",
                        "name": "生产工单",
                        "source_tables": ["work_orders"],
                        "primary_table": "work_orders",
                        "primary_key": "id",
                        "label_column": "name_cn",
                    },
                    {
                        "id": "equipment",
                        "name": "设备",
                        "source_tables": ["equipment"],
                        "primary_table": "equipment",
                        "primary_key": "id",
                        "label_column": "name_cn",
                    },
                    {
                        "id": "process_instance",
                        "name": "工序实例",
                        "source_tables": ["process_instances"],
                        "primary_table": "process_instances",
                        "primary_key": "id",
                        "label_column": "name_cn",
                    },
                ],
                "relations": [
                    {
                        "source_class": "work_order",
                        "predicate": "属于",
                        "target_class": "manufacturing_order",
                        "source_table": "work_orders",
                        "target_table": "orders",
                        "source_column": "order_id",
                        "target_column": "id",
                        "inverse_predicate": "包含工单",
                    }
                ],
                "relation_tables": [
                    {
                        "table": "relations",
                        "source_class": "process_instance",
                        "predicate": "使用",
                        "target_class": "equipment",
                        "source_column": "src_id",
                        "target_column": "dst_id",
                        "inverse_predicate": "被使用于",
                    }
                ],
                "derived_relations": [
                    {
                        "source_class": "work_order",
                        "predicate": "关联工序",
                        "target_class": "process_instance",
                        "source_table": "work_orders",
                        "target_table": "process_instances",
                        "source_column": "order_id",
                        "target_column": "order_id",
                        "confidence": 0.86,
                        "inverse_predicate": "关联工单",
                    }
                ],
            },
        }
    )


def test_semantic_mapping_builds_domain_schema_and_verified_links() -> None:
    ontology, stats = build_structured_ontology(
        _semantic_config(),
        SparseSemanticRepository(),
    )

    assert {entity.name for entity in ontology.entities.values()} >= {
        "制造订单",
        "生产工单",
        "设备",
        "工序实例",
    }
    assert "relations" not in ontology.entities
    assert any(
        triple.subject_id == "process_instance"
        and triple.predicate == "使用"
        and triple.object_id == "equipment"
        for triple in ontology.triples
    )
    assert any(
        triple.subject_id == "equipment"
        and triple.predicate == "被使用于"
        and triple.object_id == "process_instance"
        for triple in ontology.triples
    )
    assert any(
        triple.subject_id == "work_order"
        and triple.predicate == "关联工序"
        and triple.object_id == "process_instance"
        for triple in ontology.triples
    )
    by_id = {instance.id: instance for instance in ontology.instances}
    assert "equipment:EQ-001" in by_id
    process = by_id["process_instance:PROC-001"]
    assert any(
        link.predicate == "使用"
        and link.target_instance_id == "equipment:EQ-001"
        and link.source_chunks == ["structured:semantic:relation_table:relations"]
        for link in process.link_values
    )
    equipment = by_id["equipment:EQ-001"]
    assert any(
        link.predicate == "被使用于"
        and link.target_instance_id == "process_instance:PROC-001"
        and link.source_chunks == ["structured:semantic:inverse:relation_table:relations"]
        for link in equipment.link_values
    )
    work_order = by_id["work_order:WO-001"]
    assert any(
        link.predicate == "属于"
        and link.target_instance_id == "manufacturing_order:ORD-001"
        for link in work_order.link_values
    )
    assert any(
        link.predicate == "关联工序"
        and link.target_instance_id == "process_instance:PROC-001"
        for link in work_order.link_values
    )
    order = by_id["manufacturing_order:ORD-001"]
    assert any(
        link.predicate == "包含工单"
        and link.target_instance_id == "work_order:WO-001"
        for link in order.link_values
    )
    assert any(
        link.predicate == "关联工单"
        and link.target_instance_id == "work_order:WO-001"
        for link in process.link_values
    )
    assert stats.structured_quality["fallbackLinkCount"] == 0
    assert stats.structured_quality["verifiedLinkCount"] >= 6
    assert stats.structured_mapping["mode"] == "semantic_mapping"


def test_semantic_mapping_rejects_unknown_column() -> None:
    payload = _semantic_config().model_dump(mode="json", by_alias=True)
    payload["semantic_mapping"]["classes"][0]["attributes"][0]["source_column"] = "missing"
    with pytest.raises(ValueError, match="unknown column"):
        build_structured_ontology(
            load_structured_build_config(payload),
            SparseSemanticRepository(),
        )


def test_semantic_llm_relation_suggestions_are_validated() -> None:
    payload = _semantic_config().model_dump(mode="json", by_alias=True)
    payload["construction"]["use_llm"] = True
    payload["llm"] = {
        "enabled": True,
        "usage": {
            "schema_mapping": True,
            "relation_mapping": True,
            "schema_polish": True,
            "instance_generation": False,
        },
    }

    ontology, stats = build_structured_ontology(
        load_structured_build_config(payload),
        SparseSemanticRepository(),
        llm_client=FakeSemanticRelationClient(),
    )

    assert any(
        triple.subject_id == "process_instance"
        and triple.predicate == "关联订单"
        and triple.object_id == "manufacturing_order"
        for triple in ontology.triples
    )
    assert not any(triple.predicate == "非法建议" for triple in ontology.triples)
    process = {instance.id: instance for instance in ontology.instances}[
        "process_instance:PROC-001"
    ]
    assert any(
        link.predicate == "关联订单"
        and link.target_instance_id == "manufacturing_order:ORD-001"
        for link in process.link_values
    )
    assert any(
        relation["predicate"] == "关联订单"
        for relation in stats.structured_mapping["relations"]
    )
