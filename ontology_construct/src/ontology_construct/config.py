from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class ChunkConfig(BaseModel):
    size: int = Field(default=1500, gt=0)
    overlap: int = Field(default=200, ge=0)


class LLMConfig(BaseModel):
    backend: str = "deepseek"
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-v4-pro"
    api_key_env: str = "DEEPSEEK_API_KEY"
    temperature: float = Field(default=0.1, ge=0)
    max_tokens: int = Field(default=4000, gt=0)
    timeout_seconds: int = Field(default=60, gt=0)
    max_retries: int = Field(default=3, ge=0)
    concurrency: int = Field(default=4, gt=0) #10
    # When set, sent as the `enable_thinking` request parameter. Leave as None to
    # omit it entirely for providers that do not recognise the flag.
    enable_thinking: bool | None = None
    # Extra top-level body fields merged into every chat/completions request.
    # Used for provider-specific parameters (e.g. Kimi's thinking toggle).
    extra_body: dict | None = None


class MergeConfig(BaseModel):
    embedding_backend: str = "qwen"
    embedding_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    embedding_model: str = "text-embedding-v4"
    embedding_api_key_env: str = "QWEN_EMBEDDING_API_KEY"
    similarity_threshold: float = Field(default=0.92, ge=0, le=1)
    use_llm_synonym_judgment: bool = False


class ConceptConfig(BaseModel):
    max_candidates_per_chunk: int = Field(default=10, gt=0)
    max_concepts: int = Field(default=50, gt=0)
    min_evidence: int = Field(default=1, ge=0)
    selection_score_threshold: float = Field(default=8.0, ge=0)
    selection_score_ratio: float = Field(default=0.8, ge=0, le=1)
    abstraction_level: str = "high"
    use_embedding_clustering: bool = False
    embedding_similarity_threshold: float = Field(default=0.9, ge=0, le=1)
    relation_vocab: list[str] = Field(
        default_factory=lambda: [
            "包含",
            "属于",
            "导致",
            "依赖",
            "触发",
            "产生",
            "影响",
            "约束",
            "输入",
            "输出",
            "关联",
        ]
    )
    candidate_prompt: str = "prompts/extract_concept_candidates.txt"
    generalize_prompt: str = "prompts/generalize_concepts.txt"
    relation_prompt: str = "prompts/build_concept_relations.txt"
    controlled_relation_build: bool = True
    instance_prompt: str = "prompts/extract_concept_instances.txt"
    chunk_instance_prompt: str = "prompts/extract_chunk_instances.txt"
    instance_relation_prompt: str = "prompts/build_instance_relations.txt"
    # Instance extraction strategy:
    #   "per_chunk"   — standard-style: read each source chunk in full and
    #                   extract its instances together with inter-instance links
    #                   (denser connectivity, fewer isolated nodes).
    #   "per_concept" — legacy: extract instances per concept batch from concept
    #                   evidence/source text, then rebuild links afterwards.
    instance_extraction_mode: str = "per_chunk"
    # per_chunk speedup: how many source chunks to extract instances for in a
    # single LLM call. >1 cuts the instance pass from N to N/k calls. Kept small
    # so each call stays well under the token ceiling (unlike folding instances
    # into the concept-extraction call, which truncated and hurt schema quality).
    instance_chunk_batch_size: int = Field(default=3, gt=0)
    # Instance extraction has a much higher burst-rate risk than concept
    # candidate extraction, so it can use a lower dedicated concurrency.
    instance_llm_concurrency: int = Field(default=4, gt=0)  # 5
    extract_real_instances: bool = False
    extract_instance_relations: bool = False
    max_instances_per_concept: int = Field(default=3, ge=0)
    instance_batch_size: int = Field(default=5, gt=0)
    instance_relation_batch_size: int = Field(default=30, gt=1)
    max_instance_relation_pairs: int = Field(default=800, gt=0)
    min_relation_pairs_per_instance: int = Field(default=3, ge=0)
    instance_evidence_per_concept: int = Field(default=3, ge=0)
    instance_evidence_char_limit: int = Field(default=240, gt=0)
    # B1: per-concept cap on raw source-chunk text fed into instance extraction
    # so the LLM mines instances from full text, not just evidence snippets.
    instance_source_char_limit: int = Field(default=4000, ge=0)
    # Per-instance cap on source text fed into instance-relation judging so the
    # LLM can confirm links from real context instead of tiny snippets.
    instance_relation_source_char_limit: int = Field(default=1000, ge=0)


class RefineConfig(BaseModel):
    enabled: bool = False
    top_k: int = Field(default=5, gt=0)


class OutputConfig(BaseModel):
    formats: list[str] = Field(default_factory=lambda: ["json"])


class ReplayConfig(BaseModel):
    enabled: bool = True


class QALoggingConfig(BaseModel):
    enabled: bool = True


