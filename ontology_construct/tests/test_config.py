from ontology_construct.config import load_config, load_env_file, profile_domain_text


def test_load_default_config() -> None:
    config = load_config()

    assert config.chunk.size == 1500
    assert config.chunk.overlap == 200
    assert config.llm.backend == "qwen"
    assert config.llm.base_url == "https://dashscope.aliyuncs.com/compatible-mode/v1"
    assert config.llm.model == "qwen-plus"
    assert config.llm.api_key_env == "DASHSCOPE_API_KEY"
    assert config.llm.enable_thinking is False
    assert config.merge.embedding_backend == "qwen"
    assert config.merge.embedding_model == "qwen3.7-text-embedding"
    assert config.merge.embedding_api_key_env == "QWEN_EMBEDDING_API_KEY"
    assert config.replay.enabled is True
    assert config.qa_logging.enabled is True
    assert config.concept.max_candidates_per_chunk == 10
    assert config.concept.max_concepts == 50
    assert config.concept.selection_score_threshold == 8.0
    assert config.concept.selection_score_ratio == 0.0
    assert config.concept.abstraction_level == "high"
    assert config.concept.controlled_relation_build is True
    assert config.concept.relation_prompt == "prompts/build_concept_relations.txt"
    assert config.concept.extract_real_instances is False
    assert config.concept.extract_instance_relations is False
    assert config.concept.max_instances_per_concept == 15
    assert config.concept.instance_batch_size == 3
    assert config.concept.instance_relation_batch_size == 30
    assert config.concept.max_instance_relation_pairs == 800
    assert config.concept.min_relation_pairs_per_instance == 3
    assert config.concept.instance_evidence_per_concept == 10
    assert config.concept.instance_evidence_char_limit == 600
    assert config.concept.instance_source_char_limit == 4000
    assert config.concept.instance_relation_source_char_limit == 1000
    assert config.concept.instance_prompt == "prompts/extract_concept_instances.txt"
    assert config.concept.chunk_instance_prompt == "prompts/extract_chunk_instances.txt"
    assert config.concept.instance_extraction_mode == "per_chunk"
    assert config.concept.instance_chunk_batch_size == 3
    assert config.concept.instance_llm_concurrency == 5
    assert config.concept.instance_relation_prompt == "prompts/build_instance_relations.txt"
    assert config.frontend_build.default_profile == "default"
    assert config.frontend_build.default_backend == "deepseek"
    assert config.frontend_build.construction_mode == "schema-first"
    assert config.frontend_build.max_concepts == 50
    assert config.frontend_build.semantic_clustering is True
    assert config.frontend_build.instance_mode == "real"
    assert config.frontend_build.instance_count == 0
    assert config.frontend_build.qa_context_mode == "graph_rag"
    assert config.frontend_build.qa_fallback_enabled is True
    assert config.qa.max_prompt_chars == 24000
    assert config.qa.max_schema_items == 80
    assert config.qa.max_evidence_nodes == 80
    assert config.qa.max_evidence_edges == 120
    assert config.qa.max_paths == 12
    assert config.qa.ppr_alpha == 0.85
    assert config.qa.ppr_iterations == 30
    assert config.qa.hub_penalty_enabled is True
    assert config.qa.include_retrieval_trace is True
    assert config.qa.schema_guided_pruning is True
    assert config.qa.schema_type_bonus == 0.18
    assert config.qa.schema_relation_bonus == 0.22
    assert config.instance_builder.enabled is True
    assert config.instance_builder.mode == "hybrid"
    assert config.instance_builder.max_instances_per_type == 200
    assert config.instance_builder.max_log_records_per_file == 2000
    assert config.instance_builder.llm_chunk_batch_size == 2
    assert config.build_quality.chunk_retry_rounds == 1
    assert config.build_quality.instance_retry_rounds == 1
    assert config.build_quality.require_all_chunks is True
    assert config.build_quality.complete_standard_instances is True
    assert "导致" in config.concept.relation_vocab
    assert config.refine.enabled is False
    assert config.get_profile("business_process").ontology_name == "业务流程本体"
    industrial_profile = config.get_profile("industrial_process")
    assert industrial_profile.ontology_name == "工业制造流程本体"
    assert industrial_profile.domain_prompt == "prompts/industrial_process_background.txt"
    assert "执行于" in industrial_profile.relation_vocab
    assert "机床加工" in profile_domain_text(industrial_profile)


def test_load_config_reads_ontology_profiles(tmp_path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
ontology_profiles:
  default:
    ontology_name: 默认本体
    ontology_desc: 默认描述
    prompt: prompts/extract_ontology.txt
  people_relation:
    ontology_name: 人物关系本体
    ontology_desc: 人物关系描述
    prompt: prompts/people_relation_ontology.txt
""",
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.get_profile("people_relation").ontology_name == "人物关系本体"


def test_load_env_file_sets_missing_values(tmp_path, monkeypatch) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        """
        # local secrets
        DEEPSEEK_API_KEY="deepseek-key"
        export QWEN_EMBEDDING_API_KEY='qwen-key'
        """,
        encoding="utf-8",
    )
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("QWEN_EMBEDDING_API_KEY", raising=False)

    load_env_file(env_path)

    assert __import__("os").environ["DEEPSEEK_API_KEY"] == "deepseek-key"
    assert __import__("os").environ["QWEN_EMBEDDING_API_KEY"] == "qwen-key"


def test_load_env_file_does_not_override_existing_env(tmp_path, monkeypatch) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("DEEPSEEK_API_KEY=from-file", encoding="utf-8")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "from-shell")

    load_env_file(env_path)

    assert __import__("os").environ["DEEPSEEK_API_KEY"] == "from-shell"


def test_load_env_file_fills_blank_existing_env(tmp_path, monkeypatch) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("QWEN_EMBEDDING_API_KEY=from-file", encoding="utf-8")
    monkeypatch.setenv("QWEN_EMBEDDING_API_KEY", "")

    load_env_file(env_path)

    assert __import__("os").environ["QWEN_EMBEDDING_API_KEY"] == "from-file"
