import json

from click.testing import CliRunner

from ontology_construct.cli import cli
from ontology_construct.models import (
    ActionInvocation,
    Entity,
    InstanceLinkValue,
    LocalOntology,
    OntologyAction,
    OntologyInstance,
    Triple,
)
from ontology_construct.storage import save_json_model


def test_chunk_command_writes_jsonl(tmp_path) -> None:
    input_path = tmp_path / "sample.txt"
    output_path = tmp_path / "chunks.jsonl"
    input_path.write_text("第一段介绍苹果公司。\n\n第二段介绍客户账单。", encoding="utf-8")

    result = CliRunner().invoke(
        cli,
        [
            "chunk",
            "--input",
            str(input_path),
            "--output",
            str(output_path),
            "--chunk-size",
            "10",
            "--chunk-overlap",
            "0",
        ],
    )

    assert result.exit_code == 0
    rows = [json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines()]
    assert [row["id"] for row in rows] == ["chunk_0001", "chunk_0002"]
    assert rows[0]["metadata"]["chunk_size"] == 10


def test_extract_command_writes_chunks_and_local_ontologies(tmp_path) -> None:
    input_path = tmp_path / "sample.txt"
    output_dir = tmp_path / "run"
    input_path.write_text("第一段介绍苹果公司。\n\n第二段介绍客户账单。", encoding="utf-8")

    result = CliRunner().invoke(
        cli,
        [
            "extract",
            "--input",
            str(input_path),
            "--output-dir",
            str(output_dir),
            "--backend",
            "mock",
            "--chunk-size",
            "10",
            "--chunk-overlap",
            "0",
        ],
    )

    assert result.exit_code == 0
    assert (output_dir / "chunks.jsonl").exists()
    assert (output_dir / "progress.sqlite").exists()
    local_files = sorted((output_dir / "local").glob("*.json"))
    assert [path.name for path in local_files] == ["chunk_0001.json", "chunk_0002.json"]
    local = json.loads(local_files[0].read_text(encoding="utf-8"))
    assert local["chunk_id"] == "chunk_0001"
    assert local["entities"] == []

    status_result = CliRunner().invoke(cli, ["status", "--run-dir", str(output_dir)])
    assert status_result.exit_code == 0
    assert "pending=0 done=2 failed=0" in status_result.output

    rerun_result = CliRunner().invoke(
        cli,
        [
            "extract",
            "--input",
            str(input_path),
            "--output-dir",
            str(output_dir),
            "--backend",
            "mock",
            "--chunk-size",
            "10",
            "--chunk-overlap",
            "0",
        ],
    )
    assert rerun_result.exit_code == 0
    assert "skipped 2" in rerun_result.output


def test_merge_command_writes_global_ontology(tmp_path) -> None:
    local_dir = tmp_path / "local"
    output_path = tmp_path / "global.json"
    local_dir.mkdir()
    save_json_model(
        LocalOntology(
            chunk_id="chunk_0001",
            entities=[Entity(id="e1", name="苹果公司", source_chunks=["chunk_0001"])],
            triples=[
                Triple(
                    subject_id="e1",
                    predicate="成立年份",
                    object_kind="literal",
                    object_value=1976,
                    source_chunks=["chunk_0001"],
                )
            ],
        ),
        local_dir / "chunk_0001.json",
    )
    (local_dir / "._chunk_0001.json").write_bytes(b"\x00\x05\x16\x07binary metadata")

    result = CliRunner().invoke(
        cli,
        ["merge", "--local-dir", str(local_dir), "--output", str(output_path)],
    )

    assert result.exit_code == 0
    merged = json.loads(output_path.read_text(encoding="utf-8"))
    assert len(merged["entities"]) == 1
    assert len(merged["triples"]) == 1


