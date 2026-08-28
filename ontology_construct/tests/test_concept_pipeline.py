import asyncio

from ontology_construct.concept import ConceptCandidateExtractor, ConceptGeneralizer
from ontology_construct.config import ConceptConfig
from ontology_construct.llm import MockLLMClient
from ontology_construct.models import Chunk


def test_concept_candidate_extractor_parses_controlled_relations() -> None:
    chunk = Chunk(
        id="chunk_0001",
        text="设备故障会触发维护工单。",
        start_char=0,
        end_char=12,
        token_count=12,
    )
    client = MockLLMClient(
        """
        {
          "concepts": [
            {"name": "设备故障", "definition": "设备异常状态", "evidence": ["设备故障"]},
            {"name": "维护工单", "definition": "维护处理记录", "evidence": ["维护工单"]}
          ],
          "relations": [
            {"source": "设备故障", "predicate": "触发", "target": "维护工单"}
          ]
        }
        """
    )

    candidates = asyncio.run(
        ConceptCandidateExtractor(client, ConceptConfig()).extract(chunk)
    )

    assert [concept.name for concept in candidates.concepts] == ["设备故障", "维护工单"]
    assert candidates.relations[0].predicate == "触发"


def test_concept_generalizer_builds_global_ontology_from_candidates() -> None:
    chunk = Chunk(
        id="chunk_0001",
        text="设备故障会触发维护工单。",
        start_char=0,
        end_char=12,
        token_count=12,
    )
    extractor_client = MockLLMClient(
        """
        {
          "concepts": [
            {"name": "设备故障", "definition": "设备异常状态", "evidence": ["设备故障"]},
            {"name": "维护工单", "definition": "维护处理记录", "evidence": ["维护工单"]}
          ],
          "relations": [
            {"source": "设备故障", "predicate": "触发", "target": "维护工单"}
          ]
        }
        """
    )
    candidates = asyncio.run(
        ConceptCandidateExtractor(extractor_client, ConceptConfig()).extract(chunk)
    )
    generalizer = ConceptGeneralizer(MockLLMClient(), ConceptConfig(max_concepts=10))

    ontology = asyncio.run(generalizer.generalize([candidates]))

    assert [entity.name for entity in ontology.entities.values()] == [
        "设备故障",
        "维护工单",
    ]
    assert ontology.triples[0].predicate == "触发"

    artifacts = asyncio.run(generalizer.generalize_with_artifacts([candidates]))
    assert artifacts.relation_report.mode == "candidate_fallback"
    assert artifacts.relation_report.relations[0].predicate == "触发"


def test_concept_generalizer_reports_clusters_selection_and_quality() -> None:
    chunk = Chunk(
        id="chunk_0001",
        text="设备故障、设备故障类型和维护工单。",
        start_char=0,
        end_char=16,
        token_count=16,
    )
    extractor_client = MockLLMClient(
        """
        {
          "concepts": [
            {"name": "设备故障", "definition": "设备异常状态", "evidence": ["设备故障"]},
            {"name": "设备故障类型", "definition": "设备故障的分类", "evidence": ["设备故障类型"]},
            {"name": "临时编号A12", "definition": "一次性编号", "evidence": []}
          ],
          "relations": [
            {"source": "设备故障", "predicate": "触发", "target": "设备故障类型"}
          ]
        }
        """
    )
    candidates = asyncio.run(
        ConceptCandidateExtractor(extractor_client, ConceptConfig()).extract(chunk)
    )
    generalizer = ConceptGeneralizer(
        MockLLMClient(),
        ConceptConfig(max_concepts=5, min_evidence=1),
    )

    artifacts = asyncio.run(generalizer.generalize_with_artifacts([candidates]))

    assert artifacts.quality.candidate_concept_count == 3
    assert artifacts.quality.cluster_count == 2
    assert artifacts.quality.selected_concept_count == 1
    assert artifacts.selection.selected[0].merged_names == ["设备故障", "设备故障类型"]
    assert artifacts.selection.rejected[0].canonical_name == "临时编号A12"
    assert "疑似具体实例" in artifacts.selection.rejected[0].reject_reasons


