import asyncio

from ontology_construct.merger import (
    DuplicatePair,
    EntityDeduplicator,
    OntologyMerger,
    cosine_similarity,
    merge_entity_groups,
    normalize_name,
)
from ontology_construct.models import (
    ActionInvocation,
    Entity,
    InstanceAttributeValue,
    InstanceLinkValue,
    LocalOntology,
    OntologyAction,
    OntologyInstance,
    Triple,
)


def test_normalize_name_handles_width_case_and_spaces() -> None:
    assert normalize_name(" Ａｐｐｌｅ Inc. ") == "appleinc."


def test_ontology_merger_exact_merges_entities_and_duplicate_triples() -> None:
    local_a = LocalOntology(
        chunk_id="chunk_0001",
        entities=[
            Entity(
                id="e1",
                name="Apple Inc.",
                aliases=["苹果公司"],
                entity_type="Organization",
                source_chunks=["chunk_0001"],
            ),
            Entity(id="e2", name="史蒂夫乔布斯", source_chunks=["chunk_0001"]),
        ],
        triples=[
            Triple(
                subject_id="e1",
                predicate="创始人",
                object_kind="entity",
                object_id="e2",
                confidence=0.8,
                source_chunks=["chunk_0001"],
            ),
            Triple(
                subject_id="e1",
                predicate="成立年份",
                object_kind="literal",
                object_value=1976,
                confidence=0.7,
                source_chunks=["chunk_0001"],
            ),
        ],
    )
    local_b = LocalOntology(
        chunk_id="chunk_0002",
        entities=[
            Entity(
                id="x1",
                name="苹果公司",
                aliases=["Apple Inc."],
                entity_type="Organization",
                source_chunks=["chunk_0002"],
            ),
            Entity(id="x2", name="史蒂夫乔布斯", source_chunks=["chunk_0002"]),
        ],
        triples=[
            Triple(
                subject_id="x1",
                predicate="创始人",
                object_kind="entity",
                object_id="x2",
                confidence=0.95,
                source_chunks=["chunk_0002"],
            ),
            Triple(
                subject_id="x1",
                predicate="成立年份",
                object_kind="literal",
                object_value=1976,
                confidence=0.9,
                source_chunks=["chunk_0002"],
            ),
        ],
    )

    global_ontology = OntologyMerger().merge([local_a, local_b])

    assert len(global_ontology.entities) == 2
    assert len(global_ontology.triples) == 2
    founder_triple = next(
        triple for triple in global_ontology.triples if triple.predicate == "创始人"
    )
    assert founder_triple.confidence == 0.95
    assert founder_triple.source_chunks == ["chunk_0001", "chunk_0002"]


def test_cosine_similarity() -> None:
    assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == 1.0
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == 0.0


def test_merge_entity_groups_remaps_and_deduplicates_triples() -> None:
    ontology = OntologyMerger().merge(
        [
            LocalOntology(
                chunk_id="chunk_0001",
                entities=[
                    Entity(id="e1", name="苹果公司", source_chunks=["chunk_0001"]),
                    Entity(id="e2", name="Apple Inc.", source_chunks=["chunk_0002"]),
                ],
                triples=[
                    Triple(
                        subject_id="e1",
                        predicate="成立年份",
                        object_kind="literal",
                        object_value=1976,
                        confidence=0.7,
                        source_chunks=["chunk_0001"],
                    ),
                    Triple(
                        subject_id="e2",
                        predicate="成立年份",
                        object_kind="literal",
                        object_value=1976,
                        confidence=0.9,
                        source_chunks=["chunk_0002"],
                    ),
                ],
            )
        ]
    )

    merged = merge_entity_groups(ontology, [["g000001", "g000002"]])

    assert len(merged.entities) == 1
    entity = merged.entities["g000001"]
    assert entity.aliases == ["Apple Inc."]
    assert entity.source_chunks == ["chunk_0001", "chunk_0002"]
    assert len(merged.triples) == 1
    assert merged.triples[0].confidence == 0.9
    assert merged.triples[0].source_chunks == ["chunk_0001", "chunk_0002"]


def test_entity_deduplicator_uses_embedding_similarity() -> None:
    class FakeEmbeddingClient:
        async def embed_texts(self, texts):
            assert texts == ["苹果公司", "Apple Inc.", "史蒂夫乔布斯"]
            return [[1.0, 0.0], [0.99, 0.01], [0.0, 1.0]]

    ontology = OntologyMerger().merge(
        [
            LocalOntology(
                chunk_id="chunk_0001",
                entities=[
                    Entity(id="e1", name="苹果公司"),
                    Entity(id="e2", name="Apple Inc."),
                    Entity(id="e3", name="史蒂夫乔布斯"),
                ],
            )
        ]
    )

    deduped = asyncio.run(
        EntityDeduplicator(FakeEmbeddingClient(), threshold=0.95).deduplicate(ontology)
    )

    assert len(deduped.entities) == 2