def test_merge_command_limits_extracted_instances(tmp_path) -> None:
    local_dir = tmp_path / "local"
    output_path = tmp_path / "global.json"
    formatted_path = tmp_path / "formatted.json"
    schema_path = tmp_path / "schema.json"
    graph_path = tmp_path / "graph.json"
    local_dir.mkdir()
    save_json_model(
        LocalOntology(
            chunk_id="chunk_0001",
            entities=[Entity(id="e1", name="用户", source_chunks=["chunk_0001"])],
            actions=[
                OntologyAction(
                    id="a1",
                    name="识别风险",
                    input_entity_ids=["e1"],
                    output_entity_ids=["e1"],
                )
            ],
            instances=[
                OntologyInstance(
                    id="i1",
                    name="用户A",
                    class_id="e1",
                    link_values=[
                        InstanceLinkValue(predicate="相关", target_instance_id="i2")
                    ],
                ),
                OntologyInstance(id="i2", name="用户B", class_id="e1"),
                OntologyInstance(id="i3", name="用户C", class_id="e1"),
            ],
            action_invocations=[
                ActionInvocation(
                    id="ai1",
                    action_name="识别风险",
                    input_instance_ids=["i1", "i3"],
                    output_instance_ids=["i2"],
                )
            ],
        ),
        local_dir / "chunk_0001.json",
    )

    result = CliRunner().invoke(
        cli,
        [
            "merge",
            "--local-dir",
            str(local_dir),
            "--output",
            str(output_path),
            "--formatted-output",
            str(formatted_path),
            "--schema-output",
            str(schema_path),
            "--graph-output",
            str(graph_path),
            "--max-extracted-instances",
            "1",
            "--instance-count",
            "2",
        ],
    )

    assert result.exit_code == 0
    merged = json.loads(output_path.read_text(encoding="utf-8"))
    assert [instance["name"] for instance in merged["instances"]] == ["用户A"]
    assert merged["instances"][0]["link_values"] == []
    assert merged["action_invocations"][0]["input_instance_ids"] == ["inst_000001"]
    assert merged["action_invocations"][0]["output_instance_ids"] == []

    formatted = json.loads(formatted_path.read_text(encoding="utf-8"))
    real_instances = [
        instance for instance in formatted["instances"] if not instance["generated"]
    ]
    generated_instances = [
        instance for instance in formatted["instances"] if instance["generated"]
    ]
    assert [instance["instanceName"] for instance in real_instances] == ["用户A"]
    assert len(generated_instances) == 2
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    assert [obj["name"] for obj in schema["object_types"]] == ["用户"]
    graph = json.loads(graph_path.read_text(encoding="utf-8"))
    assert [obj["description"] for obj in graph["objects"]] == ["用户A"]
    assert all("objectId" not in obj for obj in graph["objects"])


def test_merge_command_use_embedding_requires_key(tmp_path, monkeypatch) -> None:
    missing_env = "MISSING_QWEN_EMBEDDING_KEY_FOR_TEST"
    monkeypatch.delenv(missing_env, raising=False)
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        f"merge:\n  embedding_api_key_env: {missing_env}\n",
        encoding="utf-8",
    )
    local_dir = tmp_path / "local"
    output_path = tmp_path / "global.json"
    local_dir.mkdir()
    save_json_model(
        LocalOntology(
            chunk_id="chunk_0001",
            entities=[Entity(id="e1", name="苹果公司", source_chunks=["chunk_0001"])],
        ),
        local_dir / "chunk_0001.json",
    )

    result = CliRunner().invoke(
        cli,
        [
            "merge",
            "--local-dir",
            str(local_dir),
            "--output",
            str(output_path),
            "--config",
            str(config_path),
            "--use-embedding",
        ],
    )

    assert result.exit_code != 0
    assert missing_env in result.output