def test_concept_generalizer_treats_max_concepts_as_cap_not_target() -> None:
    chunk = Chunk(
        id="chunk_0001",
        text="设备故障会触发维护工单，临时标签偶尔出现。",
        start_char=0,
        end_char=22,
        token_count=22,
    )
    extractor_client = MockLLMClient(
        """
        {
          "concepts": [
            {"name": "设备故障", "definition": "设备异常状态", "evidence": ["设备故障"], "abstraction_level": "high"},
            {"name": "维护工单", "definition": "维护处理记录", "evidence": ["维护工单"], "abstraction_level": "high"},
            {"name": "临时标签", "definition": "局部标签", "evidence": [], "abstraction_level": "low"}
          ],
          "relations": [
            {"source": "设备故障", "predicate": "触发", "target": "维护工单"}
          ]
        }
        """
    )
    candidates = asyncio.run(
        ConceptCandidateExtractor(extractor_client, ConceptConfig()).extract(chunk)
    )
    generalizer = ConceptGeneralizer(
        MockLLMClient(),
        ConceptConfig(max_concepts=30, selection_score_threshold=8.0),
    )

    artifacts = asyncio.run(generalizer.generalize_with_artifacts([candidates]))

    assert artifacts.quality.max_concepts == 30
    assert artifacts.quality.selected_concept_count == 2
    assert [entity.name for entity in artifacts.ontology.entities.values()] == [
        "设备故障",
        "维护工单",
    ]
    assert artifacts.selection.rejected[0].canonical_name == "临时标签"
    assert "低于概念评分阈值" in artifacts.selection.rejected[0].reject_reasons


def test_concept_generalizer_semantic_embedding_clustering_merges_related_names() -> None:
    class StaticEmbeddingClient:
        async def embed_texts(self, texts: list[str]) -> list[list[float]]:
            assert "客户画像" in texts[0]
            assert "用户分群" in texts[1]
            assert "维护工单" in texts[2]
            return [
                [1.0, 0.0],
                [0.99, 0.01],
                [0.0, 1.0],
            ]

    chunk = Chunk(
        id="chunk_0001",
        text="客户画像、用户分群和维护工单。",
        start_char=0,
        end_char=15,
        token_count=15,
    )
    extractor_client = MockLLMClient(
        """
        {
          "concepts": [
            {"name": "客户画像", "definition": "客户特征集合", "evidence": ["客户画像"], "abstraction_level": "high"},
            {"name": "用户分群", "definition": "用户特征分组", "evidence": ["用户分群"], "abstraction_level": "high"},
            {"name": "维护工单", "definition": "维护处理记录", "evidence": ["维护工单"], "abstraction_level": "high"}
          ],
          "relations": []
        }
        """
    )
    candidates = asyncio.run(
        ConceptCandidateExtractor(extractor_client, ConceptConfig()).extract(chunk)
    )
    generalizer = ConceptGeneralizer(
        MockLLMClient(),
        ConceptConfig(
            max_concepts=10,
            selection_score_threshold=0,
            selection_score_ratio=0,
            use_embedding_clustering=True,
            embedding_similarity_threshold=0.95,
            extract_real_instances=False,
        ),
        embedding_client=StaticEmbeddingClient(),
    )

    artifacts = asyncio.run(generalizer.generalize_with_artifacts([candidates]))

    assert artifacts.quality.semantic_cluster_merge_count == 1
    assert artifacts.quality.cluster_count == 2
    merged = next(
        cluster
        for cluster in artifacts.selection.selected
        if cluster.canonical_name == "客户画像"
    )
    assert merged.merged_names == ["客户画像", "用户分群"]
    assert "embedding 语义合并" in merged.keep_reasons