class FrontendBuildConfig(BaseModel):
    default_profile: str = "default"
    default_backend: str = "jiutian"
    construction_mode: str = "schema-first"
    max_concepts: int = Field(default=50, gt=0)
    semantic_clustering: bool = False
    instance_mode: str = "both"
    # Generated placeholder instances are for explicit demo scenarios only.
    # Production document builds should rely on source-grounded instances.
    instance_count: int = Field(default=0, ge=0)
    qa_context_mode: str = "graph_rag"
    qa_fallback_enabled: bool = True
    llm_timeout: int | None = Field(default=None, gt=0)
    llm_retries: int | None = Field(default=None, ge=0)
    llm_concurrency: int | None = Field(default=None, gt=0)
    min_concept_evidence: int | None = Field(default=None, ge=0)
    abstraction_level: str | None = ""


class QAConfig(BaseModel):
    max_prompt_chars: int = Field(default=24000, gt=0)
    max_schema_items: int = Field(default=80, gt=0)
    max_evidence_nodes: int = Field(default=80, gt=0)
    max_evidence_edges: int = Field(default=120, gt=0)
    max_paths: int = Field(default=12, ge=0)
    ppr_alpha: float = Field(default=0.85, ge=0, le=1)
    ppr_iterations: int = Field(default=30, gt=0)
    hub_penalty_enabled: bool = True
    # For graph_qa_evidence_v1: whether to return the retrieval trace in the API
    # response meta. It never affects the prompt (train/inference must match).
    include_retrieval_trace: bool = True
    schema_guided_pruning: bool = True
    schema_type_bonus: float = Field(default=0.18, ge=0)
    schema_relation_bonus: float = Field(default=0.22, ge=0)

    # --- graph_qa_evidence_v1 contract (§10.1) --- #
    # Default stays on the legacy contract until P6 (§12): the new answer format
    # (<think></think><answer>JSON</answer>) needs the retrained model. Opt in with
    # evidence_contract="graph_qa_evidence_v1".
    evidence_contract: str = "graph_rag_v1"  # or "graph_qa_evidence_v1" (new, post-retrain)
    max_anchors: int = Field(default=6, gt=0)
    max_anchor_pairs: int = Field(default=6, gt=0)
    max_paths_per_pair: int = Field(default=3, gt=0)
    path_max_depth: int = Field(default=6, gt=0)
    impact_bfs_depth: int = Field(default=4, gt=0)
    max_chains: int = Field(default=8, gt=0)
    max_full_chains: int = Field(default=5, gt=0)
    max_relation_lines_per_anchor: int = Field(default=12, gt=0)
    max_relation_lines_relation_intent: int = Field(default=24, gt=0)
    aggregate_example_count: int = Field(default=5, gt=0)
    max_attrs_per_node: int = Field(default=6, gt=0)
    max_same_type_candidates: int = Field(default=10, gt=0)
    supporting_node_limit: int = Field(default=60, gt=0)
    supporting_edge_limit: int = Field(default=80, gt=0)
    budget_plan_chars: int = Field(default=1500, gt=0)
    budget_core_chars: int = Field(default=9000, gt=0)
    budget_schema_chars: int = Field(default=3000, gt=0)
    budget_edges_chars: int = Field(default=4500, gt=0)
    budget_nodes_chars: int = Field(default=4500, gt=0)


class InstancePostprocessConfig(BaseModel):
    enabled: bool = True
    min_instances: int = Field(default=2, ge=0)
    max_llm_instances: int = Field(default=80, gt=0)
    max_anchor_instances: int = Field(default=40, gt=0)
    max_drop_ratio: float = Field(default=0.25, ge=0, le=1)
    min_link_confidence: float = Field(default=0.55, ge=0, le=1)
    fallback_predicate: str = "语义关联"
    source_tag: str = "instance_postprocess"


class InstanceBuilderConfig(BaseModel):
    enabled: bool = True
    mode: str = "hybrid"
    max_instances_per_type: int = Field(default=200, gt=0)
    max_log_records_per_file: int = Field(default=2000, gt=0)
    max_log_records_per_template: int = Field(default=50, gt=0)
    llm_enabled_for_unresolved: bool = True
    llm_chunk_batch_size: int = Field(default=2, gt=0)
    llm_concurrency: int = Field(default=3, gt=0)
    source_text_char_limit: int = Field(default=4000, gt=0)


