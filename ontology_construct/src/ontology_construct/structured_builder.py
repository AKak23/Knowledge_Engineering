from __future__ import annotations

import asyncio
import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from pydantic import BaseModel, Field, field_validator, model_validator

from ontology_construct.config import load_project_env
from ontology_construct.models import (
    Entity,
    GlobalOntology,
    InstanceAttributeValue,
    InstanceLinkValue,
    OntologyInstance,
    Triple,
)


_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_UNSUPPORTED_TABLES = {"entities", "relations"}
_SCHEMA_KINDS = {"entity", "reference", "quality"}
_EVENT_TABLE_NAMES = {"process_events"}
_LABEL_FALLBACKS = ("name_cn", "name", "title")


class StructuredConnectionConfig(BaseModel):
    host: str
    port: int = Field(default=5432, gt=0)
    database: str
    user: str | None = None
    user_env: str | None = None
    password_env: str | None = None
    schema_name: str = Field(default="public", alias="schema")

    @model_validator(mode="after")
    def require_user(self) -> "StructuredConnectionConfig":
        if not (self.user or self.user_env):
            raise ValueError("source.connection must include user or user_env")
        return self


class StructuredSourceConfig(BaseModel):
    type: str
    connection: StructuredConnectionConfig

    @field_validator("type")
    @classmethod
    def postgresql_only(cls, value: str) -> str:
        if value != "postgresql":
            raise ValueError("Only postgresql structured sources are supported")
        return value


class StructuredMetadataTablesConfig(BaseModel):
    meta_tables: str = "meta.meta_tables"
    meta_columns: str = "meta.meta_columns"
    entity_map: str = "meta.meta_entity_map"
    relation_map: str = "meta.meta_relation_map"

    @field_validator("meta_tables", "meta_columns", "entity_map", "relation_map")
    @classmethod
    def safe_table_ref(cls, value: str) -> str:
        _quote_qualified_identifier(value)
        return value


class StructuredMetadataFallbackConfig(BaseModel):
    use_information_schema: bool = True
    use_pg_comments: bool = True
    infer_fk_from_constraints: bool = True


class StructuredMetadataConfig(BaseModel):
    tables: StructuredMetadataTablesConfig = Field(default_factory=StructuredMetadataTablesConfig)
    fallback: StructuredMetadataFallbackConfig = Field(default_factory=StructuredMetadataFallbackConfig)


class StructuredOntologyConfig(BaseModel):
    name: str = "结构化数据本体"
    profile: str = "structured"


class StructuredConstructionConfig(BaseModel):
    mode: str = "hybrid"
    use_meta: bool = True
    use_information_schema: bool = True
    use_pg_comments: bool = True
    use_constraints: bool = True
    use_sampling: bool = True
    use_embedding: bool = True
    use_llm: bool = True
    class_merge_similarity: float = Field(default=0.88, ge=0, le=1)
    relation_candidate_similarity: float = Field(default=0.82, ge=0, le=1)
    value_overlap_threshold: float = Field(default=0.6, ge=0, le=1)
    min_relation_confidence: float = Field(default=0.7, ge=0, le=1)

    @field_validator("mode")
    @classmethod
    def hybrid_only(cls, value: str) -> str:
        if value != "hybrid":
            raise ValueError("Only hybrid structured construction mode is supported")
        return value


class StructuredSampleConfig(BaseModel):
    per_entity_table: int = Field(default=20, ge=0)
    per_reference_table: int = Field(default=50, ge=0)
    per_quality_table: int = Field(default=20, ge=0)


class StructuredRelationSampleConfig(BaseModel):
    per_relation_type: int = Field(default=100, ge=0)


class StructuredEventSampleConfig(BaseModel):
    per_event_type: int = Field(default=50, ge=0)


class StructuredInstancesConfig(BaseModel):
    sample: StructuredSampleConfig = Field(default_factory=StructuredSampleConfig)
    relation_sample: StructuredRelationSampleConfig = Field(default_factory=StructuredRelationSampleConfig)
    event_sample: StructuredEventSampleConfig = Field(default_factory=StructuredEventSampleConfig)


class StructuredLLMUsageConfig(BaseModel):
    schema_mapping: bool = True
    relation_mapping: bool = True
    schema_polish: bool = True
    instance_generation: bool = False


class StructuredLLMConfig(BaseModel):
    enabled: bool = True
    usage: StructuredLLMUsageConfig = Field(default_factory=StructuredLLMUsageConfig)


class StructuredSemanticAttributeConfig(BaseModel):
    source_table: str = ""
    source_column: str
    name: str = ""
    data_type: str = ""
    description: str = ""