def test_concept_generalizer_stops_before_max_when_tail_scores_drop() -> None:
    chunk = Chunk(
        id="chunk_0001",
        text="核心概念反复出现，边缘概念偶尔出现。",
        start_char=0,
        end_char=18,
        token_count=18,
    )
    concepts = [
        {
            "name": "核心概念",
            "definition": "核心业务概念",
            "evidence": [f"核心证据{i}" for i in range(10)],
            "abstraction_level": "high",
            "aliases": [f"核心别名{i}" for i in range(6)],
        },
        {
            "name": "边缘概念",
            "definition": "边缘业务概念",
            "evidence": ["边缘证据"],
            "abstraction_level": "high",
        },
    ]
    extractor_client = MockLLMClient(
        __import__("json").dumps({"concepts": concepts, "relations": []}, ensure_ascii=False)
    )
    candidates = asyncio.run(
        ConceptCandidateExtractor(extractor_client, ConceptConfig()).extract(chunk)
    )
    generalizer = ConceptGeneralizer(
        MockLLMClient(),
        ConceptConfig(max_concepts=30, selection_score_threshold=8.0),
    )

    artifacts = asyncio.run(generalizer.generalize_with_artifacts([candidates]))

    assert artifacts.quality.selected_concept_count == 1
    assert artifacts.selection.selected[0].canonical_name == "核心概念"
    assert artifacts.selection.rejected[0].canonical_name == "边缘概念"
    assert "低于动态概念评分阈值" in artifacts.selection.rejected[0].reject_reasons


def test_concept_generalizer_uses_controlled_relation_build() -> None:
    chunk = Chunk(
        id="chunk_0001",
        text="设备故障会触发维护工单。",
        start_char=0,
        end_char=12,
        token_count=12,
    )
    extractor_client = MockLLMClient(
        """
        {
          "concepts": [
            {"name": "设备故障", "definition": "设备异常状态", "evidence": ["设备故障"]},
            {"name": "维护工单", "definition": "维护处理记录", "evidence": ["维护工单"]}
          ],
          "relations": [
            {"source": "设备故障", "predicate": "触发", "target": "维护工单"}
          ]
        }
        """
    )
    candidates = asyncio.run(
        ConceptCandidateExtractor(extractor_client, ConceptConfig()).extract(chunk)
    )
    relation_client = MockLLMClient(
        """
        {
          "concepts": [
            {"name": "设备故障", "definition": "设备异常状态", "evidence": ["设备故障"]},
            {"name": "维护工单", "definition": "维护处理记录", "evidence": ["维护工单"]}
          ],
          "relations": [
            {"source": "设备故障", "predicate": "影响", "target": "维护工单"}
          ]
        }
        """
    )
    generalizer = ConceptGeneralizer(
        relation_client,
        ConceptConfig(max_concepts=10, controlled_relation_build=True),
    )

    artifacts = asyncio.run(generalizer.generalize_with_artifacts([candidates]))

    assert artifacts.relation_report.mode == "controlled_llm"
    assert artifacts.relation_report.relations[0].predicate == "影响"
    assert artifacts.ontology.triples[0].predicate == "影响"