def test_duplicate_pair_is_public_model() -> None:
    pair = DuplicatePair("a", "b", 0.99)

    assert pair.entity_a_id == "a"


def test_ontology_merger_remaps_instance_links_and_invocations() -> None:
    local_a = LocalOntology(
        chunk_id="chunk_0001",
        entities=[
            Entity(id="e1", name="用户", source_chunks=["chunk_0001"]),
            Entity(id="e2", name="订单", source_chunks=["chunk_0001"]),
        ],
        actions=[
            OntologyAction(
                id="a1",
                name="创建订单",
                input_entity_ids=["e1"],
                output_entity_ids=["e2"],
                source_chunks=["chunk_0001"],
            )
        ],
        instances=[
            OntologyInstance(
                id="i1",
                name="张三",
                class_id="e1",
                attribute_values=[
                    InstanceAttributeValue(
                        attribute_name="等级",
                        value="VIP",
                        source_chunks=["chunk_0001"],
                    )
                ],
                link_values=[
                    InstanceLinkValue(
                        predicate="拥有",
                        target_instance_id="i2",
                        confidence=0.9,
                        source_chunks=["chunk_0001"],
                    )
                ],
                source_chunks=["chunk_0001"],
            ),
            OntologyInstance(
                id="i2",
                name="PO-001",
                class_id="e2",
                source_chunks=["chunk_0001"],
            ),
        ],
        action_invocations=[
            ActionInvocation(
                id="ai1",
                action_name="创建订单",
                input_instance_ids=["i1"],
                output_instance_ids=["i2"],
                source_chunks=["chunk_0001"],
            )
        ],
    )
    local_b = LocalOntology(
        chunk_id="chunk_0002",
        entities=[
            Entity(id="x1", name="用户", source_chunks=["chunk_0002"]),
            Entity(id="x2", name="订单", source_chunks=["chunk_0002"]),
        ],
        instances=[
            OntologyInstance(
                id="j1",
                name="张三",
                class_id="x1",
                attribute_values=[
                    InstanceAttributeValue(
                        attribute_name="等级",
                        value="VIP",
                        source_chunks=["chunk_0002"],
                    )
                ],
                source_chunks=["chunk_0002"],
            ),
        ],
    )

    merged = OntologyMerger().merge([local_a, local_b])

    instances_by_name = {instance.name: instance for instance in merged.instances}
    assert set(instances_by_name) == {"张三", "PO-001"}
    zhangsan = instances_by_name["张三"]
    order = instances_by_name["PO-001"]
    assert zhangsan.source_chunks == ["chunk_0001", "chunk_0002"]
    # link target_instance_id is remapped to the merged global instance id.
    assert zhangsan.link_values[0].target_instance_id == order.id
    # attribute_values dedupe by attribute_name, source chunks accumulate.
    assert len(zhangsan.attribute_values) == 1
    assert zhangsan.attribute_values[0].source_chunks == ["chunk_0001", "chunk_0002"]

    invocation = merged.action_invocations[0]
    assert invocation.input_instance_ids == [zhangsan.id]
    assert invocation.output_instance_ids == [order.id]


def test_merge_entity_groups_remerges_instances_after_class_collapse() -> None:
    ontology = OntologyMerger().merge(
        [
            LocalOntology(
                chunk_id="chunk_0001",
                entities=[
                    Entity(id="e1", name="供应商", source_chunks=["chunk_0001"]),
                    Entity(id="e2", name="Supplier", source_chunks=["chunk_0001"]),
                ],
                instances=[
                    OntologyInstance(
                        id="i1",
                        name="ACME",
                        class_id="e1",
                        source_chunks=["chunk_0001"],
                    ),
                    OntologyInstance(
                        id="i2",
                        name="ACME",
                        class_id="e2",
                        source_chunks=["chunk_0001"],
                    ),
                ],
                action_invocations=[
                    ActionInvocation(
                        id="ai1",
                        action_name="供货",
                        input_instance_ids=["i2"],
                        source_chunks=["chunk_0001"],
                    )
                ],
            )
        ]
    )

    # Pre-condition: two separate instances because the two classes haven't
    # been recognised as duplicates yet by the exact-match merger.
    assert len(ontology.instances) == 2

    deduped = merge_entity_groups(ontology, [["g000001", "g000002"]])

    # Both ACME instances now share the same class and should collapse.
    assert len(deduped.entities) == 1
    assert len(deduped.instances) == 1
    canonical_instance_id = deduped.instances[0].id
    # The action invocation should have its input remapped to the surviving id.
    assert deduped.action_invocations[0].input_instance_ids == [canonical_instance_id]


