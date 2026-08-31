import json
from pathlib import Path

from click.testing import CliRunner

import ontology_construct.cli as cli_module
from ontology_construct.cli import cli


FIXTURE = Path(__file__).parent / "fixtures" / "phase2_cooling_system.txt"


class CoolingWorkflowClient:
    async def complete(self, prompt: str, *, system_prompt: str | None = None) -> str:
        if '"schema_types"' in prompt:
            return json.dumps(
                {
                    "instances": [
                        {"concept": "冷却设备", "name": "冷却机组01", "links": [{"predicate": "配置", "target": "温度传感器01"}, {"predicate": "配置", "target": "水冷泵01"}]},
                        {"concept": "冷却设备", "name": "冷却机组02", "links": [{"predicate": "配置", "target": "温度传感器02"}, {"predicate": "配置", "target": "水冷泵02"}]},
                        {"concept": "温度传感器", "name": "温度传感器01"},
                        {"concept": "温度传感器", "name": "温度传感器02"},
                        {"concept": "水冷泵", "name": "水冷泵01"},
                        {"concept": "水冷泵", "name": "水冷泵02"},
                        {"concept": "控制器", "name": "控制器01", "links": [{"predicate": "控制", "target": "水冷泵01"}]},
                        {"concept": "检修工单", "name": "检修工单01", "links": [{"predicate": "关联", "target": "温度传感器01"}, {"predicate": "关联", "target": "值班人员01"}]},
                        {"concept": "行动记录", "name": "行动记录01", "links": [{"predicate": "属于", "target": "冷却机组01"}]},
                        {"concept": "温度统计", "name": "温度统计01", "links": [{"predicate": "来源于", "target": "温度传感器01"}]},
                        {"concept": "水箱", "name": "冷却水箱01", "links": [{"predicate": "供水", "target": "水冷泵01"}]},
                        {"concept": "值班人员", "name": "值班人员01"},
                    ]
                },
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "entities": [
                    {"id": "e1", "name": "冷却设备", "type": "Equipment"},
                    {"id": "e2", "name": "温度传感器", "type": "Sensor"},
                    {"id": "e3", "name": "水冷泵", "type": "Equipment"},
                    {"id": "e4", "name": "控制器", "type": "Controller"},
                    {"id": "e5", "name": "检修工单", "type": "WorkOrder"},
                    {"id": "e6", "name": "行动记录", "type": "Record"},
                    {"id": "e7", "name": "温度统计", "type": "Statistics"},
                    {"id": "e8", "name": "水箱", "type": "Equipment"},
                    {"id": "e9", "name": "值班人员", "type": "Person"},
                ],
                "triples": [
                    {"subject": "e2", "predicate": "监测", "object": "e1", "object_type": "entity"},
                    {"subject": "e4", "predicate": "控制", "object": "e3", "object_type": "entity"},
                    {"subject": "e5", "predicate": "关联", "object": "e2", "object_type": "entity"},
                    {"subject": "e5", "predicate": "分派给", "object": "e9", "object_type": "entity"},
                    {"subject": "e6", "predicate": "属于", "object": "e1", "object_type": "entity"},
                    {"subject": "e7", "predicate": "来源于", "object": "e2", "object_type": "entity"},
                    {"subject": "e8", "predicate": "供水", "object": "e3", "object_type": "entity"},
                ],
                "rules": [
                    {"id": "r1", "name": "高温启动规则", "scope": "e1", "conditions": [{"id": "c1", "field": "当前温度", "operator": ">", "value": 30, "value_type": "number"}]},
                    {"id": "r2", "name": "低温停止规则", "scope": "e1", "conditions": [{"id": "c2", "field": "当前温度", "operator": "<=", "value": 25, "value_type": "number"}]},
                    {"id": "r3", "name": "传感器失联规则", "scope": "e2", "conditions": [{"id": "c3", "field": "连续未上报次数", "operator": ">=", "value": 3, "value_type": "integer"}]},
                ],
                "actions": [
                    {"id": "a1", "name": "启动水冷", "trigger_rules": ["r1"], "targets": ["e3"]},
                    {"id": "a2", "name": "停止水冷", "trigger_rules": ["r2"], "targets": ["e3"]},
                    {"id": "a3", "name": "创建检修工单", "trigger_rules": ["r3"], "outputs": ["e5"], "targets": ["e5"]},
                ],
                "instances": [
                    {"id": "i1", "name": "冷却机组01", "class": "e1"},
                    {"id": "i2", "name": "冷却机组02", "class": "e1"},
                    {"id": "i3", "name": "温度传感器01", "class": "e2"},
                    {"id": "i4", "name": "温度传感器02", "class": "e2"},
                    {"id": "i5", "name": "水冷泵01", "class": "e3"},
                    {"id": "i6", "name": "水冷泵02", "class": "e3"},
                ],
                "action_invocations": [],
            },
            ensure_ascii=False,
        )


def test_standard_workflow_completes_instances_and_removes_no_grounded_class(
    tmp_path,
    monkeypatch,
) -> None:
    client = CoolingWorkflowClient()
    monkeypatch.setattr(cli_module, "create_llm_client", lambda *_args: client)
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
chunk:
  size: 5000
  overlap: 0
instance_postprocess:
  enabled: false
action_builder:
  enabled: false
build_quality:
  chunk_retry_rounds: 1
  instance_retry_rounds: 1
  require_all_chunks: true
  complete_standard_instances: true
  quarantine_orphan_entities: true
""",
        encoding="utf-8",
    )
    output_dir = tmp_path / "run"

    result = CliRunner().invoke(
        cli,
        [
            "build",
            "--input",
            str(FIXTURE),
            "--output-dir",
            str(output_dir),
            "--config",
            str(config_path),
            "--backend",
            "mock",
            "--profile",
            "default",
        ],
    )

    assert result.exit_code == 0, result.output
    formatted = json.loads(
        (output_dir / "formatted_ontology.json").read_text(encoding="utf-8")
    )
    objects = {item["objectName"]: item for item in formatted["objects"]}
    expected = {
        "冷却设备",
        "温度传感器",
        "水冷泵",
        "控制器",
        "检修工单",
        "行动记录",
        "温度统计",
        "水箱",
        "值班人员",
    }
    assert set(objects) == expected
    assert all(objects[name]["realInstanceCount"] >= 1 for name in expected)
    assert all(objects[name]["instanceCoverage"] == "grounded" for name in expected)
    assert not any(instance["generated"] for instance in formatted["instances"])
    assert len(formatted["rules"]) == 3
    assert len(formatted["actions"]) == 3
    quality = json.loads((output_dir / "build_quality.json").read_text(encoding="utf-8"))
    assert quality["status"] == "passed"
    assert quality["uncoveredEntityCount"] == 0
    assert quality["quarantinedEntityCount"] == 0