def test_concept_generalizer_extracts_real_instances() -> None:
    chunk = Chunk(
        id="chunk_0001",
        text="TS-100 传感器发生设备故障。",
        start_char=0,
        end_char=18,
        token_count=18,
    )
    extractor_client = MockLLMClient(
        """
        {
          "concepts": [
            {
              "name": "设备故障",
              "definition": "设备异常状态",
              "evidence": ["TS-100 传感器发生设备故障。"],
              "abstraction_level": "high"
            }
          ],
          "relations": []
        }
        """
    )
    candidates = asyncio.run(
        ConceptCandidateExtractor(extractor_client, ConceptConfig()).extract(chunk)
    )
    instance_client = MockLLMClient(
        """
        {
          "concepts": [
            {
              "name": "设备故障",
              "definition": "设备异常状态",
              "evidence": ["TS-100 传感器发生设备故障。"],
              "abstraction_level": "high"
            }
          ],
          "relations": [],
          "instances": [
            {
              "concept": "设备故障",
              "name": "TS-100 传感器故障",
              "attributes": {"设备": "TS-100 传感器"},
              "links": [
                {
                  "target": "维修工单 WO-1",
                  "predicate": "触发",
                  "evidence": ["TS-100 传感器发生设备故障并触发维修工单 WO-1。"]
                }
              ],
              "evidence": ["TS-100 传感器发生设备故障。"]
            },
            {
              "concept": "设备故障",
              "name": "维修工单 WO-1",
              "attributes": {"编号": "WO-1"},
              "evidence": ["触发维修工单 WO-1。"]
            }
          ]
        }
        """
    )
    generalizer = ConceptGeneralizer(
        instance_client,
        ConceptConfig(
            max_concepts=10,
            controlled_relation_build=True,
            extract_real_instances=True,
            max_instances_per_concept=2,
        ),
    )

    artifacts = asyncio.run(generalizer.generalize_with_artifacts([candidates]))

    assert artifacts.instance_report.mode == "real_llm"
    assert artifacts.instance_report.instances[0].name == "TS-100 传感器故障"
    assert artifacts.ontology.instances[0].generated is False
    assert artifacts.ontology.instances[0].attribute_values[0].attribute_name == "设备"
    assert artifacts.ontology.instances[0].link_values[0].predicate == "触发"
    assert artifacts.ontology.instances[0].link_values[0].target_instance_id == "real_inst_000002"


def test_concept_generalizer_builds_relations_between_real_instances() -> None:
    chunk = Chunk(
        id="chunk_0001",
        text="2025活动包含10元权益礼包。",
        start_char=0,
        end_char=16,
        token_count=16,
    )
    extractor_client = MockLLMClient(
        """
        {
          "concepts": [
            {"name": "营销活动", "definition": "业务推广活动", "evidence": ["2025活动"], "abstraction_level": "high"},
            {"name": "权益礼包", "definition": "活动权益", "evidence": ["10元权益礼包"], "abstraction_level": "high"}
          ],
          "relations": [
            {"source": "营销活动", "predicate": "包含", "target": "权益礼包"}
          ]
        }
        """
    )
    candidates = asyncio.run(
        ConceptCandidateExtractor(extractor_client, ConceptConfig()).extract(chunk)
    )

    class RoutingClient:
        async def complete(self, prompt: str, *, system_prompt: str | None = None) -> str:
            if "本体实例抽取专家" in prompt:
                return """
                {
                  "instances": [
                    {"concept": "营销活动", "name": "2025活动", "evidence": ["2025活动包含10元权益礼包。"]},
                    {"concept": "权益礼包", "name": "10元权益礼包", "evidence": ["2025活动包含10元权益礼包。"]}
                  ]
                }
                """
            if "本体实例关系判定专家" in prompt:
                return """
                {
                  "relations": [
                    {"source": "2025活动", "predicate": "包含", "target": "10元权益礼包", "evidence": ["2025活动包含10元权益礼包。"]}
                  ]
                }
                """
            return """
            {
              "concepts": [
                {"name": "营销活动", "definition": "业务推广活动", "evidence": ["2025活动"], "abstraction_level": "high"},
                {"name": "权益礼包", "definition": "活动权益", "evidence": ["10元权益礼包"], "abstraction_level": "high"}
              ],
              "relations": [
                {"source": "营销活动", "predicate": "包含", "target": "权益礼包"}
              ]
            }
            """

    generalizer = ConceptGeneralizer(
        RoutingClient(),
        ConceptConfig(
            max_concepts=10,
            controlled_relation_build=True,
            extract_real_instances=True,
            extract_instance_relations=True,
            max_instances_per_concept=2,
        ),
    )

    artifacts = asyncio.run(generalizer.generalize_with_artifacts([candidates]))

    assert artifacts.instance_report.relation_mode == "instance_relation_llm_with_fallback"
    assert artifacts.instance_report.relation_count == 1
    assert artifacts.instance_report.relation_fallback_count == 1
    activity = next(instance for instance in artifacts.ontology.instances if instance.name == "2025活动")
    assert activity.link_values[0].predicate == "包含"
    assert activity.link_values[0].target_instance_id == "real_inst_000002"