def test_export_command_writes_json(tmp_path) -> None:
    ontology_path = tmp_path / "global.json"
    export_path = tmp_path / "ontology_export.json"
    local_dir = tmp_path / "local"
    local_dir.mkdir()
    save_json_model(
        LocalOntology(
            chunk_id="chunk_0001",
            entities=[Entity(id="e1", name="苹果公司", source_chunks=["chunk_0001"])],
            triples=[
                Triple(
                    subject_id="e1",
                    predicate="成立年份",
                    object_kind="literal",
                    object_value=1976,
                    source_chunks=["chunk_0001"],
                )
            ],
        ),
        local_dir / "chunk_0001.json",
    )
    CliRunner().invoke(
        cli,
        ["merge", "--local-dir", str(local_dir), "--output", str(ontology_path)],
    )

    result = CliRunner().invoke(
        cli,
        [
            "export",
            "--ontology",
            str(ontology_path),
            "--format",
            "json",
            "--output",
            str(export_path),
        ],
    )

    assert result.exit_code == 0
    exported = json.loads(export_path.read_text(encoding="utf-8"))
    assert len(exported["entities"]) == 1
    assert len(exported["triples"]) == 1


def test_build_command_runs_mock_pipeline(tmp_path) -> None:
    input_path = tmp_path / "sample.txt"
    output_dir = tmp_path / "run"
    input_path.write_text("第一段介绍苹果公司。\n\n第二段介绍客户账单。", encoding="utf-8")

    result = CliRunner().invoke(
        cli,
        [
            "build",
            "--input",
            str(input_path),
            "--output-dir",
            str(output_dir),
            "--backend",
            "mock",
            "--chunk-size",
            "10",
            "--chunk-overlap",
            "0",
        ],
    )

    assert result.exit_code == 0
    assert (output_dir / "chunks.jsonl").exists()
    assert (output_dir / "progress.sqlite").exists()
    assert (output_dir / "global.json").exists()
    assert (output_dir / "ontology.json").exists()
    assert (output_dir / "formatted_ontology.json").exists()
    assert (output_dir / "schema.json").exists()
    assert (output_dir / "graph.json").exists()
    exported = json.loads((output_dir / "ontology.json").read_text(encoding="utf-8"))
    assert exported["entities"] == {}
    assert exported["triples"] == []
    formatted = json.loads(
        (output_dir / "formatted_ontology.json").read_text(encoding="utf-8")
    )
    assert formatted["ontology"]["profile"] == "default"
    assert formatted["objects"] == []
    schema = json.loads((output_dir / "schema.json").read_text(encoding="utf-8"))
    assert schema["scenario"] == "default"
    assert schema["object_types"] == []
    graph = json.loads((output_dir / "graph.json").read_text(encoding="utf-8"))
    assert graph["objects"] == []

    rerun_result = CliRunner().invoke(
        cli,
        [
            "build",
            "--input",
            str(input_path),
            "--output-dir",
            str(output_dir),
            "--backend",
            "mock",
            "--chunk-size",
            "10",
            "--chunk-overlap",
            "0",
        ],
    )
    assert rerun_result.exit_code == 0
    assert "skipped=2" in rerun_result.output


def test_build_command_schema_first_mock_writes_outputs(tmp_path) -> None:
    input_path = tmp_path / "sample.txt"
    output_dir = tmp_path / "schema_first"
    input_path.write_text("设备故障会触发维护工单。", encoding="utf-8")

    result = CliRunner().invoke(
        cli,
        [
            "build",
            "--input",
            str(input_path),
            "--output-dir",
            str(output_dir),
            "--backend",
            "mock",
            "--construction-mode",
            "schema-first",
            "--max-concepts",
            "20",
            "--instance-count",
            "1",
            "--chunk-size",
            "100",
            "--chunk-overlap",
            "0",
        ],
    )

    assert result.exit_code == 0
    assert "Schema-first build complete" in result.output
    assert (output_dir / "chunks.jsonl").exists()
    assert (output_dir / "concept_candidates" / "chunk_0001.json").exists()
    assert (output_dir / "concept_global.json").exists()
    assert (output_dir / "concept_clusters.json").exists()
    assert (output_dir / "concept_selected.json").exists()
    assert (output_dir / "concept_relations.json").exists()
    assert (output_dir / "concept_instances.json").exists()
    assert (output_dir / "ontology_quality.json").exists()
    assert (output_dir / "formatted_ontology.json").exists()
    formatted = json.loads(
        (output_dir / "formatted_ontology.json").read_text(encoding="utf-8")
    )
    assert formatted["objects"] == []
    assert formatted["links"] == []
    assert formatted["instances"] == []
