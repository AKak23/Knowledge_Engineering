import asyncio
import json

from ontology_construct.config import (
    AppConfig,
    BuildQualityConfig,
    InstanceBuilderConfig,
)
from ontology_construct.instance_builder import InstanceBuilder
from ontology_construct.models import (
    ActionInvocation,
    Chunk,
    Entity,
    GlobalOntology,
    OntologyInstance,
    Triple,
)


def _manufacturing_ontology() -> GlobalOntology:
    return GlobalOntology.from_entities_and_triples(
        [
            Entity(id="order", name="生产订单"),
            Entity(id="work_order", name="生产工单"),
            Entity(id="process", name="加工工序"),
            Entity(id="resource", name="资源"),
            Entity(id="entity", name="生产实体"),
            Entity(id="event", name="生产事件"),
        ],
        [
            Triple(subject_id="order", predicate="包含", object_id="work_order"),
            Triple(subject_id="process", predicate="执行于", object_id="resource"),
            Triple(subject_id="process", predicate="处理", object_id="entity"),
        ],
    )


def test_instance_builder_extracts_log_instances_and_links(tmp_path) -> None:
    log_path = tmp_path / "process.log"
    log_path.write_text(
        "000004 | 事件000010 | process_start | 订单=0001号整车订单 | "
        "工单=0001号生产工单 | 工序=开卷落料-工序实例02 | "
        "资源=[1号冲压线(1号冲压线)] | 实体=[0001号冲压批(0001号冲压批)]\n",
        encoding="utf-8",
    )
    config = AppConfig(
        instance_builder=InstanceBuilderConfig(llm_enabled_for_unresolved=False)
    )

    ontology, report = asyncio.run(
        InstanceBuilder(config).build(_manufacturing_ontology(), [log_path], tmp_path)
    )

    names = {instance.name for instance in ontology.instances}
    assert "0001号整车订单" in names
    assert "0001号生产工单" in names
    assert "开卷落料-工序实例02" in names
    assert "1号冲压线" in names
    assert "0001号冲压批" in names
    assert report.instance_count >= 5
    assert report.link_count >= 3
    assert (tmp_path / "instance_build_report.json").exists()