class ActionBuilderConfig(BaseModel):
    # Deterministic action layer: derive Palantir-style actions (type + event)
    # from instances whose object type denotes an event/action, so the action
    # layer stops being an empty shell. Event objects are kept untouched; the
    # actions are added on top (non-destructive).
    enabled: bool = True
    # An instance is an action source when its object type name contains any of
    # these keywords. Kept narrow by default ("事件") to avoid false positives
    # such as "检测设备"; extend per profile via OntologyProfileConfig.action_vocab.
    event_type_keywords: list[str] = Field(default_factory=lambda: ["事件"])
    # Event out-links whose predicate marks the action's actor (input side).
    actor_predicates: list[str] = Field(
        default_factory=lambda: ["输入", "消耗", "使用", "来源于", "执行于"]
    )
    # Event out-links whose predicate marks the action's target (output side).
    target_predicates: list[str] = Field(
        default_factory=lambda: ["输出", "产出", "生成", "流转到", "处理", "影响"]
    )
    # Tie-breaker for generic links: a participant whose object type name matches
    # one of these is treated as the action target (the thing acted upon).
    target_type_keywords: list[str] = Field(
        default_factory=lambda: [
            "工序",
            "工单",
            "实体",
            "缺陷",
            "返工",
            "产品",
            "物料",
            "钢卷",
            "冲压件",
        ]
    )
    # Suffixes stripped from an event type name to derive a verb-style action name
    # (e.g. "工序启动事件" -> "工序启动").
    name_strip_suffixes: list[str] = Field(default_factory=lambda: ["事件"])
    source_tag: str = "action_builder"


class BuildQualityConfig(BaseModel):
    """Workflow-level completeness and publication policy.

    Provider clients already retry individual HTTP requests. These retries are
    an additional workflow pass over failed chunks/batches, which is useful for
    long structured extraction responses that occasionally time out.
    """

    chunk_retry_rounds: int = Field(default=1, ge=0, le=3)
    instance_retry_rounds: int = Field(default=1, ge=0, le=3)
    require_all_chunks: bool = True
    complete_standard_instances: bool = True
    quarantine_orphan_entities: bool = True
    warn_on_uncovered_entities: bool = True


class OntologyProfileConfig(BaseModel):
    ontology_name: str = "通用本体"
    ontology_desc: str = "从长文本中抽取的通用本体结构"
    prompt: str = "prompts/extract_ontology.txt"
    # Optional path (relative to project root) to a domain-knowledge / modeling
    # guidance file. When set, its content is injected into both standard and
    # schema-first prompts via the supplemental channel so the LLM applies the
    # scenario's class/relation/attribute conventions.
    domain_prompt: str = ""
    # Optional schema-first relation vocabulary override. Empty means fall back
    # to the global concept.relation_vocab.
    relation_vocab: list[str] = Field(default_factory=list)
    # Optional extra object-type name keywords that mark an instance as an action
    # source, merged into action_builder.event_type_keywords for this profile.
    action_vocab: list[str] = Field(default_factory=list)


class AppConfig(BaseModel):
    chunk: ChunkConfig = Field(default_factory=ChunkConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    merge: MergeConfig = Field(default_factory=MergeConfig)
    concept: ConceptConfig = Field(default_factory=ConceptConfig)
    refine: RefineConfig = Field(default_factory=RefineConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)
    replay: ReplayConfig = Field(default_factory=ReplayConfig)
    qa_logging: QALoggingConfig = Field(default_factory=QALoggingConfig)
    frontend_build: FrontendBuildConfig = Field(default_factory=FrontendBuildConfig)
    qa: QAConfig = Field(default_factory=QAConfig)
    instance_postprocess: InstancePostprocessConfig = Field(
        default_factory=InstancePostprocessConfig
    )
    instance_builder: InstanceBuilderConfig = Field(
        default_factory=InstanceBuilderConfig
    )
    action_builder: ActionBuilderConfig = Field(
        default_factory=ActionBuilderConfig
    )
    build_quality: BuildQualityConfig = Field(default_factory=BuildQualityConfig)
    ontology_profiles: dict[str, OntologyProfileConfig] = Field(
        default_factory=lambda: {
            "default": OntologyProfileConfig(),
        }
    )

    def get_profile(self, name: str | None = None) -> OntologyProfileConfig:
        profile_name = name or "default"
        if profile_name not in self.ontology_profiles:
            available = ", ".join(sorted(self.ontology_profiles))
            raise ValueError(
                f"Unknown ontology profile: {profile_name}. Available: {available}"
            )
        return self.ontology_profiles[profile_name]


def load_config(path: Path | str | None = None) -> AppConfig:
    config_path = Path(path) if path else default_config_path()
    with config_path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return AppConfig.model_validate(data)


def profile_domain_text(profile_config: OntologyProfileConfig) -> str:
    """Read a profile's domain-guidance file, if configured. Returns "" when
    unset or missing so callers can treat it as optional."""
    raw = (profile_config.domain_prompt or "").strip()
    if not raw:
        return ""
    path = Path(raw)
    if not path.is_absolute():
        path = project_root() / path
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()


def default_config_path() -> Path:
    return Path(__file__).resolve().parents[2] / "configs" / "default.yaml"


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def load_project_env() -> None:
    load_env_file(project_root() / ".env")


def load_env_file(path: Path | str) -> None:
    env_path = Path(path)
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        os.environ[key] = _parse_env_value(value.strip())


def _parse_env_value(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value