class StructuredSemanticClassConfig(BaseModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = ""
    source_tables: list[str] = Field(default_factory=list)
    primary_table: str
    primary_key: str = ""
    label_column: str = ""
    attributes: list[StructuredSemanticAttributeConfig] = Field(default_factory=list)
    materialize: bool = True


class StructuredSemanticRelationConfig(BaseModel):
    source_class: str
    predicate: str = Field(min_length=1)
    target_class: str
    source_table: str
    target_table: str
    source_column: str
    target_column: str
    join_type: str = "fk"
    confidence: float = Field(default=1.0, ge=0, le=1)
    inverse_predicate: str = ""


class StructuredSemanticRelationTableConfig(BaseModel):
    table: str
    source_class: str
    predicate: str = Field(min_length=1)
    target_class: str
    source_column: str
    target_column: str
    materialize_as_class: bool = False
    confidence: float = Field(default=1.0, ge=0, le=1)
    inverse_predicate: str = ""


class StructuredSemanticDerivedRelationConfig(BaseModel):
    source_class: str
    predicate: str = Field(min_length=1)
    target_class: str
    source_table: str
    target_table: str
    source_column: str
    target_column: str
    via_table: str = ""
    via_source_column: str = ""
    via_target_column: str = ""
    confidence: float = Field(default=0.8, ge=0, le=1)
    inverse_predicate: str = ""


class StructuredSemanticEventTableConfig(BaseModel):
    table: str
    class_id: str


class StructuredSemanticSamplingConfig(BaseModel):
    default_per_class: int | None = Field(default=None, ge=0)
    class_limits: dict[str, int] = Field(default_factory=dict)
    table_limits: dict[str, int] = Field(default_factory=dict)
    auto_include_related: bool = True

    @field_validator("class_limits", "table_limits")
    @classmethod
    def non_negative_limits(cls, value: dict[str, int]) -> dict[str, int]:
        invalid = [key for key, limit in value.items() if limit < 0]
        if invalid:
            raise ValueError(f"sampling limits must be non-negative: {', '.join(invalid)}")
        return value


class StructuredSemanticMappingConfig(BaseModel):
    classes: list[StructuredSemanticClassConfig] = Field(default_factory=list)
    relations: list[StructuredSemanticRelationConfig] = Field(default_factory=list)
    relation_tables: list[StructuredSemanticRelationTableConfig] = Field(default_factory=list)
    derived_relations: list[StructuredSemanticDerivedRelationConfig] = Field(default_factory=list)
    event_tables: list[StructuredSemanticEventTableConfig] = Field(default_factory=list)
    sampling: StructuredSemanticSamplingConfig = Field(
        default_factory=StructuredSemanticSamplingConfig
    )

    @model_validator(mode="after")
    def require_classes(self) -> "StructuredSemanticMappingConfig":
        if not self.classes:
            raise ValueError("semantic_mapping.classes must include at least one class")
        return self


class StructuredBuildConfig(BaseModel):
    version: int = 1
    source: StructuredSourceConfig
    construction: StructuredConstructionConfig = Field(default_factory=StructuredConstructionConfig)
    metadata: StructuredMetadataConfig = Field(default_factory=StructuredMetadataConfig)
    ontology: StructuredOntologyConfig = Field(default_factory=StructuredOntologyConfig)
    instances: StructuredInstancesConfig = Field(default_factory=StructuredInstancesConfig)
    llm: StructuredLLMConfig = Field(default_factory=StructuredLLMConfig)
    semantic_mapping: StructuredSemanticMappingConfig | None = None

    @field_validator("version")
    @classmethod
    def supported_version(cls, value: int) -> int:
        if value != 1:
            raise ValueError("Only structured YAML version 1 is supported")
        return value


@dataclass
class StructuredTable:
    table_name: str
    kind: str = "entity"
    entity_type: str = ""
    row_count: int = 0
    description: str = ""


@dataclass
class StructuredColumn:
    table_name: str
    column_name: str
    data_type: str = "text"
    is_nullable: bool = True
    role: str = "attribute"
    ref_table: str = ""
    ref_column: str = ""
    description: str = ""


@dataclass
class StructuredRelationMap:
    rel_type: str
    src_entity_types: list[str] = field(default_factory=list)
    dst_entity_types: list[str] = field(default_factory=list)
    holder_table: str = "relations"
    src_col: str = "src_id"
    dst_col: str = "dst_id"
    tags: str = ""


@dataclass
class StructuredMetadata:
    tables: dict[str, StructuredTable] = field(default_factory=dict)
    columns: dict[str, list[StructuredColumn]] = field(default_factory=dict)
    entity_map: dict[str, str] = field(default_factory=dict)
    relation_map: list[StructuredRelationMap] = field(default_factory=list)


@dataclass
class StructuredBuildStats:
    object_count: int = 0
    schema_relation_count: int = 0
    instance_count: int = 0
    instance_link_count: int = 0
    profile_table_count: int = 0
    candidate_class_count: int = 0
    candidate_relation_count: int = 0
    mapped_class_count: int = 0
    mapped_relation_count: int = 0
    database_profile: dict[str, Any] = field(default_factory=dict)
    structured_candidates: dict[str, Any] = field(default_factory=dict)
    structured_mapping: dict[str, Any] = field(default_factory=dict)
    structured_quality: dict[str, Any] = field(default_factory=dict)


class StructuredRepository(Protocol):
    def load_metadata(self, config: StructuredBuildConfig) -> StructuredMetadata:
        ...

    def sample_rows(
        self,
        table_name: str,
        columns: list[StructuredColumn],
        limit: int,
    ) -> list[dict[str, Any]]:
        ...

    def sample_relation_rows(
        self,
        table_name: str,
        type_column: str,
        source_column: str,
        target_column: str,
        per_type: int,
    ) -> list[dict[str, Any]]:
        ...

    def fetch_rows_by_values(
        self,
        table_name: str,
        columns: list[StructuredColumn],
        key_column: str,
        values: list[Any],
    ) -> list[dict[str, Any]]:
        ...


class SchemaPolishClient(Protocol):
    async def complete(self, prompt: str, *, system_prompt: str | None = None) -> str:
        ...


class StructuredEmbeddingClient(Protocol):
    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        ...


class PostgresStructuredRepository:
    def __init__(self, config: StructuredBuildConfig):
        self.config = config

    def load_metadata(self, config: StructuredBuildConfig) -> StructuredMetadata:
        meta = StructuredMetadata()
        try:
            meta.tables = {
                row["table_name"]: StructuredTable(
                    table_name=row["table_name"],
                    kind=row.get("kind") or "entity",
                    entity_type=row.get("entity_type") or "",
                    row_count=int(row.get("row_count") or 0),
                    description=row.get("description") or "",
                )
                for row in self._fetch_table(config.metadata.tables.meta_tables)
            }
            for row in self._fetch_table(config.metadata.tables.meta_columns):
                column = StructuredColumn(
                    table_name=row["table_name"],
                    column_name=row["column_name"],
                    data_type=row.get("data_type") or "text",
                    is_nullable=bool(row.get("is_nullable", True)),
                    role=row.get("role") or "attribute",
                    ref_table=row.get("ref_table") or "",
                    ref_column=row.get("ref_column") or "",
                    description=row.get("description") or "",
                )
                meta.columns.setdefault(column.table_name, []).append(column)
            meta.entity_map = {
                row["table_name"]: row["ontology_entity_type"]
                for row in self._fetch_table(config.metadata.tables.entity_map)
                if row.get("ontology_entity_type")
            }
            meta.relation_map = [
                StructuredRelationMap(
                    rel_type=row["rel_type"],
                    src_entity_types=_split_csv(row.get("src_entity_types")),
                    dst_entity_types=_split_csv(row.get("dst_entity_types")),
                    holder_table=row.get("holder_table") or "relations",
                    src_col=row.get("src_col") or "src_id",
                    dst_col=row.get("dst_col") or "dst_id",
                    tags=row.get("tags") or "",
                )
                for row in self._fetch_table(config.metadata.tables.relation_map)
                if row.get("rel_type")
            ]
        except Exception:
            if not config.metadata.fallback.use_information_schema:
                raise
            meta = self._fallback_metadata(config.source.connection.schema_name)
        return meta

    def sample_rows(
        self,
        table_name: str,
        columns: list[StructuredColumn],
        limit: int,
    ) -> list[dict[str, Any]]:
        if limit <= 0:
            return []
        select_cols = ", ".join(_quote_identifier(column.column_name) for column in columns)
        order_column = _primary_key(columns) or _label_column(columns) or columns[0].column_name
        if table_name in _EVENT_TABLE_NAMES and any(
            column.column_name == "event_type" for column in columns
        ):
            sql = (
                "select * from ("
                f"select {select_cols}, row_number() over "
                f"(partition by event_type order by {_quote_identifier(order_column)}) as rn "
                f"from {_quote_table_in_schema(table_name, self.config)}"
                ") ranked where rn <= %s"
            )
            return self._fetch_sql(sql, (limit,))
        sql = (
            f"select {select_cols} from {_quote_table_in_schema(table_name, self.config)} "
            f"order by {_quote_identifier(order_column)} limit %s"
        )
        return self._fetch_sql(sql, (limit,))

    def sample_relation_rows(
        self,
        table_name: str,
        type_column: str,
        source_column: str,
        target_column: str,
        per_type: int,
    ) -> list[dict[str, Any]]:
        if per_type <= 0:
            return []
        table = _quote_table_in_schema(table_name, self.config)
        type_col = _quote_identifier(type_column)
        source_col = _quote_identifier(source_column)
        target_col = _quote_identifier(target_column)
        sql = (
            "select * from ("
            f"select {type_col} as rel_type, {source_col} as src_id, {target_col} as dst_id, "
            f"row_number() over (partition by {type_col} order by {type_col}, {source_col}, {target_col}) as rn "
            f"from {table}"
            ") ranked where rn <= %s"
        )
        return self._fetch_sql(sql, (per_type,))

    def fetch_rows_by_values(
        self,
        table_name: str,
        columns: list[StructuredColumn],
        key_column: str,
        values: list[Any],
    ) -> list[dict[str, Any]]:
        unique_values = list(dict.fromkeys(str(value) for value in values if value is not None))
        if not unique_values:
            return []
        select_cols = ", ".join(_quote_identifier(column.column_name) for column in columns)
        placeholders = ", ".join(["%s"] * len(unique_values))
        sql = (
            f"select {select_cols} from {_quote_table_in_schema(table_name, self.config)} "
            f"where {_quote_identifier(key_column)} in ({placeholders})"
        )
        return self._fetch_sql(sql, tuple(unique_values))

    def _fetch_table(self, table_ref: str) -> list[dict[str, Any]]:
        return self._fetch_sql(f"select * from {_quote_qualified_identifier(table_ref)}")

    def _fetch_sql(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:  # pragma: no cover - depends on runtime install.
            raise RuntimeError(
                "psycopg is required for structured PostgreSQL builds"
            ) from exc
        load_project_env()
        conn = self.config.source.connection
        user = conn.user or os.environ.get(conn.user_env or "")
        password = os.environ.get(conn.password_env or "") if conn.password_env else None
        if conn.user_env and not user:
            raise RuntimeError(f"Missing PostgreSQL user env: {conn.user_env}")
        if conn.password_env and password is None:
            raise RuntimeError(f"Missing PostgreSQL password env: {conn.password_env}")
        with psycopg.connect(
            host=conn.host,
            port=conn.port,
            dbname=conn.database,
            user=user,
            password=password,
            row_factory=dict_row,
        ) as pg_conn:
            pg_conn.execute("set transaction read only")
            with pg_conn.cursor() as cur:
                cur.execute(sql, params)
                return [dict(row) for row in cur.fetchall()]

    def _fallback_metadata(self, schema_name: str) -> StructuredMetadata:
        tables = self._fetch_sql(
            """
            select table_name
            from information_schema.tables
            where table_schema = %s and table_type = 'BASE TABLE'
            order by table_name
            """,
            (schema_name,),
        )
        columns = self._fetch_sql(
            """
            select c.table_name, c.column_name, c.data_type, c.is_nullable,
                   case
                     when tc.constraint_type = 'PRIMARY KEY' then 'pk'
                     when c.column_name in ('name_cn', 'name', 'title') then 'label'
                     else 'attribute'
                   end as role
            from information_schema.columns c
            left join information_schema.key_column_usage kcu
              on c.table_schema = kcu.table_schema
             and c.table_name = kcu.table_name
             and c.column_name = kcu.column_name
            left join information_schema.table_constraints tc
              on kcu.constraint_schema = tc.constraint_schema
             and kcu.constraint_name = tc.constraint_name
            where c.table_schema = %s
            order by c.table_name, c.ordinal_position
            """,
            (schema_name,),
        )
        foreign_keys = self._fetch_sql(
            """
            select kcu.table_name, kcu.column_name,
                   ccu.table_name as ref_table, ccu.column_name as ref_column
            from information_schema.table_constraints tc
            join information_schema.key_column_usage kcu
              on tc.constraint_schema = kcu.constraint_schema
             and tc.constraint_name = kcu.constraint_name
            join information_schema.constraint_column_usage ccu
              on ccu.constraint_schema = tc.constraint_schema
             and ccu.constraint_name = tc.constraint_name
            where tc.table_schema = %s and tc.constraint_type = 'FOREIGN KEY'
            """,
            (schema_name,),
        )
        fk_by_column = {
            (row["table_name"], row["column_name"]): row
            for row in foreign_keys
        }
        meta = StructuredMetadata()
        for row in tables:
            table_name = row["table_name"]
            meta.tables[table_name] = StructuredTable(
                table_name=table_name,
                kind="entity",
                entity_type=table_name,
                description=table_name,
            )
        for row in columns:
            table_name = row["table_name"]
            fk = fk_by_column.get((table_name, row["column_name"]))
            column = StructuredColumn(
                table_name=table_name,
                column_name=row["column_name"],
                data_type=row.get("data_type") or "text",
                is_nullable=row.get("is_nullable") == "YES",
                role="fk" if fk else (row.get("role") or "attribute"),
                ref_table=(fk or {}).get("ref_table") or "",
                ref_column=(fk or {}).get("ref_column") or "",
            )
            meta.columns.setdefault(table_name, []).append(column)
        return meta


def load_structured_build_config(payload: dict[str, Any]) -> StructuredBuildConfig:
    _reject_plaintext_password(payload)
    if "queries" in payload:
        raise ValueError("queries are not supported by structured build v1")
    normalized = dict(payload)
    if "source" not in normalized and "database" in normalized:
        database = dict(normalized["database"] or {})
        source_type = database.pop("type", None)
        normalized["source"] = {"type": source_type, "connection": database}
    return StructuredBuildConfig.model_validate(normalized)


def build_structured_ontology(
    config: StructuredBuildConfig,
    repository: StructuredRepository | None = None,
    embedding_client: StructuredEmbeddingClient | None = None,
    llm_client: SchemaPolishClient | None = None,
    stage_callback: Callable[[str], None] | None = None,
) -> tuple[GlobalOntology, StructuredBuildStats]:
    def update_stage(stage: str) -> None:
        if stage_callback is not None:
            stage_callback(stage)

    repo = repository or PostgresStructuredRepository(config)
    update_stage("读取数据库元信息")
    metadata = repo.load_metadata(config)
    if config.semantic_mapping is not None:
        _validate_semantic_mapping(config.semantic_mapping, metadata)
        update_stage("读取语义映射采样数据")
        rows_by_table = _sample_semantic_rows(config, metadata, repo)
        update_stage("生成数据库画像")
        database_profile = _build_database_profile(metadata, rows_by_table)
        update_stage("生成语义本体")
        return _build_semantic_ontology(
            config=config,
            metadata=metadata,
            repository=repo,
            rows_by_table=rows_by_table,
            database_profile=database_profile,
            llm_client=llm_client,
        )

    class_tables = _class_tables(metadata)
    rows_by_table: dict[str, list[dict[str, Any]]] = {}
    instance_by_raw_id: dict[str, str] = {}
    instance_by_table_pk: dict[tuple[str, str], str] = {}
    instances: list[OntologyInstance] = []

    for table in class_tables:
        columns = metadata.columns.get(table.table_name, [])
        if not columns:
            continue
        rows = (
            repo.sample_rows(table.table_name, columns, _sample_limit(config, table))
            if config.construction.use_sampling
            else []
        )
        rows_by_table[table.table_name] = rows

    update_stage("生成数据库画像")
    database_profile = _build_database_profile(metadata, rows_by_table)
    update_stage("生成结构化候选")
    structured_candidates = _build_structured_candidates(metadata, database_profile)
    if config.construction.use_embedding:
        if embedding_client is None:
            raise RuntimeError(
                "Missing embedding client for hybrid structured construction"
            )
        update_stage("embedding 语义召回")
        _apply_embedding_evidence(config, structured_candidates, embedding_client)
    update_stage("LLM 生成结构化 mapping")
    structured_mapping = _build_structured_mapping(
        config,
        metadata,
        structured_candidates,
        llm_client,
    )
    entities = _build_entities_from_mapping(metadata, structured_mapping)
    triples = _build_schema_triples_from_mapping(config, structured_mapping)

    mapped_table_names = {
        table_name
        for item in structured_mapping.get("classes", [])
        for table_name in item.get("source_tables", [])
    }
    class_tables = [
        table for table in class_tables if table.table_name in mapped_table_names
    ]

    for table in class_tables:
        columns = metadata.columns.get(table.table_name, [])
        rows = rows_by_table.get(table.table_name, [])
        for row in rows:
            instance = _row_to_instance(table, columns, row)
            if instance is None:
                continue
            instances.append(instance)
            pk_value = instance.id.split(":", 1)[1]
            instance_by_table_pk[(table.table_name, pk_value)] = instance.id
            instance_by_raw_id.setdefault(pk_value, instance.id)

    instance_by_id = {instance.id: instance for instance in instances}
    _add_fk_instance_links(metadata, rows_by_table, instance_by_table_pk, instance_by_id)
    _add_explicit_relation_links(config, metadata, repo, instance_by_raw_id, instance_by_id)
    _add_event_links(metadata, rows_by_table, instance_by_raw_id, instance_by_id)
    ontology = GlobalOntology.from_entities_and_triples(
        entities,
        triples,
        instances=instances,
    )
    stats = StructuredBuildStats(
        object_count=len(entities),
        schema_relation_count=len(triples),
        instance_count=len(instances),
        instance_link_count=sum(len(instance.link_values) for instance in instances),
        profile_table_count=len(database_profile.get("tables", [])),
        candidate_class_count=len(structured_candidates.get("classes", [])),
        candidate_relation_count=len(structured_candidates.get("relations", [])),
        mapped_class_count=len(structured_mapping.get("classes", [])),
        mapped_relation_count=len(structured_mapping.get("relations", [])),
        database_profile=database_profile,
        structured_candidates=structured_candidates,
        structured_mapping=structured_mapping,
        structured_quality=_structured_quality(ontology),
    )
    return ontology, stats


def _validate_semantic_mapping(
    mapping: StructuredSemanticMappingConfig,
    metadata: StructuredMetadata,
) -> None:
    tables = set(metadata.tables)
    columns = {
        table_name: {column.column_name for column in table_columns}
        for table_name, table_columns in metadata.columns.items()
    }
    classes = {item.id for item in mapping.classes}
    if len(classes) != len(mapping.classes):
        raise ValueError("semantic_mapping.classes contains duplicate class ids")

    def require_table(table_name: str, context: str) -> None:
        if table_name not in tables:
            raise ValueError(f"{context} references unknown table: {table_name}")

    def require_column(table_name: str, column_name: str, context: str) -> None:
        require_table(table_name, context)
        if column_name and column_name not in columns.get(table_name, set()):
            raise ValueError(
                f"{context} references unknown column: {table_name}.{column_name}"
            )

    for item in mapping.classes:
        require_table(item.primary_table, f"semantic_mapping.classes[{item.id}]")
        source_tables = item.source_tables or [item.primary_table]
        for table_name in source_tables:
            require_table(table_name, f"semantic_mapping.classes[{item.id}].source_tables")
        primary_key = item.primary_key or _primary_key(metadata.columns.get(item.primary_table, []))
        if not primary_key:
            raise ValueError(f"semantic class {item.id} must define a primary_key")
        require_column(item.primary_table, primary_key, f"semantic class {item.id}")
        if item.label_column:
            require_column(item.primary_table, item.label_column, f"semantic class {item.id}")
        for attribute in item.attributes:
            table_name = attribute.source_table or item.primary_table
            require_column(table_name, attribute.source_column, f"semantic class {item.id}")

    for item in mapping.relations:
        if item.source_class not in classes:
            raise ValueError(f"semantic relation references unknown source_class: {item.source_class}")
        if item.target_class not in classes:
            raise ValueError(f"semantic relation references unknown target_class: {item.target_class}")
        require_column(item.source_table, item.source_column, "semantic_mapping.relations")
        require_column(item.target_table, item.target_column, "semantic_mapping.relations")

    for item in mapping.relation_tables:
        if item.source_class not in classes:
            raise ValueError(f"semantic relation_table references unknown source_class: {item.source_class}")
        if item.target_class not in classes:
            raise ValueError(f"semantic relation_table references unknown target_class: {item.target_class}")
        require_column(item.table, item.source_column, "semantic_mapping.relation_tables")
        require_column(item.table, item.target_column, "semantic_mapping.relation_tables")

    for item in mapping.derived_relations:
        if item.source_class not in classes:
            raise ValueError(f"semantic derived_relation references unknown source_class: {item.source_class}")
        if item.target_class not in classes:
            raise ValueError(f"semantic derived_relation references unknown target_class: {item.target_class}")
        require_column(item.source_table, item.source_column, "semantic_mapping.derived_relations")
        require_column(item.target_table, item.target_column, "semantic_mapping.derived_relations")
        if item.via_table:
            require_column(item.via_table, item.via_source_column, "semantic_mapping.derived_relations")
            require_column(item.via_table, item.via_target_column, "semantic_mapping.derived_relations")

    for item in mapping.event_tables:
        if item.class_id not in classes:
            raise ValueError(f"semantic event_table references unknown class_id: {item.class_id}")
        require_table(item.table, "semantic_mapping.event_tables")


def _sample_semantic_rows(
    config: StructuredBuildConfig,
    metadata: StructuredMetadata,
    repository: StructuredRepository,
) -> dict[str, list[dict[str, Any]]]:
    mapping = config.semantic_mapping
    if mapping is None:
        return {}
    tables: dict[str, int] = {}
    for class_config in mapping.classes:
        if not class_config.materialize:
            continue
        tables[class_config.primary_table] = max(
            tables.get(class_config.primary_table, 0),
            _semantic_sample_limit(config, class_config),
        )
    for relation in mapping.relations:
        tables.setdefault(relation.source_table, _semantic_table_limit(config, relation.source_table))
    for relation_table in mapping.relation_tables:
        tables.setdefault(relation_table.table, _semantic_table_limit(config, relation_table.table))
    for relation in mapping.derived_relations:
        tables.setdefault(relation.source_table, _semantic_table_limit(config, relation.source_table))
        if relation.via_table:
            tables.setdefault(relation.via_table, _semantic_table_limit(config, relation.via_table))
    rows_by_table: dict[str, list[dict[str, Any]]] = {}
    for table_name, limit in tables.items():
        columns = metadata.columns.get(table_name, [])
        if not columns:
            continue
        rows_by_table[table_name] = (
            repository.sample_rows(table_name, columns, limit)
            if config.construction.use_sampling
            else []
        )
    return rows_by_table


def _build_semantic_ontology(
    *,
    config: StructuredBuildConfig,
    metadata: StructuredMetadata,
    repository: StructuredRepository,
    rows_by_table: dict[str, list[dict[str, Any]]],
    database_profile: dict[str, Any],
    llm_client: SchemaPolishClient | None,
) -> tuple[GlobalOntology, StructuredBuildStats]:
    mapping = config.semantic_mapping
    if mapping is None:
        raise ValueError("semantic_mapping is required for semantic ontology build")

    effective_mapping = _semantic_mapping_with_llm_relations(
        config,
        metadata,
        mapping,
        database_profile,
        llm_client,
    )
    class_by_id = {item.id: item for item in mapping.classes}
    entities = _build_semantic_entities(metadata, effective_mapping)
    triples = _build_semantic_triples(effective_mapping)
    instances, instance_lookup = _build_semantic_instances(
        config,
        metadata,
        effective_mapping,
        rows_by_table,
    )
    instance_by_id = {instance.id: instance for instance in instances}
    _add_semantic_relation_links(
        config,
        metadata,
        effective_mapping,
        repository,
        rows_by_table,
        class_by_id,
        instance_lookup,
        instance_by_id,
    )
    _add_semantic_derived_links(
        config,
        metadata,
        effective_mapping,
        repository,
        rows_by_table,
        class_by_id,
        instance_lookup,
        instance_by_id,
    )
    instances = list(instance_by_id.values())
    ontology = GlobalOntology.from_entities_and_triples(
        entities,
        triples,
        instances=instances,
    )
    quality = _structured_quality(ontology)
    stats = StructuredBuildStats(
        object_count=len(entities),
        schema_relation_count=len(triples),
        instance_count=len(instances),
        instance_link_count=sum(len(instance.link_values) for instance in instances),
        profile_table_count=len(database_profile.get("tables", [])),
        candidate_class_count=len(mapping.classes),
        candidate_relation_count=(
            len(mapping.relations)
            + len(mapping.relation_tables)
            + len(mapping.derived_relations)
        ),
        mapped_class_count=len(entities),
        mapped_relation_count=len(triples),
        database_profile=database_profile,
        structured_candidates=_semantic_candidates_artifact(mapping),
        structured_mapping=_semantic_mapping_artifact(effective_mapping),
        structured_quality=quality,
    )
    return ontology, stats


def _semantic_mapping_with_llm_relations(
    config: StructuredBuildConfig,
    metadata: StructuredMetadata,
    mapping: StructuredSemanticMappingConfig,
    database_profile: dict[str, Any],
    llm_client: SchemaPolishClient | None,
) -> StructuredSemanticMappingConfig:
    if (
        not config.construction.use_llm
        or not config.llm.enabled
        or not config.llm.usage.relation_mapping
    ):
        return mapping
    if llm_client is None:
        raise RuntimeError("Missing LLM client for semantic structured construction")
    raw_relations = _request_semantic_llm_relations(mapping, database_profile, llm_client)
    if not raw_relations:
        return mapping
    accepted = _valid_semantic_llm_relations(config, metadata, mapping, raw_relations)
    if not accepted:
        return mapping
    effective_mapping = mapping.model_copy(deep=True)
    effective_mapping.relations.extend(accepted)
    return effective_mapping


def _request_semantic_llm_relations(
    mapping: StructuredSemanticMappingConfig,
    database_profile: dict[str, Any],
    llm_client: SchemaPolishClient,
) -> list[dict[str, Any]]:
    prompt_payload = {
        "classes": [
            {
                "id": item.id,
                "name": item.name,
                "description": item.description,
                "source_tables": item.source_tables or [item.primary_table],
                "primary_table": item.primary_table,
                "primary_key": item.primary_key,
            }
            for item in mapping.classes
        ],
        "existing_relations": [
            item.model_dump(mode="json")
            for item in mapping.relations + [
                StructuredSemanticRelationConfig(
                    source_class=rel.source_class,
                    predicate=rel.predicate,
                    target_class=rel.target_class,
                    source_table=rel.table,
                    target_table=rel.table,
                    source_column=rel.source_column,
                    target_column=rel.target_column,
                    join_type="relation_table",
                    confidence=rel.confidence,
                    inverse_predicate=rel.inverse_predicate,
                )
                for rel in mapping.relation_tables
            ]
        ],
        "tables": _compact_semantic_profile_for_llm(database_profile),
    }
    prompt = (
        "请基于结构化数据库 metadata 为已声明的领域类补充少量 schema 关系建议。\n"
        "限制：只能使用输入中已有 class id、table、column；必须给出可由字段 join 验证的关系；"
        "不得生成实例事实，不得自由猜测业务事实；不确定或低置信关系不要输出。\n"
        "输出 JSON：{\"relations\":[{\"source_class\":\"...\",\"predicate\":\"...\","
        "\"target_class\":\"...\",\"source_table\":\"...\",\"target_table\":\"...\","
        "\"source_column\":\"...\",\"target_column\":\"...\",\"join_type\":\"llm_verified\","
        "\"confidence\":0.8,\"inverse_predicate\":\"...\"}]}。无建议输出空数组。\n\n"
        f"输入:\n{json.dumps(prompt_payload, ensure_ascii=False, default=str)}"
    )
    raw = _run_async(
        _complete_and_close_llm(
            llm_client,
            prompt,
            "你是数据库本体 schema 关系专家，只输出合法 JSON。",
        )
    )
    try:
        data = _loads_json_object(raw)
    except (json.JSONDecodeError, ValueError):
        return []
    relations = data.get("relations") if isinstance(data, dict) else None
    if not isinstance(relations, list):
        return []
    return [item for item in relations if isinstance(item, dict)]


def _compact_semantic_profile_for_llm(database_profile: dict[str, Any]) -> list[dict[str, Any]]:
    tables: list[dict[str, Any]] = []
    for table in database_profile.get("tables", []):
        tables.append(
            {
                "table_name": table.get("table_name"),
                "description": table.get("description"),
                "primary_key": table.get("primary_key"),
                "columns": [
                    {
                        "column_name": column.get("column_name"),
                        "data_type": column.get("data_type"),
                        "role": column.get("role"),
                        "ref_table": column.get("ref_table"),
                        "ref_column": column.get("ref_column"),
                        "description": column.get("description"),
                        "stats": column.get("stats"),
                    }
                    for column in table.get("columns", [])
                ],
            }
        )
    return tables


def _first_string(value: Any) -> str:
    if isinstance(value, list) and value:
        return str(value[0])
    if isinstance(value, tuple) and value:
        return str(value[0])
    return ""


def _valid_semantic_llm_relations(
    config: StructuredBuildConfig,
    metadata: StructuredMetadata,
    mapping: StructuredSemanticMappingConfig,
    raw_relations: list[dict[str, Any]],
) -> list[StructuredSemanticRelationConfig]:
    class_by_id = {item.id: item for item in mapping.classes}
    existing_keys = {
        (item.source_class, item.predicate, item.target_class)
        for item in mapping.relations
    }
    columns = {
        table_name: {column.column_name for column in table_columns}
        for table_name, table_columns in metadata.columns.items()
    }
    accepted: list[StructuredSemanticRelationConfig] = []
    for item in raw_relations:
        confidence = _bounded_confidence(item.get("confidence"))
        if confidence is None or confidence < config.construction.min_relation_confidence:
            continue
        normalized = {
            "source_class": str(item.get("source_class") or ""),
            "predicate": str(item.get("predicate") or "").strip(),
            "target_class": str(item.get("target_class") or ""),
            "source_table": str(item.get("source_table") or ""),
            "target_table": str(item.get("target_table") or ""),
            "source_column": str(
                item.get("source_column")
                or _first_string(item.get("source_columns"))
                or ""
            ),
            "target_column": str(
                item.get("target_column")
                or _first_string(item.get("target_columns"))
                or ""
            ),
            "join_type": str(item.get("join_type") or "llm_verified"),
            "confidence": confidence,
            "inverse_predicate": str(item.get("inverse_predicate") or "").strip(),
        }
        if not normalized["predicate"]:
            continue
        source_class = class_by_id.get(normalized["source_class"])
        target_class = class_by_id.get(normalized["target_class"])
        if source_class is None or target_class is None:
            continue
        source_tables = set(source_class.source_tables or [source_class.primary_table])
        target_tables = set(target_class.source_tables or [target_class.primary_table])
        if normalized["source_table"] not in source_tables:
            continue
        if normalized["target_table"] not in target_tables:
            continue
        if normalized["source_column"] not in columns.get(normalized["source_table"], set()):
            continue
        if normalized["target_column"] not in columns.get(normalized["target_table"], set()):
            continue
        key = (
            normalized["source_class"],
            normalized["predicate"],
            normalized["target_class"],
        )
        if key in existing_keys:
            continue
        try:
            relation = StructuredSemanticRelationConfig.model_validate(normalized)
        except ValueError:
            continue
        accepted.append(relation)
        existing_keys.add(key)
    return accepted


def _build_semantic_entities(
    metadata: StructuredMetadata,
    mapping: StructuredSemanticMappingConfig,
) -> list[Entity]:
    entities: list[Entity] = []
    for item in mapping.classes:
        if not item.materialize:
            continue
        primary_key = item.primary_key or _primary_key(metadata.columns.get(item.primary_table, [])) or "id"
        attrs: dict[str, Any] = {"说明": item.description or item.name}
        attributes = item.attributes or _semantic_default_attributes(metadata, item)
        for attribute in attributes:
            source_table = attribute.source_table or item.primary_table
            column = _metadata_column(metadata, source_table, attribute.source_column)
            attrs[attribute.source_column] = (
                attribute.description
                or attribute.name
                or (column.description if column else "")
                or (column.data_type if column else "")
            )
        entities.append(
            Entity(
                id=item.id,
                name=item.name,
                entity_type=item.id,
                attributes=attrs,
                primary_key=primary_key,
                source_chunks=[f"structured:semantic:{item.primary_table}"],
            )
        )
    return entities


def _build_semantic_triples(mapping: StructuredSemanticMappingConfig) -> list[Triple]:
    triples: dict[tuple[str, str, str], Triple] = {}
    for item in mapping.relations:
        _add_semantic_schema_triple(
            triples,
            item.source_class,
            item.predicate,
            item.target_class,
            f"structured:semantic:{item.join_type}:{item.source_table}.{item.source_column}",
            item.confidence,
        )
        if item.inverse_predicate:
            _add_semantic_schema_triple(
                triples,
                item.target_class,
                item.inverse_predicate,
                item.source_class,
                f"structured:semantic:inverse:{item.join_type}:{item.source_table}.{item.source_column}",
                item.confidence,
            )
    for item in mapping.relation_tables:
        _add_semantic_schema_triple(
            triples,
            item.source_class,
            item.predicate,
            item.target_class,
            f"structured:semantic:relation_table:{item.table}",
            item.confidence,
        )
        if item.inverse_predicate:
            _add_semantic_schema_triple(
                triples,
                item.target_class,
                item.inverse_predicate,
                item.source_class,
                f"structured:semantic:inverse:relation_table:{item.table}",
                item.confidence,
            )
    for item in mapping.derived_relations:
        source = (
            f"structured:semantic:derived:{item.via_table}"
            if item.via_table
            else f"structured:semantic:derived:{item.source_table}.{item.source_column}"
        )
        _add_semantic_schema_triple(
            triples,
            item.source_class,
            item.predicate,
            item.target_class,
            source,
            item.confidence,
        )
        if item.inverse_predicate:
            _add_semantic_schema_triple(
                triples,
                item.target_class,
                item.inverse_predicate,
                item.source_class,
                f"structured:semantic:inverse:derived:{item.source_table}.{item.source_column}",
                item.confidence,
            )
    return list(triples.values())


def _add_semantic_schema_triple(
    triples: dict[tuple[str, str, str], Triple],
    source_class: str,
    predicate: str,
    target_class: str,
    source_chunk: str,
    confidence: float,
) -> None:
    _add_schema_triple(
        triples,
        source_class,
        predicate,
        target_class,
        source_chunk,
    )
    triples[(source_class, predicate, target_class)].confidence = confidence


def _build_semantic_instances(
    config: StructuredBuildConfig,
    metadata: StructuredMetadata,
    mapping: StructuredSemanticMappingConfig,
    rows_by_table: dict[str, list[dict[str, Any]]],
) -> tuple[list[OntologyInstance], dict[tuple[str, str, str], str]]:
    instances: list[OntologyInstance] = []
    lookup: dict[tuple[str, str, str], str] = {}
    for class_config in mapping.classes:
        if not class_config.materialize:
            continue
        columns = metadata.columns.get(class_config.primary_table, [])
        rows = rows_by_table.get(class_config.primary_table, [])
        for row in rows:
            instance = _semantic_row_to_instance(metadata, class_config, columns, row)
            if instance is None:
                continue
            instances.append(instance)
            _index_semantic_instance(lookup, class_config, columns, row, instance.id)
    return instances, lookup


def _add_semantic_relation_links(
    config: StructuredBuildConfig,
    metadata: StructuredMetadata,
    mapping: StructuredSemanticMappingConfig,
    repository: StructuredRepository,
    rows_by_table: dict[str, list[dict[str, Any]]],
    class_by_id: dict[str, StructuredSemanticClassConfig],
    instance_lookup: dict[tuple[str, str, str], str],
    instance_by_id: dict[str, OntologyInstance],
) -> None:
    for relation in mapping.relations:
        source_class = class_by_id[relation.source_class]
        target_class = class_by_id[relation.target_class]
        rows = rows_by_table.get(relation.source_table, [])
        source_pk = source_class.primary_key or _primary_key(metadata.columns.get(source_class.primary_table, []))
        for row in rows:
            source_raw = row.get(source_pk) if relation.source_table == source_class.primary_table else None
            if source_raw is None:
                continue
            source_id = instance_lookup.get((source_class.primary_table, source_pk, str(source_raw)))
            source = instance_by_id.get(source_id or "")
            if source is None:
                continue
            target_raw = row.get(relation.source_column)
            target_id = _ensure_semantic_instance(
                metadata,
                repository,
                target_class,
                relation.target_column,
                target_raw,
                instance_lookup,
                instance_by_id,
                config.semantic_mapping.sampling.auto_include_related
                if config.semantic_mapping
                else True,
            )
            if target_id:
                _add_instance_link(
                    source,
                    relation.predicate,
                    target_id,
                    f"structured:semantic:{relation.join_type}:{relation.source_table}.{relation.source_column}",
                )
                if relation.inverse_predicate:
                    _add_semantic_inverse_link(
                        instance_by_id,
                        target_id,
                        relation.inverse_predicate,
                        source.id,
                        f"structured:semantic:inverse:{relation.join_type}:{relation.source_table}.{relation.source_column}",
                    )

    for relation in mapping.relation_tables:
        source_class = class_by_id[relation.source_class]
        target_class = class_by_id[relation.target_class]
        source_pk = source_class.primary_key or _primary_key(metadata.columns.get(source_class.primary_table, []))
        target_pk = target_class.primary_key or _primary_key(metadata.columns.get(target_class.primary_table, []))
        rows = rows_by_table.get(relation.table, [])
        for row in rows:
            source_id = _ensure_semantic_instance(
                metadata,
                repository,
                source_class,
                source_pk,
                row.get(relation.source_column),
                instance_lookup,
                instance_by_id,
                config.semantic_mapping.sampling.auto_include_related
                if config.semantic_mapping
                else True,
            )
            target_id = _ensure_semantic_instance(
                metadata,
                repository,
                target_class,
                target_pk,
                row.get(relation.target_column),
                instance_lookup,
                instance_by_id,
                config.semantic_mapping.sampling.auto_include_related
                if config.semantic_mapping
                else True,
            )
            source = instance_by_id.get(source_id or "")
            if source is not None and target_id:
                _add_instance_link(
                    source,
                    relation.predicate,
                    target_id,
                    f"structured:semantic:relation_table:{relation.table}",
                )
                if relation.inverse_predicate:
                    _add_semantic_inverse_link(
                        instance_by_id,
                        target_id,
                        relation.inverse_predicate,
                        source.id,
                        f"structured:semantic:inverse:relation_table:{relation.table}",
                    )


def _add_semantic_derived_links(
    config: StructuredBuildConfig,
    metadata: StructuredMetadata,
    mapping: StructuredSemanticMappingConfig,
    repository: StructuredRepository,
    rows_by_table: dict[str, list[dict[str, Any]]],
    class_by_id: dict[str, StructuredSemanticClassConfig],
    instance_lookup: dict[tuple[str, str, str], str],
    instance_by_id: dict[str, OntologyInstance],
) -> None:
    auto_include = (
        config.semantic_mapping.sampling.auto_include_related
        if config.semantic_mapping
        else True
    )
    for relation in mapping.derived_relations:
        source_class = class_by_id[relation.source_class]
        target_class = class_by_id[relation.target_class]
        source_pk = source_class.primary_key or _primary_key(metadata.columns.get(source_class.primary_table, []))
        source_rows = rows_by_table.get(relation.source_table, [])
        for source_row in source_rows:
            source_raw = (
                source_row.get(source_pk)
                if relation.source_table == source_class.primary_table
                else None
            )
            if source_raw is None:
                continue
            source_id = instance_lookup.get(
                (source_class.primary_table, source_pk, str(source_raw))
            )
            source = instance_by_id.get(source_id or "")
            if source is None:
                continue
            target_values = _semantic_derived_target_values(
                relation,
                source_row,
                rows_by_table,
            )
            if not target_values:
                continue
            for target_raw in target_values:
                target_ids = _ensure_semantic_instances(
                    metadata,
                    repository,
                    target_class,
                    relation.target_column,
                    target_raw,
                    instance_lookup,
                    instance_by_id,
                    auto_include,
                )
                for target_id in target_ids:
                    _add_instance_link(
                        source,
                        relation.predicate,
                        target_id,
                        f"structured:semantic:derived:{relation.source_table}.{relation.source_column}",
                    )
                    if relation.inverse_predicate:
                        _add_semantic_inverse_link(
                            instance_by_id,
                            target_id,
                            relation.inverse_predicate,
                            source.id,
                            f"structured:semantic:inverse:derived:{relation.source_table}.{relation.source_column}",
                        )


def _semantic_derived_target_values(
    relation: StructuredSemanticDerivedRelationConfig,
    source_row: dict[str, Any],
    rows_by_table: dict[str, list[dict[str, Any]]],
) -> list[Any]:
    source_value = source_row.get(relation.source_column)
    if source_value is None:
        return []
    if not relation.via_table:
        return [source_value]
    target_values: list[Any] = []
    source_key = str(source_value)
    for via_row in rows_by_table.get(relation.via_table, []):
        if str(via_row.get(relation.via_source_column)) != source_key:
            continue
        target_value = via_row.get(relation.via_target_column)
        if target_value is not None:
            target_values.append(target_value)
    return list(dict.fromkeys(target_values))


def _add_semantic_inverse_link(
    instance_by_id: dict[str, OntologyInstance],
    source_id: str,
    predicate: str,
    target_id: str,
    source_chunk: str,
) -> None:
    source = instance_by_id.get(source_id)
    if source is None:
        return
    _add_instance_link(source, predicate, target_id, source_chunk)


def _ensure_semantic_instance(
    metadata: StructuredMetadata,
    repository: StructuredRepository,
    class_config: StructuredSemanticClassConfig,
    key_column: str,
    raw_value: Any,
    instance_lookup: dict[tuple[str, str, str], str],
    instance_by_id: dict[str, OntologyInstance],
    auto_include_related: bool,
) -> str | None:
    if raw_value is None:
        return None
    key = (class_config.primary_table, key_column, str(raw_value))
    existing = instance_lookup.get(key)
    if existing:
        return existing
    if not auto_include_related:
        return None
    columns = metadata.columns.get(class_config.primary_table, [])
    if not columns:
        return None
    fetcher = getattr(repository, "fetch_rows_by_values", None)
    if fetcher is None:
        return None
    rows = fetcher(class_config.primary_table, columns, key_column, [raw_value])
    for row in rows:
        instance = _semantic_row_to_instance(metadata, class_config, columns, row)
        if instance is None:
            continue
        instance_by_id.setdefault(instance.id, instance)
        _index_semantic_instance(instance_lookup, class_config, columns, row, instance.id)
        if instance_lookup.get(key):
            return instance_lookup[key]
    return None


def _ensure_semantic_instances(
    metadata: StructuredMetadata,
    repository: StructuredRepository,
    class_config: StructuredSemanticClassConfig,
    key_column: str,
    raw_value: Any,
    instance_lookup: dict[tuple[str, str, str], str],
    instance_by_id: dict[str, OntologyInstance],
    auto_include_related: bool,
) -> list[str]:
    if raw_value is None:
        return []
    key = (class_config.primary_table, key_column, str(raw_value))
    if key_column == class_config.primary_key:
        existing = instance_lookup.get(key)
        if existing:
            return [existing]
    if not auto_include_related and instance_lookup.get(key):
        return [instance_lookup[key]]
    if not auto_include_related:
        return []
    columns = metadata.columns.get(class_config.primary_table, [])
    if not columns:
        return []
    fetcher = getattr(repository, "fetch_rows_by_values", None)
    if fetcher is None:
        return []
    instance_ids: list[str] = []
    rows = fetcher(class_config.primary_table, columns, key_column, [raw_value])
    for row in rows:
        instance = _semantic_row_to_instance(metadata, class_config, columns, row)
        if instance is None:
            continue
        instance_by_id.setdefault(instance.id, instance)
        _index_semantic_instance(instance_lookup, class_config, columns, row, instance.id)
        instance_ids.append(instance.id)
    if not instance_ids and instance_lookup.get(key):
        instance_ids.append(instance_lookup[key])
    return list(dict.fromkeys(instance_ids))


def _semantic_row_to_instance(
    metadata: StructuredMetadata,
    class_config: StructuredSemanticClassConfig,
    columns: list[StructuredColumn],
    row: dict[str, Any],
) -> OntologyInstance | None:
    primary_key = class_config.primary_key or _primary_key(columns)
    if not primary_key or row.get(primary_key) is None:
        return None
    raw_id = str(row[primary_key])
    label_column = class_config.label_column or _label_column(columns)
    name = str(row.get(label_column) or raw_id) if label_column else raw_id
    attributes = class_config.attributes or _semantic_default_attributes(metadata, class_config)
    values: list[InstanceAttributeValue] = []
    for attribute in attributes:
        source_table = attribute.source_table or class_config.primary_table
        if source_table != class_config.primary_table:
            continue
        value = row.get(attribute.source_column)
        if value is None:
            continue
        column = _metadata_column(metadata, source_table, attribute.source_column)
        values.append(
            InstanceAttributeValue(
                attribute_name=attribute.name or attribute.source_column,
                value=_structured_scalar_value(value),
                data_type=_data_type(attribute.data_type or (column.data_type if column else ""), value),
                source_chunks=[f"structured:semantic:{class_config.primary_table}"],
            )
        )
    return OntologyInstance(
        id=f"{class_config.id}:{raw_id}",
        name=name,
        class_id=class_config.id,
        attribute_values=values,
        source_chunks=[f"structured:semantic:{class_config.primary_table}"],
    )


def _index_semantic_instance(
    lookup: dict[tuple[str, str, str], str],
    class_config: StructuredSemanticClassConfig,
    columns: list[StructuredColumn],
    row: dict[str, Any],
    instance_id: str,
) -> None:
    for column in columns:
        value = row.get(column.column_name)
        if value is None:
            continue
        lookup.setdefault((class_config.primary_table, column.column_name, str(value)), instance_id)


def _semantic_default_attributes(
    metadata: StructuredMetadata,
    class_config: StructuredSemanticClassConfig,
) -> list[StructuredSemanticAttributeConfig]:
    attributes: list[StructuredSemanticAttributeConfig] = []
    for column in metadata.columns.get(class_config.primary_table, []):
        if column.role in {"pk", "fk", "label"}:
            continue
        attributes.append(
            StructuredSemanticAttributeConfig(
                source_table=class_config.primary_table,
                source_column=column.column_name,
                name=column.description or column.column_name,
                data_type=column.data_type,
                description=column.description,
            )
        )
    return attributes


def _semantic_sample_limit(
    config: StructuredBuildConfig,
    class_config: StructuredSemanticClassConfig,
) -> int:
    mapping = config.semantic_mapping
    if mapping is None:
        return 0
    if class_config.id in mapping.sampling.class_limits:
        return mapping.sampling.class_limits[class_config.id]
    if class_config.primary_table in mapping.sampling.table_limits:
        return mapping.sampling.table_limits[class_config.primary_table]
    if mapping.sampling.default_per_class is not None:
        return mapping.sampling.default_per_class
    table = StructuredTable(class_config.primary_table)
    return _sample_limit(config, table)


def _semantic_table_limit(config: StructuredBuildConfig, table_name: str) -> int:
    mapping = config.semantic_mapping
    if mapping is None:
        return 0
    if table_name in mapping.sampling.table_limits:
        return mapping.sampling.table_limits[table_name]
    if mapping.sampling.default_per_class is not None:
        return mapping.sampling.default_per_class
    return config.instances.relation_sample.per_relation_type


def _metadata_column(
    metadata: StructuredMetadata,
    table_name: str,
    column_name: str,
) -> StructuredColumn | None:
    for column in metadata.columns.get(table_name, []):
        if column.column_name == column_name:
            return column
    return None


def _semantic_candidates_artifact(mapping: StructuredSemanticMappingConfig) -> dict[str, Any]:
    return {
        "version": 1,
        "mode": "semantic_mapping",
        "classes": [item.model_dump(mode="json") for item in mapping.classes],
        "relations": [item.model_dump(mode="json") for item in mapping.relations],
        "relation_tables": [
            item.model_dump(mode="json") for item in mapping.relation_tables
        ],
        "derived_relations": [
            item.model_dump(mode="json") for item in mapping.derived_relations
        ],
        "event_tables": [item.model_dump(mode="json") for item in mapping.event_tables],
    }


def _semantic_mapping_artifact(mapping: StructuredSemanticMappingConfig) -> dict[str, Any]:
    classes = [
        {
            "class_id": item.id,
            "name": item.name,
            "description": item.description,
            "source_tables": item.source_tables or [item.primary_table],
            "primary_key": item.primary_key,
            "label_column": item.label_column,
            "attributes": [
                attribute.model_dump(mode="json") for attribute in item.attributes
            ],
            "evidence": ["semantic_mapping"],
            "confidence": 1.0,
        }
        for item in mapping.classes
        if item.materialize
    ]
    relations = [
        {
            "predicate": item.predicate,
            "source_class": item.source_class,
            "target_class": item.target_class,
            "source_table": item.source_table,
            "target_table": item.target_table,
            "source_columns": [item.source_column],
            "target_columns": [item.target_column],
            "join": {"type": item.join_type},
            "inverse_predicate": item.inverse_predicate,
            "evidence": ["semantic_mapping"],
            "confidence": item.confidence,
        }
        for item in mapping.relations
    ]
    relations.extend(
        {
            "predicate": item.predicate,
            "source_class": item.source_class,
            "target_class": item.target_class,
            "source_table": item.table,
            "target_table": item.table,
            "source_columns": [item.source_column],
            "target_columns": [item.target_column],
            "join": {"type": "relation_table", "table": item.table},
            "inverse_predicate": item.inverse_predicate,
            "evidence": ["semantic_mapping", "relation_table"],
            "confidence": item.confidence,
        }
        for item in mapping.relation_tables
    )
    relations.extend(
        {
            "predicate": item.predicate,
            "source_class": item.source_class,
            "target_class": item.target_class,
            "source_table": item.source_table,
            "target_table": item.target_table,
            "source_columns": [item.source_column],
            "target_columns": [item.target_column],
            "join": {
                "type": "derived",
                "via_table": item.via_table,
                "via_source_column": item.via_source_column,
                "via_target_column": item.via_target_column,
            },
            "inverse_predicate": item.inverse_predicate,
            "evidence": ["semantic_mapping", "derived_relation"],
            "confidence": item.confidence,
        }
        for item in mapping.derived_relations
    )
    return {"version": 1, "mode": "semantic_mapping", "classes": classes, "relations": relations}


def _structured_quality(ontology: GlobalOntology) -> dict[str, Any]:
    instances_by_class: dict[str, int] = {}
    links_by_predicate: dict[str, int] = {}
    instance_link_count = 0
    fallback_link_count = 0
    verified_link_count = 0
    linked_instance_ids: set[str] = set()
    for instance in ontology.instances:
        instances_by_class[instance.class_id] = instances_by_class.get(instance.class_id, 0) + 1
        for link in instance.link_values:
            instance_link_count += 1
            linked_instance_ids.add(instance.id)
            linked_instance_ids.add(link.target_instance_id)
            links_by_predicate[link.predicate] = links_by_predicate.get(link.predicate, 0) + 1
            sources = " ".join(link.source_chunks)
            if "fallback" in sources or link.predicate == "语义关联":
                fallback_link_count += 1
            if any(
                marker in sources
                for marker in (
                    "structured:semantic:",
                    "structured:fk:",
                    "structured:relations",
                    "structured:event:",
                )
            ):
                verified_link_count += 1
    isolated = sum(1 for instance in ontology.instances if instance.id not in linked_instance_ids)
    instance_count = len(ontology.instances)
    return {
        "objectCount": len(ontology.entities),
        "schemaRelationCount": len(ontology.triples),
        "instanceCount": instance_count,
        "instanceLinkCount": instance_link_count,
        "isolatedInstanceCount": isolated,
        "isolatedInstanceRatio": round(isolated / instance_count, 4) if instance_count else 0,
        "fallbackLinkCount": fallback_link_count,
        "fallbackLinkRatio": round(fallback_link_count / instance_link_count, 4)
        if instance_link_count
        else 0,
        "verifiedLinkCount": verified_link_count,
        "verifiedLinkRatio": round(verified_link_count / instance_link_count, 4)
        if instance_link_count
        else 0,
        "instancesByClass": dict(sorted(instances_by_class.items())),
        "linksByPredicate": dict(sorted(links_by_predicate.items())),
    }


def _build_database_profile(
    metadata: StructuredMetadata,
    rows_by_table: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    tables: list[dict[str, Any]] = []
    for table in metadata.tables.values():
        columns = metadata.columns.get(table.table_name, [])
        rows = rows_by_table.get(table.table_name, [])
        table_profile = {
            "table_name": table.table_name,
            "kind": table.kind,
            "entity_type": metadata.entity_map.get(table.table_name)
            or table.entity_type
            or table.table_name,
            "description": table.description or "",
            "row_count": table.row_count,
            "primary_key": _primary_key(columns),
            "label_column": _label_column(columns),
            "columns": [
                {
                    "column_name": column.column_name,
                    "data_type": column.data_type,
                    "is_nullable": column.is_nullable,
                    "role": column.role,
                    "ref_table": column.ref_table,
                    "ref_column": column.ref_column,
                    "description": column.description,
                    "signals": _column_signals(column),
                    "stats": _column_sample_stats(column.column_name, rows),
                }
                for column in columns
            ],
            "sample_rows": rows[:5],
            "evidence": _table_evidence(table),
        }
        tables.append(table_profile)
    return {"version": 1, "tables": tables}


def _build_structured_candidates(
    metadata: StructuredMetadata,
    database_profile: dict[str, Any],
) -> dict[str, Any]:
    profile_by_table = {
        table["table_name"]: table for table in database_profile.get("tables", [])
    }
    class_candidates: list[dict[str, Any]] = []
    attribute_candidates: list[dict[str, Any]] = []
    relation_candidates: dict[tuple[str, str, str], dict[str, Any]] = {}
    class_table_names = {table.table_name for table in _class_tables(metadata)}
    table_by_entity_type: dict[str, list[str]] = {}

    for table in _class_tables(metadata):
        entity_type = metadata.entity_map.get(table.table_name) or table.entity_type or table.table_name
        table_by_entity_type.setdefault(entity_type, []).append(table.table_name)
        columns = metadata.columns.get(table.table_name, [])
        class_candidates.append(
            {
                "candidate_id": table.table_name,
                "class_id": table.table_name,
                "name": table.description or table.entity_type or table.table_name,
                "description": table.description or table.table_name,
                "source_tables": [table.table_name],
                "primary_key": _primary_key(columns) or "id",
                "label_column": _label_column(columns),
                "confidence": 0.95 if table.description or table.entity_type else 0.75,
                "evidence": _table_evidence(table),
                "embedding_text": _table_embedding_text(profile_by_table.get(table.table_name, {})),
            }
        )
        for column in columns:
            if column.role in {"pk", "fk", "label"}:
                continue
            attribute_candidates.append(
                {
                    "class_id": table.table_name,
                    "source_table": table.table_name,
                    "source_column": column.column_name,
                    "name": column.description or column.column_name,
                    "data_type": column.data_type,
                    "role": column.role,
                    "confidence": 0.9 if column.description or column.role else 0.7,
                    "evidence": _column_evidence(column),
                }
            )

    for relation in metadata.relation_map:
        for source_type in relation.src_entity_types:
            for target_type in relation.dst_entity_types:
                for source_table in table_by_entity_type.get(source_type, []):
                    for target_table in table_by_entity_type.get(target_type, []):
                        _add_relation_candidate(
                            relation_candidates,
                            source_table,
                            relation.rel_type,
                            target_table,
                            source_columns=[relation.src_col],
                            target_columns=[relation.dst_col],
                            join={"type": "relations_table", "table": relation.holder_table},
                            confidence=0.95,
                            evidence=["meta_relation_map"],
                        )

    for table_name, columns in metadata.columns.items():
        if table_name not in class_table_names:
            continue
        for column in columns:
            if column.role == "fk" and column.ref_table in class_table_names:
                _add_relation_candidate(
                    relation_candidates,
                    table_name,
                    _predicate_from_fk(column, metadata.tables.get(column.ref_table)),
                    column.ref_table,
                    source_columns=[column.column_name],
                    target_columns=[column.ref_column or _primary_key(metadata.columns.get(column.ref_table, []))],
                    join={
                        "type": "fk",
                        "source_column": column.column_name,
                        "target_column": column.ref_column or "id",
                    },
                    confidence=0.98,
                    evidence=["foreign_key"],
                )
                continue
            if column.role != "fk" and column.column_name.endswith(("_id", "_code", "_no")):
                target_table = _guess_target_table(column.column_name, class_table_names)
                if target_table and target_table != table_name:
                    _add_relation_candidate(
                        relation_candidates,
                        table_name,
                        _predicate_from_fk(
                            StructuredColumn(
                                table_name,
                                column.column_name,
                                ref_table=target_table,
                            ),
                            metadata.tables.get(target_table),
                        ),
                        target_table,
                        source_columns=[column.column_name],
                        target_columns=[_primary_key(metadata.columns.get(target_table, [])) or "id"],
                        join={"type": "name_heuristic", "source_column": column.column_name},
                        confidence=0.72,
                        evidence=["field_name_heuristic"],
                    )

    _add_value_overlap_relation_candidates(
        metadata,
        database_profile,
        relation_candidates,
        class_table_names,
    )

    return {
        "version": 1,
        "classes": class_candidates,
        "attributes": attribute_candidates,
        "relations": list(relation_candidates.values()),
        "embedding_merges": [],
    }


def _apply_embedding_evidence(
    config: StructuredBuildConfig,
    candidates: dict[str, Any],
    embedding_client: StructuredEmbeddingClient,
) -> None:
    classes = candidates.get("classes", [])
    if len(classes) < 2:
        return
    texts = [item.get("embedding_text") or item.get("name") or item["class_id"] for item in classes]
    vectors = _run_async(embedding_client.embed_texts(texts))
    merges: list[dict[str, Any]] = []
    for left in range(len(classes)):
        for right in range(left + 1, len(classes)):
            similarity = _cosine_similarity(vectors[left], vectors[right])
            if similarity >= config.construction.class_merge_similarity:
                merges.append(
                    {
                        "left_class_id": classes[left]["class_id"],
                        "right_class_id": classes[right]["class_id"],
                        "similarity": round(similarity, 4),
                        "evidence": ["embedding_class_similarity"],
                    }
                )
    candidates["embedding_merges"] = merges


def _build_structured_mapping(
    config: StructuredBuildConfig,
    metadata: StructuredMetadata,
    candidates: dict[str, Any],
    llm_client: SchemaPolishClient | None,
) -> dict[str, Any]:
    mapping = _deterministic_mapping_from_candidates(candidates)
    if not config.construction.use_llm or not config.llm.enabled:
        return mapping
    if llm_client is None:
        raise RuntimeError("Missing LLM client for hybrid structured construction")
    raw_mapping = _request_llm_mapping(config, candidates, llm_client)
    if not raw_mapping:
        return mapping
    return _merge_valid_llm_mapping(metadata, mapping, raw_mapping)


def _deterministic_mapping_from_candidates(candidates: dict[str, Any]) -> dict[str, Any]:
    attributes_by_class: dict[str, list[dict[str, Any]]] = {}
    for item in candidates.get("attributes", []):
        attributes_by_class.setdefault(item["class_id"], []).append(
            {
                "name": item["name"],
                "source_table": item["source_table"],
                "source_column": item["source_column"],
                "data_type": item["data_type"],
                "evidence": item["evidence"],
                "confidence": item["confidence"],
            }
        )
    classes = [
        {
            "class_id": item["class_id"],
            "name": item["name"],
            "description": item["description"],
            "source_tables": item["source_tables"],
            "primary_key": item["primary_key"],
            "label_column": item.get("label_column") or "",
            "attributes": attributes_by_class.get(item["class_id"], []),
            "evidence": item["evidence"],
            "confidence": item["confidence"],
        }
        for item in candidates.get("classes", [])
    ]
    relations = [
        {
            "predicate": item["predicate"],
            "source_class": item["source_class"],
            "target_class": item["target_class"],
            "source_table": item["source_table"],
            "target_table": item["target_table"],
            "source_columns": item["source_columns"],
            "target_columns": item["target_columns"],
            "join": item["join"],
            "evidence": item["evidence"],
            "confidence": item["confidence"],
        }
        for item in candidates.get("relations", [])
    ]
    return {"version": 1, "classes": classes, "relations": relations}


def _request_llm_mapping(
    config: StructuredBuildConfig,
    candidates: dict[str, Any],
    llm_client: SchemaPolishClient,
) -> dict[str, Any] | None:
    prompt_payload = _compact_candidates_for_llm(candidates)
    prompt = (
        "请基于数据库候选证据返回本体 mapping 的少量修正项。\n"
        "限制：只返回需要改名、改描述或确认的新关系；不要复述全部输入；"
        "只能引用输入中已有的 source_tables/source_columns；不能生成实例或事实；"
        "不确定的关系不要输出。\n"
        "输出 JSON：{\"classes\":[],\"relations\":[]}。无修正时输出空数组。\n\n"
        f"候选证据:\n{json.dumps(prompt_payload, ensure_ascii=False)}"
    )
    raw = _run_async(
        _complete_and_close_llm(
            llm_client,
            prompt,
            "你是数据库本体映射专家，只输出合法 JSON。",
        )
    )
    try:
        data = _loads_json_object(raw)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def _compact_candidates_for_llm(candidates: dict[str, Any]) -> dict[str, Any]:
    classes = []
    for item in candidates.get("classes", []):
        classes.append(
            {
                "class_id": item["class_id"],
                "name": item["name"],
                "description": item["description"],
                "source_tables": item["source_tables"],
            }
        )
    relations = [
        {
            "predicate": item["predicate"],
            "source_class": item["source_class"],
            "target_class": item["target_class"],
            "source_table": item["source_table"],
            "target_table": item["target_table"],
            "source_columns": item["source_columns"],
            "target_columns": item["target_columns"],
            "evidence": ",".join(item["evidence"]),
            "confidence": item["confidence"],
        }
        for item in candidates.get("relations", [])
        if float(item.get("confidence") or 0) < 0.9
        or any(
            evidence in {"field_name_heuristic", "value_overlap", "embedding_relation_similarity"}
            for evidence in item.get("evidence", [])
        )
    ]
    return {
        "classes": classes,
        "relations": relations[:8],
        "embedding_merges": candidates.get("embedding_merges", [])[:10],
    }


def _merge_valid_llm_mapping(
    metadata: StructuredMetadata,
    mapping: dict[str, Any],
    llm_mapping: dict[str, Any],
) -> dict[str, Any]:
    valid_tables = set(metadata.tables)
    valid_columns = {
        (table_name, column.column_name)
        for table_name, columns in metadata.columns.items()
        for column in columns
    }
    class_by_id = {item["class_id"]: item for item in mapping.get("classes", [])}
    for item in llm_mapping.get("classes", []):
        if not isinstance(item, dict):
            continue
        source_tables = [
            str(table)
            for table in item.get("source_tables", [])
            if str(table) in valid_tables
        ]
        if not source_tables:
            continue
        class_id = str(item.get("class_id") or source_tables[0])
        existing = class_by_id.get(class_id) or class_by_id.get(source_tables[0])
        if existing is None:
            continue
        name = str(item.get("name") or "").strip()
        description = str(item.get("description") or "").strip()
        if name:
            existing["name"] = name
        if description:
            existing["description"] = description
        confidence = _bounded_confidence(item.get("confidence"))
        if confidence is not None:
            existing["confidence"] = confidence
        evidence = [str(value) for value in item.get("evidence", []) if value]
        if evidence:
            existing["evidence"] = list(dict.fromkeys(existing.get("evidence", []) + evidence + ["llm_schema_mapping"]))

    relation_keys = {
        (item["source_class"], item["predicate"], item["target_class"])
        for item in mapping.get("relations", [])
    }
    for item in llm_mapping.get("relations", []):
        if not isinstance(item, dict):
            continue
        source_class = str(item.get("source_class") or item.get("source_table") or "")
        target_class = str(item.get("target_class") or item.get("target_table") or "")
        source_table = str(item.get("source_table") or source_class)
        target_table = str(item.get("target_table") or target_class)
        predicate = str(item.get("predicate") or "").strip()
        if (
            not predicate
            or source_table not in valid_tables
            or target_table not in valid_tables
            or source_class not in class_by_id
            or target_class not in class_by_id
        ):
            continue
        source_columns = [str(value) for value in item.get("source_columns", [])]
        target_columns = [str(value) for value in item.get("target_columns", [])]
        if not source_columns or not target_columns:
            continue
        if any((source_table, column) not in valid_columns for column in source_columns):
            continue
        if any((target_table, column) not in valid_columns for column in target_columns):
            continue
        key = (source_class, predicate, target_class)
        if key in relation_keys:
            continue
        confidence = _bounded_confidence(item.get("confidence")) or 0.75
        mapping.setdefault("relations", []).append(
            {
                "predicate": predicate,
                "source_class": source_class,
                "target_class": target_class,
                "source_table": source_table,
                "target_table": target_table,
                "source_columns": source_columns,
                "target_columns": target_columns,
                "join": item.get("join") if isinstance(item.get("join"), dict) else {"type": "llm_verified"},
                "evidence": list(dict.fromkeys([str(value) for value in item.get("evidence", []) if value] + ["llm_relation_mapping"])),
                "confidence": confidence,
            }
        )
        relation_keys.add(key)
    return mapping


def _build_entities_from_mapping(
    metadata: StructuredMetadata,
    mapping: dict[str, Any],
) -> list[Entity]:
    entities: list[Entity] = []
    for item in mapping.get("classes", []):
        class_id = item["class_id"]
        source_table = item.get("source_tables", [class_id])[0]
        attrs: dict[str, Any] = {"说明": item.get("description") or item.get("name") or class_id}
        for attribute in item.get("attributes", []):
            attrs[attribute["source_column"]] = attribute.get("name") or attribute.get("data_type") or ""
        entities.append(
            Entity(
                id=class_id,
                name=item.get("name") or class_id,
                entity_type=metadata.entity_map.get(source_table) or class_id,
                attributes=attrs,
                primary_key=item.get("primary_key") or _primary_key(metadata.columns.get(source_table, [])) or "id",
                source_chunks=[f"structured:mapping:{source_table}"],
            )
        )
    return entities


def _build_schema_triples_from_mapping(
    config: StructuredBuildConfig,
    mapping: dict[str, Any],
) -> list[Triple]:
    triples: dict[tuple[str, str, str], Triple] = {}
    for relation in mapping.get("relations", []):
        confidence = float(relation.get("confidence") or 0)
        _add_schema_triple(
            triples,
            relation["source_class"],
            relation["predicate"],
            relation["target_class"],
            "structured:mapping",
        )
        key = (relation["source_class"], relation["predicate"], relation["target_class"])
        triples[key].confidence = max(
            triples[key].confidence,
            confidence if confidence >= config.construction.min_relation_confidence else 0.5,
        )
    return list(triples.values())


async def polish_structured_schema(
    ontology: GlobalOntology,
    llm_client: SchemaPolishClient,
) -> GlobalOntology:
    """Polish display text only; ids, triples and instances remain deterministic."""
    payload = {
        "objects": [
            {
                "id": entity.id,
                "name": entity.name,
                "description": entity.attributes.get("说明", ""),
                "attributes": [
                    name for name in entity.attributes.keys() if name != "说明"
                ],
            }
            for entity in ontology.entities.values()
        ],
        "relations": [
            {
                "source": triple.subject_id,
                "predicate": triple.predicate,
                "target": triple.object_id,
            }
            for triple in ontology.triples
            if triple.object_kind == "entity"
        ],
    }
    prompt = (
        "请润色结构化数据库自动生成的本体 schema 展示文本。\n"
        "限制：只能返回已有 objects 的 id、name、description；不得新增、删除或修改 id；"
        "不得生成实例、事实关系或数据库中不存在的信息。\n"
        "输出 JSON：{\"objects\":[{\"id\":\"...\",\"name\":\"...\",\"description\":\"...\"}]}。\n\n"
        f"当前 schema:\n{json.dumps(payload, ensure_ascii=False)}"
    )
    raw = await llm_client.complete(
        prompt,
        system_prompt="你只负责润色本体对象显示名和描述，只输出合法 JSON。",
    )
    try:
        data = _loads_json_object(raw)
    except ValueError:
        return ontology
    if not isinstance(data.get("objects"), list):
        return ontology
    for item in data["objects"]:
        if not isinstance(item, dict):
            continue
        entity_id = str(item.get("id") or "")
        entity = ontology.entities.get(entity_id)
        if entity is None:
            continue
        name = str(item.get("name") or "").strip()
        description = str(item.get("description") or "").strip()
        if name:
            entity.name = name
        if description:
            entity.attributes["说明"] = description
    return ontology


def _class_tables(metadata: StructuredMetadata) -> list[StructuredTable]:
    result: list[StructuredTable] = []
    for table in metadata.tables.values():
        if table.table_name in _UNSUPPORTED_TABLES:
            continue
        if table.kind in _SCHEMA_KINDS or table.table_name in _EVENT_TABLE_NAMES:
            result.append(table)
    return result


def _build_entities(
    metadata: StructuredMetadata,
    class_tables: list[StructuredTable],
) -> list[Entity]:
    entities: list[Entity] = []
    for table in class_tables:
        attrs: dict[str, Any] = {"说明": table.description or table.table_name}
        for column in metadata.columns.get(table.table_name, []):
            if column.role in {"pk", "fk", "label"}:
                continue
            attrs[column.column_name] = column.description or column.data_type
        entities.append(
            Entity(
                id=table.table_name,
                name=table.description or table.entity_type or table.table_name,
                entity_type=metadata.entity_map.get(table.table_name)
                or table.entity_type
                or table.table_name,
                attributes=attrs,
                primary_key=_primary_key(metadata.columns.get(table.table_name, []))
                or "id",
                source_chunks=[f"structured:{table.table_name}"],
            )
        )
    return entities


def _build_schema_triples(
    metadata: StructuredMetadata,
    entity_by_table: dict[str, Entity],
) -> list[Triple]:
    triples: dict[tuple[str, str, str], Triple] = {}
    table_by_entity_type: dict[str, list[str]] = {}
    for table in metadata.tables.values():
        if table.table_name not in entity_by_table:
            continue
        entity_type = metadata.entity_map.get(table.table_name) or table.entity_type
        table_by_entity_type.setdefault(entity_type, []).append(table.table_name)
    for relation in metadata.relation_map:
        for source_type in relation.src_entity_types:
            for target_type in relation.dst_entity_types:
                for source_table in table_by_entity_type.get(source_type, []):
                    for target_table in table_by_entity_type.get(target_type, []):
                        _add_schema_triple(
                            triples,
                            source_table,
                            relation.rel_type,
                            target_table,
                            "structured:relation_map",
                        )
    for table_name, columns in metadata.columns.items():
        if table_name not in entity_by_table:
            continue
        for column in columns:
            if column.role != "fk" or column.ref_table not in entity_by_table:
                continue
            _add_schema_triple(
                triples,
                table_name,
                _predicate_from_fk(column, metadata.tables.get(column.ref_table)),
                column.ref_table,
                f"structured:fk:{table_name}.{column.column_name}",
            )
    return list(triples.values())


def _add_schema_triple(
    triples: dict[tuple[str, str, str], Triple],
    source_table: str,
    predicate: str,
    target_table: str,
    source_chunk: str,
) -> None:
    if source_table == target_table and predicate == "关联":
        return
    key = (source_table, predicate, target_table)
    triples.setdefault(
        key,
        Triple(
            subject_id=source_table,
            predicate=predicate,
            object_kind="entity",
            object_id=target_table,
            confidence=1.0,
            source_chunks=[source_chunk],
        ),
    )


def _row_to_instance(
    table: StructuredTable,
    columns: list[StructuredColumn],
    row: dict[str, Any],
) -> OntologyInstance | None:
    pk = _primary_key(columns)
    if not pk or row.get(pk) is None:
        return None
    raw_id = str(row[pk])
    label = _label_column(columns)
    name = str(row.get(label) or raw_id) if label else raw_id
    return OntologyInstance(
        id=f"{table.table_name}:{raw_id}",
        name=name,
        class_id=table.table_name,
        attribute_values=[
            InstanceAttributeValue(
                attribute_name=column.column_name,
                value=_structured_scalar_value(row.get(column.column_name)),
                data_type=_data_type(column.data_type, row.get(column.column_name)),
                source_chunks=[f"structured:{table.table_name}"],
            )
            for column in columns
            if column.role not in {"pk", "fk", "label"}
            and row.get(column.column_name) is not None
        ],
        source_chunks=[f"structured:{table.table_name}"],
    )


def _add_fk_instance_links(
    metadata: StructuredMetadata,
    rows_by_table: dict[str, list[dict[str, Any]]],
    instance_by_table_pk: dict[tuple[str, str], str],
    instance_by_id: dict[str, OntologyInstance],
) -> None:
    for table_name, rows in rows_by_table.items():
        columns = metadata.columns.get(table_name, [])
        pk = _primary_key(columns)
        if not pk:
            continue
        for row in rows:
            source_id = instance_by_table_pk.get((table_name, str(row.get(pk))))
            source = instance_by_id.get(source_id or "")
            if source is None:
                continue
            for column in columns:
                if column.role != "fk" or not column.ref_table:
                    continue
                target_raw = row.get(column.column_name)
                target_id = instance_by_table_pk.get((column.ref_table, str(target_raw)))
                if target_id:
                    _add_instance_link(
                        source,
                        _predicate_from_fk(column, metadata.tables.get(column.ref_table)),
                        target_id,
                        f"structured:fk:{table_name}.{column.column_name}",
                    )


def _add_explicit_relation_links(
    config: StructuredBuildConfig,
    metadata: StructuredMetadata,
    repo: StructuredRepository,
    instance_by_raw_id: dict[str, str],
    instance_by_id: dict[str, OntologyInstance],
) -> None:
    relation = next(
        (
            item
            for item in metadata.relation_map
            if item.holder_table == "relations"
        ),
        None,
    )
    if relation is None or "relations" not in metadata.tables:
        return
    rows = repo.sample_relation_rows(
        relation.holder_table,
        "rel_type",
        relation.src_col,
        relation.dst_col,
        config.instances.relation_sample.per_relation_type,
    )
    for row in rows:
        source_id = instance_by_raw_id.get(str(row.get("src_id")))
        target_id = instance_by_raw_id.get(str(row.get("dst_id")))
        source = instance_by_id.get(source_id or "")
        if source is None or not target_id:
            continue
        _add_instance_link(
            source,
            str(row.get("rel_type") or relation.rel_type),
            target_id,
            "structured:relations",
        )


def _add_event_links(
    metadata: StructuredMetadata,
    rows_by_table: dict[str, list[dict[str, Any]]],
    instance_by_raw_id: dict[str, str],
    instance_by_id: dict[str, OntologyInstance],
) -> None:
    for table_name in _EVENT_TABLE_NAMES | {"quality_inspections"}:
        rows = rows_by_table.get(table_name) or []
        for row in rows:
            source_id = instance_by_raw_id.get(str(row.get("event_id") or row.get("inspection_id") or row.get("id")))
            source = instance_by_id.get(source_id or "")
            if source is None:
                continue
            for column in metadata.columns.get(table_name, []):
                value = row.get(column.column_name)
                if value is None:
                    continue
                if column.role == "fk" or column.column_name.endswith("_id"):
                    target_id = instance_by_raw_id.get(str(value))
                    if target_id:
                        _add_instance_link(source, _event_predicate(column.column_name), target_id, f"structured:event:{table_name}")
                    continue
                if column.column_name.endswith("_ids_json"):
                    for item in _json_array(value):
                        target_id = instance_by_raw_id.get(str(item))
                        if target_id:
                            _add_instance_link(source, _event_predicate(column.column_name), target_id, f"structured:event:{table_name}")


def _add_instance_link(
    source: OntologyInstance,
    predicate: str,
    target_id: str,
    source_chunk: str,
) -> None:
    if source.id == target_id:
        return
    key = (predicate, target_id)
    existing = {(link.predicate, link.target_instance_id) for link in source.link_values}
    if key in existing:
        return
    source.link_values.append(
        InstanceLinkValue(
            predicate=predicate,
            target_instance_id=target_id,
            confidence=1.0,
            source_chunks=[source_chunk],
        )
    )


def _sample_limit(config: StructuredBuildConfig, table: StructuredTable) -> int:
    if table.table_name in _EVENT_TABLE_NAMES:
        return config.instances.event_sample.per_event_type
    if table.kind == "reference":
        return config.instances.sample.per_reference_table
    if table.kind == "quality":
        return config.instances.sample.per_quality_table
    return config.instances.sample.per_entity_table


def _primary_key(columns: list[StructuredColumn]) -> str:
    for column in columns:
        if column.role == "pk":
            return column.column_name
    for column in columns:
        if column.column_name == "id":
            return column.column_name
    return ""


def _label_column(columns: list[StructuredColumn]) -> str:
    for column in columns:
        if column.role == "label":
            return column.column_name
    names = {column.column_name for column in columns}
    for fallback in _LABEL_FALLBACKS:
        if fallback in names:
            return fallback
    return ""


def _predicate_from_fk(column: StructuredColumn, target_table: StructuredTable | None) -> str:
    target_name = (target_table.description if target_table else "") or column.ref_table
    if target_name:
        return f"关联{target_name}"
    return column.column_name.replace("_id", "")


def _event_predicate(column_name: str) -> str:
    mapping = {
        "process_instance_id": "关联工序实例",
        "target_entity_id": "关联对象",
        "resource_ids_json": "使用资源",
        "input_entity_ids_json": "输入",
        "output_entity_ids_json": "输出",
    }
    return mapping.get(column_name, column_name.replace("_ids_json", "").replace("_id", ""))


def _data_type(data_type: str, value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    lowered = data_type.lower()
    if any(token in lowered for token in ("int", "serial")):
        return "integer"
    if any(token in lowered for token in ("double", "numeric", "real", "decimal")):
        return "number"
    if "bool" in lowered:
        return "boolean"
    return "string"


def _structured_scalar_value(value: Any) -> str | int | float | bool | None:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _json_array(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _split_csv(value: Any) -> list[str]:
    return [part.strip() for part in str(value or "").split(",") if part.strip()]


def _column_signals(column: StructuredColumn) -> list[str]:
    signals: list[str] = []
    name = column.column_name.lower()
    if column.role:
        signals.append(f"role:{column.role}")
    if name in {"id"} or name.endswith(("_id", "_code", "_no")):
        signals.append("id_like")
    if name in _LABEL_FALLBACKS or "name" in name or "label" in name:
        signals.append("label_like")
    if "time" in name or "date" in name or column.role == "timestamp":
        signals.append("time_like")
    if "status" in name or "state" in name or "lifecycle" in name:
        signals.append("status_like")
    if column.role == "measure" or any(token in column.data_type.lower() for token in ("int", "numeric", "double", "real", "decimal")):
        signals.append("measure_like")
    return list(dict.fromkeys(signals))


def _column_sample_stats(column_name: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    values = [row.get(column_name) for row in rows]
    non_null = [value for value in values if value is not None]
    samples: list[Any] = []
    for value in non_null:
        if value not in samples:
            samples.append(value)
        if len(samples) >= 5:
            break
    return {
        "sample_size": len(values),
        "null_count": len(values) - len(non_null),
        "distinct_count": len({json.dumps(value, ensure_ascii=False, sort_keys=True, default=str) for value in non_null}),
        "sample_values": samples,
    }


def _table_evidence(table: StructuredTable) -> list[str]:
    evidence = ["information_schema"]
    if table.description:
        evidence.append("table_description")
    if table.entity_type:
        evidence.append("meta_entity_type")
    if table.kind:
        evidence.append(f"meta_kind:{table.kind}")
    return evidence


def _column_evidence(column: StructuredColumn) -> list[str]:
    evidence = [f"role:{column.role}"]
    if column.description:
        evidence.append("column_description")
    if column.ref_table:
        evidence.append("foreign_key")
    return evidence


def _table_embedding_text(profile: dict[str, Any]) -> str:
    columns = profile.get("columns", [])
    column_text = " ".join(
        f"{column.get('column_name')} {column.get('description') or ''} {column.get('role') or ''}"
        for column in columns
    )
    return (
        f"table {profile.get('table_name', '')} "
        f"kind {profile.get('kind', '')} "
        f"description {profile.get('description', '')} "
        f"columns {column_text}"
    ).strip()


def _add_relation_candidate(
    relation_candidates: dict[tuple[str, str, str], dict[str, Any]],
    source_table: str,
    predicate: str,
    target_table: str,
    *,
    source_columns: list[str],
    target_columns: list[str],
    join: dict[str, Any],
    confidence: float,
    evidence: list[str],
) -> None:
    key = (source_table, predicate, target_table)
    current = relation_candidates.get(key)
    if current is not None and current.get("confidence", 0) >= confidence:
        return
    relation_candidates[key] = {
        "candidate_id": f"{source_table}:{predicate}:{target_table}",
        "predicate": predicate,
        "source_class": source_table,
        "target_class": target_table,
        "source_table": source_table,
        "target_table": target_table,
        "source_columns": [column for column in source_columns if column],
        "target_columns": [column for column in target_columns if column],
        "join": join,
        "confidence": confidence,
        "evidence": evidence,
        "embedding_text": f"{source_table} {predicate} {target_table} {' '.join(source_columns)} {' '.join(target_columns)}",
    }


def _guess_target_table(column_name: str, table_names: set[str]) -> str:
    stem = re.sub(r"_(id|code|no)$", "", column_name)
    candidates = {
        stem,
        f"{stem}s",
        f"{stem}es",
        stem.replace("_", ""),
    }
    for table_name in table_names:
        compact = table_name.replace("_", "")
        if table_name in candidates or compact in candidates:
            return table_name
    return ""


def _add_value_overlap_relation_candidates(
    metadata: StructuredMetadata,
    database_profile: dict[str, Any],
    relation_candidates: dict[tuple[str, str, str], dict[str, Any]],
    class_table_names: set[str],
) -> None:
    profile_by_table = {
        table["table_name"]: table for table in database_profile.get("tables", [])
    }
    for source_table in class_table_names:
        source_profile = profile_by_table.get(source_table, {})
        for source_column in source_profile.get("columns", []):
            source_name = source_column.get("column_name", "")
            if not source_name.endswith(("_id", "_code", "_no")):
                continue
            source_values = {
                str(value)
                for value in source_column.get("stats", {}).get("sample_values", [])
                if value is not None
            }
            if not source_values:
                continue
            for target_table in class_table_names:
                if target_table == source_table:
                    continue
                target_columns = metadata.columns.get(target_table, [])
                target_key = _primary_key(target_columns) or _label_column(target_columns)
                target_profile = profile_by_table.get(target_table, {})
                target_column_profile = next(
                    (
                        item
                        for item in target_profile.get("columns", [])
                        if item.get("column_name") == target_key
                    ),
                    None,
                )
                if target_column_profile is None:
                    continue
                target_values = {
                    str(value)
                    for value in target_column_profile.get("stats", {}).get("sample_values", [])
                    if value is not None
                }
                if not target_values:
                    continue
                overlap = len(source_values & target_values) / max(len(source_values), 1)
                if overlap < 0.6:
                    continue
                _add_relation_candidate(
                    relation_candidates,
                    source_table,
                    _predicate_from_fk(
                        StructuredColumn(
                            source_table,
                            source_name,
                            ref_table=target_table,
                        ),
                        metadata.tables.get(target_table),
                    ),
                    target_table,
                    source_columns=[source_name],
                    target_columns=[target_key],
                    join={
                        "type": "value_overlap",
                        "source_column": source_name,
                        "target_column": target_key,
                        "overlap": round(overlap, 4),
                    },
                    confidence=0.76,
                    evidence=["value_overlap"],
                )


def _run_async(awaitable):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(awaitable)
    raise RuntimeError("Hybrid structured construction cannot run inside an active event loop")


async def _complete_and_close_llm(
    llm_client: SchemaPolishClient,
    prompt: str,
    system_prompt: str,
) -> str:
    try:
        return await llm_client.complete(prompt, system_prompt=system_prompt)
    finally:
        aclose = getattr(llm_client, "aclose", None)
        if aclose is not None:
            await aclose()


def _cosine_similarity(vector_a: list[float], vector_b: list[float]) -> float:
    if len(vector_a) != len(vector_b):
        return 0.0
    dot = sum(a * b for a, b in zip(vector_a, vector_b))
    norm_a = sum(a * a for a in vector_a) ** 0.5
    norm_b = sum(b * b for b in vector_b) ** 0.5
    if not norm_a or not norm_b:
        return 0.0
    return dot / (norm_a * norm_b)


def _bounded_confidence(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, min(1.0, number))


def _loads_json_object(raw: str) -> dict[str, Any]:
    candidate = raw.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", candidate, re.DOTALL)
    if fenced:
        candidate = fenced.group(1).strip()
    if not candidate.startswith("{"):
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start >= 0 and end > start:
            candidate = candidate[start : end + 1]
    data = json.loads(candidate)
    if not isinstance(data, dict):
        raise ValueError("Expected a JSON object")
    return data


def _reject_plaintext_password(payload: Any) -> None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            if str(key).lower() == "password":
                raise ValueError("Plaintext database password is not allowed; use password_env")
            _reject_plaintext_password(value)
    elif isinstance(payload, list):
        for item in payload:
            _reject_plaintext_password(item)


def _quote_table_in_schema(table_name: str, config: StructuredBuildConfig) -> str:
    if "." in table_name:
        return _quote_qualified_identifier(table_name)
    return (
        f"{_quote_identifier(config.source.connection.schema_name)}."
        f"{_quote_identifier(table_name)}"
    )


def _quote_qualified_identifier(value: str) -> str:
    parts = value.split(".")
    if not 1 <= len(parts) <= 2:
        raise ValueError(f"Invalid table reference: {value}")
    return ".".join(_quote_identifier(part) for part in parts)


def _quote_identifier(value: str) -> str:
    if not _IDENTIFIER_RE.match(value):
        raise ValueError(f"Invalid identifier: {value}")
    return f'"{value}"'