def test_concept_generalizer_falls_back_when_instance_relation_llm_fails() -> None:
    chunk = Chunk(
        id="chunk_0001",
        text="2025活动包含10元权益礼包。",
        start_char=0,
        end_char=16,
        token_count=16,
    )
    extractor_client = MockLLMClient(
        """
        {
          "concepts": [
            {"name": "营销活动", "definition": "业务推广活动", "evidence": ["2025活动"], "abstraction_level": "high"},
            {"name": "权益礼包", "definition": "活动权益", "evidence": ["10元权益礼包"], "abstraction_level": "high"}
          ],
          "relations": [
            {"source": "营销活动", "predicate": "包含", "target": "权益礼包"}
          ]
        }
        """
    )
    candidates = asyncio.run(
        ConceptCandidateExtractor(extractor_client, ConceptConfig()).extract(chunk)
    )

    class FailingRelationClient:
        async def complete(self, prompt: str, *, system_prompt: str | None = None) -> str:
            if "本体实例抽取专家" in prompt:
                return """
                {
                  "instances": [
                    {"concept": "营销活动", "name": "2025活动", "evidence": ["2025活动包含10元权益礼包。"]},
                    {"concept": "权益礼包", "name": "10元权益礼包", "evidence": ["2025活动包含10元权益礼包。"]}
                  ]
                }
                """
            if "本体实例关系判定专家" in prompt:
                raise TimeoutError("timed out")
            return """
            {
              "concepts": [
                {"name": "营销活动", "definition": "业务推广活动", "evidence": ["2025活动"], "abstraction_level": "high"},
                {"name": "权益礼包", "definition": "活动权益", "evidence": ["10元权益礼包"], "abstraction_level": "high"}
              ],
              "relations": [
                {"source": "营销活动", "predicate": "包含", "target": "权益礼包"}
              ]
            }
            """

    generalizer = ConceptGeneralizer(
        FailingRelationClient(),
        ConceptConfig(
            max_concepts=10,
            controlled_relation_build=True,
            extract_real_instances=True,
            extract_instance_relations=True,
            max_instances_per_concept=2,
        ),
    )

    artifacts = asyncio.run(generalizer.generalize_with_artifacts([candidates]))

    assert artifacts.instance_report.relation_mode == "heuristic_fallback"
    assert artifacts.instance_report.relation_count == 1
    activity = next(instance for instance in artifacts.ontology.instances if instance.name == "2025活动")
    assert activity.link_values[0].predicate == "包含"


def test_concept_generalizer_batches_instance_extraction_and_truncates_evidence() -> None:
    long_evidence = "A" * 500
    chunk = Chunk(
        id="chunk_0001",
        text="多个概念包含真实实例。",
        start_char=0,
        end_char=10,
        token_count=10,
    )
    concepts = [
        {
            "name": f"概念{i}",
            "definition": f"概念{i}定义",
            "evidence": [long_evidence],
            "abstraction_level": "high",
        }
        for i in range(1, 4)
    ]
    extractor_client = MockLLMClient(
        __import__("json").dumps({"concepts": concepts, "relations": []}, ensure_ascii=False)
    )
    candidates = asyncio.run(
        ConceptCandidateExtractor(extractor_client, ConceptConfig()).extract(chunk)
    )

    class RecordingClient:
        def __init__(self):
            self.prompts = []

        async def complete(self, prompt: str, *, system_prompt: str | None = None) -> str:
            self.prompts.append(prompt)
            return '{"instances": []}'

    client = RecordingClient()
    generalizer = ConceptGeneralizer(
        client,
        ConceptConfig(
            max_concepts=10,
            controlled_relation_build=False,
            extract_real_instances=True,
            selection_score_threshold=0,
            selection_score_ratio=0,
            instance_batch_size=2,
            instance_evidence_char_limit=20,
        ),
    )

    artifacts = asyncio.run(generalizer.generalize_with_artifacts([candidates]))

    assert artifacts.instance_report.mode == "real_llm"
    instance_prompts = [
        prompt for prompt in client.prompts if "本体实例抽取专家" in prompt
    ]
    assert len(instance_prompts) == 2
    assert "AAAAAAAAAAAAAAAAAAAA..." in instance_prompts[0]
    assert long_evidence not in instance_prompts[0]