def test_instance_builder_extracts_json_instances_by_field_mapping(tmp_path) -> None:
    json_path = tmp_path / "records.json"
    json_path.write_text(
        json.dumps(
            [
                {
                    "order_id": "订单A",
                    "resource_name": "质检A组",
                    "event_code": "事件0001",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    config = AppConfig(
        instance_builder=InstanceBuilderConfig(llm_enabled_for_unresolved=False)
    )

    ontology, report = asyncio.run(
        InstanceBuilder(config).build(_manufacturing_ontology(), [json_path])
    )

    by_name = {instance.name: instance.class_id for instance in ontology.instances}
    assert by_name["订单A"] == "order"
    assert by_name["质检A组"] == "resource"
    assert by_name["事件0001"] == "event"
    assert report.candidate_count == 3


def test_instance_builder_keeps_successful_llm_batches_when_one_fails(tmp_path) -> None:
    text_path = tmp_path / "notes.txt"
    text_path.write_text(
        "用户张三订购了套餐A。\n\n" * 20 + "这段会触发失败。\n\n" * 20,
        encoding="utf-8",
    )
    ontology = GlobalOntology.from_entities_and_triples(
        [Entity(id="user", name="用户"), Entity(id="plan", name="套餐")],
        [Triple(subject_id="user", predicate="订购", object_id="plan")],
    )
    config = AppConfig(
        chunk={"size": 30, "overlap": 0},
        instance_builder=InstanceBuilderConfig(
            llm_chunk_batch_size=1,
            llm_concurrency=1,
            source_text_char_limit=200,
        ),
        build_quality=BuildQualityConfig(instance_retry_rounds=0),
    )

    class PartiallyFailingClient:
        def __init__(self) -> None:
            self.calls = 0

        async def complete(self, prompt: str, *, system_prompt: str | None = None) -> str:
            self.calls += 1
            if self.calls == 2:
                raise RuntimeError("temporary failure")
            return """
            {
              "instances": [
                {"concept": "用户", "name": "张三"},
                {"concept": "套餐", "name": "套餐A"}
              ]
            }
            """

    built, report = asyncio.run(
        InstanceBuilder(config, llm_client=PartiallyFailingClient()).build(
            ontology,
            [text_path],
            tmp_path,
        )
    )

    assert {instance.name for instance in built.instances} >= {"张三", "套餐A"}
    assert report.failed_batch_count == 1
    assert "temporary failure" in (tmp_path / "failed_instance_batches.jsonl").read_text(
        encoding="utf-8"
    )


def test_instance_builder_retries_failed_batch_and_preserves_existing_instances(
    tmp_path,
) -> None:
    text_path = tmp_path / "cooling.txt"
    text_path.write_text("控制器01控制水冷泵01。", encoding="utf-8")
    ontology = GlobalOntology.from_entities_and_triples(
        [Entity(id="controller", name="控制器"), Entity(id="pump", name="水冷泵")],
        [Triple(subject_id="controller", predicate="控制", object_id="pump")],
        instances=[
            OntologyInstance(id="existing", name="控制器01", class_id="controller")
        ],
        action_invocations=[
            ActionInvocation(id="inv1", action_name="启动水冷", input_instance_ids=["existing"])
        ],
    )
    config = AppConfig(
        chunk={"size": 100, "overlap": 0},
        build_quality=BuildQualityConfig(instance_retry_rounds=1),
    )

    class RetryClient:
        def __init__(self) -> None:
            self.calls = 0

        async def complete(self, prompt: str, *, system_prompt: str | None = None) -> str:
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("transient timeout")
            return json.dumps(
                {
                    "instances": [
                        {"concept": "控制器", "name": "控制器01"},
                        {
                            "concept": "水冷泵",
                            "name": "水冷泵01",
                            "links": [{"predicate": "由控制器控制", "target": "控制器01"}],
                        },
                    ]
                },
                ensure_ascii=False,
            )

    client = RetryClient()
    built, report = asyncio.run(
        InstanceBuilder(config, llm_client=client).build(ontology, [text_path])
    )

    assert client.calls == 2
    assert {instance.name for instance in built.instances} == {"控制器01", "水冷泵01"}
    assert report.existing_instance_count == 1
    assert report.added_instance_count == 1
    assert report.failed_batch_count == 0
    assert built.action_invocations[0].id == "inv1"


def test_instance_builder_keeps_api_chunk_ids_as_instance_provenance(tmp_path) -> None:
    text_path = tmp_path / "input_001_cooling.txt"
    text_path.write_text("控制器01控制水冷泵01。", encoding="utf-8")
    ontology = GlobalOntology.from_entities_and_triples(
        [Entity(id="controller", name="控制器")],
        [],
    )
    source_chunk = Chunk(
        id="file_001_chunk_0001",
        text="控制器01控制水冷泵01。",
        start_char=0,
        end_char=13,
        token_count=13,
        metadata={"source_file": text_path.name},
    )

    class Client:
        async def complete(self, prompt: str, *, system_prompt: str | None = None) -> str:
            return '{"instances":[{"concept":"控制器","name":"控制器01"}]}'

    built, _report = asyncio.run(
        InstanceBuilder(AppConfig(), llm_client=Client()).build(
            ontology,
            [text_path],
            source_chunks=[source_chunk],
        )
    )

    assert built.instances[0].source_chunks == ["file_001_chunk_0001"]


def test_instance_builder_deduplicates_by_class_and_name(tmp_path) -> None:
    log_path = tmp_path / "process.log"
    log_path.write_text(
        "\n".join(
            [
                "事件000010 | 订单=0001号整车订单 | 工单=0001号生产工单",
                "事件000011 | 订单=0001号整车订单 | 工单=0001号生产工单",
            ]
        ),
        encoding="utf-8",
    )
    config = AppConfig(
        instance_builder=InstanceBuilderConfig(llm_enabled_for_unresolved=False)
    )

    ontology, _report = asyncio.run(
        InstanceBuilder(config).build(_manufacturing_ontology(), [log_path])
    )

    names = [instance.name for instance in ontology.instances]
    assert names.count("0001号整车订单") == 1
    assert names.count("0001号生产工单") == 1