def test_ontology_merger_collapses_synonymous_actions_and_remaps_invocations() -> None:
    local = LocalOntology(
        chunk_id="chunk_0001",
        entities=[
            Entity(id="e1", name="用户", source_chunks=["chunk_0001"]),
            Entity(id="e2", name="订单", source_chunks=["chunk_0001"]),
        ],
        actions=[
            OntologyAction(
                id="a1",
                name="创建订单",
                input_entity_ids=["e1"],
                output_entity_ids=["e2"],
                source_chunks=["chunk_0001"],
            ),
            OntologyAction(
                id="a2",
                name=" 创建订单 ",
                input_entity_ids=["e1"],
                output_entity_ids=["e2"],
                source_chunks=["chunk_0001"],
            ),
        ],
        instances=[
            OntologyInstance(
                id="i1",
                name="张三",
                class_id="e1",
                source_chunks=["chunk_0001"],
            ),
            OntologyInstance(
                id="i2",
                name="PO-001",
                class_id="e2",
                source_chunks=["chunk_0001"],
            ),
        ],
        action_invocations=[
            ActionInvocation(
                id="ai1",
                action_name=" 创建订单 ",
                input_instance_ids=["i1"],
                output_instance_ids=["i2"],
                source_chunks=["chunk_0001"],
            )
        ],
    )

    merged = OntologyMerger().merge([local])

    # Whitespace-only variant should fold into the canonical action.
    assert len(merged.actions) == 1
    assert merged.actions[0].name == "创建订单"
    # The invocation's action_name should be rewritten to the canonical spelling
    # so the formatter can resolve it to actionId.
    assert merged.action_invocations[0].action_name == "创建订单"


def test_ontology_merger_prefers_higher_confidence_attribute_value() -> None:
    locals_to_merge = [
        LocalOntology(
            chunk_id="chunk_0001",
            entities=[Entity(id="e1", name="订单")],
            instances=[
                OntologyInstance(
                    id="i1",
                    name="PO-001",
                    class_id="e1",
                    attribute_values=[
                        InstanceAttributeValue(
                            attribute_name="金额",
                            value=1000,
                            confidence=0.5,
                            source_chunks=["chunk_0001"],
                        )
                    ],
                )
            ],
        ),
        LocalOntology(
            chunk_id="chunk_0002",
            entities=[Entity(id="x1", name="订单")],
            instances=[
                OntologyInstance(
                    id="j1",
                    name="PO-001",
                    class_id="x1",
                    attribute_values=[
                        InstanceAttributeValue(
                            attribute_name="金额",
                            value=2000,
                            confidence=0.95,
                            source_chunks=["chunk_0002"],
                        )
                    ],
                )
            ],
        ),
    ]

    merged = OntologyMerger().merge(locals_to_merge)

    instance = merged.instances[0]
    assert len(instance.attribute_values) == 1
    # The higher-confidence value wins even though it arrived second.
    assert instance.attribute_values[0].value == 2000
    assert sorted(instance.attribute_values[0].source_chunks) == [
        "chunk_0001",
        "chunk_0002",
    ]


def test_ontology_merger_warns_on_conflicting_instance_attribute(caplog) -> None:
    locals_to_merge = [
        LocalOntology(
            chunk_id="chunk_0001",
            entities=[Entity(id="e1", name="订单", source_chunks=["chunk_0001"])],
            instances=[
                OntologyInstance(
                    id="i1",
                    name="PO-001",
                    class_id="e1",
                    attribute_values=[
                        InstanceAttributeValue(
                            attribute_name="金额",
                            value=1000,
                            source_chunks=["chunk_0001"],
                        )
                    ],
                    source_chunks=["chunk_0001"],
                )
            ],
        ),
        LocalOntology(
            chunk_id="chunk_0002",
            entities=[Entity(id="x1", name="订单", source_chunks=["chunk_0002"])],
            instances=[
                OntologyInstance(
                    id="j1",
                    name="PO-001",
                    class_id="x1",
                    attribute_values=[
                        InstanceAttributeValue(
                            attribute_name="金额",
                            value=2000,
                            source_chunks=["chunk_0002"],
                        )
                    ],
                    source_chunks=["chunk_0002"],
                )
            ],
        ),
    ]

    with caplog.at_level("WARNING", logger="ontology_construct.merger.ontology_merger"):
        merged = OntologyMerger().merge(locals_to_merge)

    instance = merged.instances[0]
    assert len(instance.attribute_values) == 1
    # First-encountered value wins; the conflict is logged.
    assert instance.attribute_values[0].value == 1000
    warning_messages = [
        record.message for record in caplog.records if record.levelname == "WARNING"
    ]
    assert any(
        "PO-001" in message and "金额" in message and "2000" in message
        for message in warning_messages
    )


def test_ontology_merger_drops_instance_without_resolvable_class() -> None:
    local = LocalOntology(
        chunk_id="chunk_0001",
        entities=[Entity(id="e1", name="用户")],
        instances=[
            OntologyInstance(id="i1", name="张三", class_id="missing"),
            OntologyInstance(id="i2", name="李四", class_id="e1"),
        ],
    )

    merged = OntologyMerger().merge([local])

    assert [instance.name for instance in merged.instances] == ["李四"]