def test_concept_generalizer_per_chunk_extraction_links_co_occurring_instances() -> None:
    from ontology_construct.concept.models import ConceptCluster

    # per_chunk mode reads the full chunk and extracts instances + inline links
    # together, so co-occurring instances are connected without the heuristic
    # relation-rebuild pass.
    instance_client = MockLLMClient(
        """
        {
          "instances": [
            {
              "concept": "客户",
              "name": "张三",
              "attributes": {},
              "links": [
                {"target": "59元套餐", "predicate": "订购", "evidence": ["张三订购59元套餐"]}
              ],
              "evidence": ["客户张三"]
            },
            {
              "concept": "套餐",
              "name": "59元套餐",
              "attributes": {"月费": "59"},
              "links": [],
              "evidence": ["59元套餐"]
            }
          ]
        }
        """
    )
    config = ConceptConfig(
        instance_extraction_mode="per_chunk",
        extract_real_instances=True,
        max_instances_per_concept=15,
        extract_instance_relations=False,
    )
    generalizer = ConceptGeneralizer(
        instance_client,
        config,
        chunk_texts={"f1_c0": "客户张三订购了59元套餐。"},
    )
    clusters = {
        "k_cust": ConceptCluster(
            cluster_id="c1", canonical_name="客户", aliases=[], merged_names=["客户"],
            definitions=["用户"], evidence=[], source_chunks=["f1_c0"],
            abstraction_level="high", relation_degree=1, score=10,
        ),
        "k_plan": ConceptCluster(
            cluster_id="c2", canonical_name="套餐", aliases=[], merged_names=["套餐"],
            definitions=["资费"], evidence=[], source_chunks=["f1_c0"],
            abstraction_level="high", relation_degree=1, score=10,
        ),
    }
    id_by_key = {"k_cust": "c000001", "k_plan": "c000002"}

    instances, _report = asyncio.run(
        generalizer._build_instances(
            ["k_cust", "k_plan"], clusters, id_by_key, concept_relations=[]
        )
    )

    by_name = {inst.name: inst for inst in instances}
    assert set(by_name) == {"张三", "59元套餐"}
    # The inline link came from reading the chunk, not the heuristic pass.
    assert by_name["张三"].link_values[0].predicate == "订购"
    assert by_name["张三"].link_values[0].target_instance_id == by_name["59元套餐"].id
    # No isolated node.
    linked = set()
    for inst in instances:
        for link in inst.link_values:
            linked.add(inst.id)
            linked.add(link.target_instance_id)
    assert all(inst.id in linked for inst in instances)


def test_concept_candidate_extractor_also_parses_instances() -> None:
    chunk = Chunk(
        id="chunk_0001",
        text="客户张三订购了59元套餐。",
        start_char=0,
        end_char=13,
        token_count=13,
    )
    client = MockLLMClient(
        """
        {
          "concepts": [
            {"name": "客户", "definition": "用户", "evidence": ["客户张三"]},
            {"name": "套餐", "definition": "资费", "evidence": ["59元套餐"]}
          ],
          "relations": [
            {"source": "客户", "predicate": "订购", "target": "套餐"}
          ],
          "instances": [
            {"concept": "客户", "name": "张三",
             "links": [{"target": "59元套餐", "predicate": "订购"}]},
            {"concept": "套餐", "name": "59元套餐", "attributes": {"月费": "59"}}
          ]
        }
        """
    )

    candidates = asyncio.run(
        ConceptCandidateExtractor(client, ConceptConfig()).extract(chunk)
    )

    assert [inst.name for inst in candidates.instances] == ["张三", "59元套餐"]
    assert candidates.instances[0].links[0].target == "59元套餐"


def test_concept_generalizer_folds_candidate_instances_without_extra_calls() -> None:
    from ontology_construct.concept.models import (
        ChunkConceptCandidates,
        ConceptCandidate,
        ConceptInstanceCandidate,
        RelationCandidate,
    )

    class CountingClient:
        def __init__(self) -> None:
            self.calls = 0

        async def complete(self, prompt: str, *, system_prompt: str | None = None) -> str:
            self.calls += 1
            return "{}"  # force deterministic path; no instances/relations from LLM

    def make_chunk(chunk_id: str) -> ChunkConceptCandidates:
        return ChunkConceptCandidates(
            chunk_id=chunk_id,
            concepts=[
                ConceptCandidate(name="客户", definition="用户", evidence=[f"客户张三{chunk_id}"], abstraction_level="high"),
                ConceptCandidate(name="套餐", definition="资费", evidence=[f"59元套餐{chunk_id}"], abstraction_level="high"),
            ],
            relations=[RelationCandidate(source="客户", predicate="订购", target="套餐")],
            instances=[
                ConceptInstanceCandidate(
                    concept="客户", name="张三",
                    links=[RelationCandidate(source="张三", predicate="订购", target="59元套餐")],
                ),
                ConceptInstanceCandidate(concept="套餐", name="59元套餐", attributes={"月费": "59"}),
            ],
        )

    chunks = [make_chunk("c0"), make_chunk("c1")]
    client = CountingClient()
    generalizer = ConceptGeneralizer(
        client,
            ConceptConfig(
                instance_extraction_mode="per_chunk",
                extract_real_instances=True,
                controlled_relation_build=False,
                selection_score_threshold=0.0,
            ),
    )

    artifacts = asyncio.run(generalizer.generalize_with_artifacts(chunks))

    names = {inst.name for inst in artifacts.ontology.instances}
    assert names == {"张三", "59元套餐"}
    zhang = next(i for i in artifacts.ontology.instances if i.name == "张三")
    plan = next(i for i in artifacts.ontology.instances if i.name == "59元套餐")
    # Inline link reused from candidate extraction (no extra per-chunk pass).
    assert zhang.link_values[0].target_instance_id == plan.id
    # Only the single global-generalize attempt hit the LLM; no instance pass.
    assert client.calls == 1


def test_instance_relation_pair_selection_guarantees_low_score_instances() -> None:
    from ontology_construct.concept.models import ConceptInstanceCandidate, RelationCandidate

    hot = [
        ConceptInstanceCandidate(concept="工序", name=f"高频工序{i}", evidence=["共现A"])
        for i in range(1, 5)
    ]
    rare = ConceptInstanceCandidate(concept="设备", name="孤立设备", evidence=["孤立设备"])
    instances = [*hot, rare]
    chunk_text = (
        "高频工序1 高频工序2 高频工序3 高频工序4 共现A "
        "孤立设备 与 高频工序1 共同出现"
    )
    generalizer = ConceptGeneralizer(
        MockLLMClient(),
        ConceptConfig(
            max_instance_relation_pairs=4,
            min_relation_pairs_per_instance=1,
        ),
        chunk_texts={"chunk_0001": chunk_text},
    )

    selection = generalizer._select_instance_relation_pairs(
        instances,
        [
            RelationCandidate(source="工序", predicate="关联", target="工序"),
            RelationCandidate(source="设备", predicate="执行于", target="工序"),
        ],
    )

    assert len(selection.pairs) == 4
    assert selection.candidate_count > len(selection.pairs)
    assert selection.truncated_count == selection.candidate_count - len(selection.pairs)
    assert any(
        pair.source.name == "孤立设备" or pair.target.name == "孤立设备"
        for pair in selection.pairs
    )
