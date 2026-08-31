import json

from fastapi.testclient import TestClient

from ontology_construct import api as api_module
from ontology_construct.models import Entity, GlobalOntology, OntologyInstance, Triple
from ontology_construct.storage import save_json_model


def _formatted_ontology_payload() -> dict:
    return {
        "ontology": {
            "ontologyId": 1,
            "ontologyName": "上传本体",
            "ontologyDesc": "从文件上传的本体",
            "profile": "imported",
            "createdAt": "2026-05-28 10:00:00",
            "updatedAt": "2026-05-28 10:00:00",
        },
        "objects": [
            {
                "objectId": 1,
                "objectName": "用户",
                "objectCode": "user",
                "attributes": [
                    {
                        "attributeId": 1,
                        "attributeName": "用户名",
                        "attributeCode": "user_name",
                        "sampleValue": "张三",
                    }
                ],
            },
            {
                "objectId": 2,
                "objectName": "订单",
                "objectCode": "order",
            },
        ],
        "links": [
            {
                "linkId": 1,
                "sourceObjectId": 1,
                "sourceObjectName": "用户",
                "targetObjectId": 2,
                "targetObjectName": "订单",
                "linkInfo": "拥有",
            }
        ],
        "actions": [],
        "instances": [
            {
                "instanceId": 1,
                "instanceCode": "zhangsan",
                "instanceName": "张三",
                "objectId": 1,
                "objectName": "用户",
                "attributeValues": [
                    {
                        "attributeId": 1,
                        "attributeName": "用户名",
                        "attributeCode": "user_name",
                        "value": "张三",
                    }
                ],
                "linkValues": [
                    {
                        "linkId": 1,
                        "linkInfo": "拥有",
                        "targetInstanceId": 2,
                        "targetInstanceName": "订单A",
                        "targetObjectId": 2,
                        "targetObjectName": "订单",
                    }
                ],
            },
            {
                "instanceId": 2,
                "instanceCode": "order_a",
                "instanceName": "订单A",
                "objectId": 2,
                "objectName": "订单",
            },
        ],
        "actionInvocations": [],
    }


def test_api_builds_mock_job_and_serves_graph(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(api_module, "API_OUTPUT_ROOT", tmp_path / "api_jobs")
    client = TestClient(api_module.create_app())

    response = client.post(
        "/ontology/jobs",
        data={
            "backend": "mock",
            "profile": "default",
            "supplemental_prompt": "这是一份测试文档，重点抽取用户和订单。",
        },
        files={"file": ("sample.txt", "用户张三有一个订单。", "text/plain")},
    )

    assert response.status_code == 200
    job_id = response.json()["jobId"]

    status = client.get(f"/ontology/jobs/{job_id}/status")
    assert status.status_code == 200
    assert status.json()["status"] == "completed"
    assert status.json()["supplementalPrompt"] == "这是一份测试文档，重点抽取用户和订单。"

    replay = client.get(f"/ontology/jobs/{job_id}/replay")
    assert replay.status_code == 200
    replay_events = replay.json()["events"]
    assert replay_events
    assert any(event["state"]["status"] == "completed" for event in replay_events)
    assert any("progress" in event["state"] for event in replay_events)

    graph = client.get(f"/ontology/jobs/{job_id}/graph")
    assert graph.status_code == 200
    assert graph.json()["nodes"] == []

    jobs = client.get("/ontology/jobs")
    assert jobs.status_code == 200
    assert jobs.json()["jobs"][0]["jobId"] == job_id
    assert jobs.json()["jobs"][0]["status"] == "completed"
    assert jobs.json()["jobs"][0]["supplementalPrompt"] == "这是一份测试文档，重点抽取用户和订单。"
    assert jobs.json()["jobs"][0]["hasReplay"] is True

    canonical = client.get(f"/ontology/jobs/{job_id}/canonical")
    assert canonical.status_code == 200
    assert canonical.json()["schema"]["object_types"] == []
    assert canonical.json()["graph"]["objects"] == []

    quality = client.get(f"/ontology/jobs/{job_id}/quality")
    assert quality.status_code == 200
    assert quality.json()["status"] == "passed"
    assert quality.json()["failedChunkCount"] == 0


def test_api_profiles_include_frontend_build_defaults(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(api_module, "API_OUTPUT_ROOT", tmp_path / "api_jobs")
    client = TestClient(api_module.create_app())

    response = client.get("/ontology/profiles")

    assert response.status_code == 200
    defaults = response.json()["buildDefaults"]
    assert defaults["defaultProfile"] == "default"
    assert defaults["defaultBackend"] == "deepseek"
    assert defaults["constructionMode"] == "schema-first"
    assert defaults["replayEnabled"] is True
    assert defaults["maxConcepts"] == 50
    assert defaults["semanticClustering"] is True
    assert defaults["instanceMode"] == "real"
    assert defaults["instanceCount"] == 0
    assert defaults["qaContextMode"] == "graph_rag"
    profile_names = {profile["name"] for profile in response.json()["profiles"]}
    assert "industrial_process" in profile_names


def test_job_store_can_disable_replay_events(tmp_path) -> None:
    store = api_module.JobStore(root=tmp_path / "api_jobs", replay_enabled=False)

    job_id = store.create()
    store.update(job_id, {"status": "running", "stage": "测试阶段"})
    store.update(job_id, {"status": "running", "stage": "下一阶段"})
    store.append_event(job_id, {"status": "completed"})

    state = store.read(job_id)
    assert state["stage"] == "下一阶段"
    assert [item["stage"] for item in state["stageHistory"]] == [
        "测试阶段",
        "下一阶段",
    ]
    assert not (store.job_dir(job_id) / "job_events.jsonl").exists()


def test_optional_embedding_missing_key_degrades_instead_of_failing(
    tmp_path,
    monkeypatch,
) -> None:
    store = api_module.JobStore(root=tmp_path / "api_jobs", replay_enabled=False)
    job_id = store.create()
    config = api_module.load_config()
    monkeypatch.setattr(
        api_module,
        "create_embedding_client",
        lambda _config: (_ for _ in ()).throw(RuntimeError("missing key")),
    )

    enabled = api_module._prepare_optional_embedding(
        store=store,
        job_id=job_id,
        config=config,
        requested=True,
    )

    state = store.read(job_id)
    assert enabled is False
    assert state["embeddingStatus"] == "skipped"
    assert state["embeddingWarning"] == "missing key"
    assert config.concept.use_embedding_clustering is False


def test_overall_progress_advances_after_chunk_extraction() -> None:
    chunk_progress = {"percent": 100}

    assert api_module._overall_build_percent(
        {"status": "running", "stage": "候选概念抽取"},
        chunk_progress,
    ) == 35.0
    assert api_module._overall_build_percent(
        {"status": "running", "stage": "独立实例抽取"},
        chunk_progress,
    ) == 70.0
    assert api_module._overall_build_percent(
        {"status": "completed", "stage": "已完成"},
        chunk_progress,
    ) == 100.0


def test_api_builds_schema_first_mock_job(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(api_module, "API_OUTPUT_ROOT", tmp_path / "api_jobs")
    client = TestClient(api_module.create_app())

    response = client.post(
        "/ontology/jobs",
        data={
            "backend": "mock",
            "profile": "default",
            "construction_mode": "schema-first",
            "max_concepts": "20",
            "abstraction_level": "high",
            "extract_real_instances": "true",
        },
        files={"file": ("sample.txt", "设备故障会触发维护工单。", "text/plain")},
    )

    assert response.status_code == 200
    job_id = response.json()["jobId"]
    status = client.get(f"/ontology/jobs/{job_id}/status")
    assert status.status_code == 200
    assert status.json()["status"] == "completed"
    assert status.json()["constructionMode"] == "schema-first"
    assert status.json()["maxConcepts"] == 20
    assert status.json()["extractRealInstances"] is False

    graph = client.get(f"/ontology/jobs/{job_id}/graph")
    assert graph.status_code == 200
    assert graph.json()["nodes"] == []

    jobs = client.get("/ontology/jobs")
    summary = next(job for job in jobs.json()["jobs"] if job["jobId"] == job_id)
    assert summary["constructionMode"] == "schema-first"
    assert summary["maxConcepts"] == 20
    assert summary["extractRealInstances"] is False


def test_api_rebuilds_instances_for_existing_schema_first_job(tmp_path, monkeypatch) -> None:
    root = tmp_path / "api_jobs"
    monkeypatch.setattr(api_module, "API_OUTPUT_ROOT", root)
    client = TestClient(api_module.create_app())
    job_id = "job_manual"
    job_dir = root / job_id
    job_dir.mkdir(parents=True)
    log_path = job_dir / "input_001_process.log"
    log_path.write_text(
        "事件000010 | 订单=0001号整车订单 | 工单=0001号生产工单 | "
        "工序=开卷落料-工序实例02 | 资源=[1号冲压线(1号冲压线)]\n",
        encoding="utf-8",
    )
    ontology = GlobalOntology.from_entities_and_triples(
        [
            Entity(id="order", name="生产订单"),
            Entity(id="work_order", name="生产工单"),
            Entity(id="process", name="加工工序"),
            Entity(id="resource", name="资源"),
            Entity(id="event", name="生产事件"),
        ],
        [Triple(subject_id="order", predicate="包含", object_id="work_order")],
    )
    save_json_model(ontology, job_dir / "concept_global.json")
    (job_dir / "job.json").write_text(
        json.dumps(
            {
                "jobId": job_id,
                "status": "completed",
                "profile": "industrial_process",
                "constructionMode": "schema-first",
                "inputPaths": [str(log_path)],
                "inputNames": [log_path.name],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    response = client.post(f"/ontology/jobs/{job_id}/instances/rebuild")

    assert response.status_code == 200
    status = client.get(f"/ontology/jobs/{job_id}/status").json()
    assert status["status"] == "completed"
    assert status["instanceBuildInstances"] >= 4
    result = client.get(f"/ontology/jobs/{job_id}/result").json()
    instance_names = {instance["instanceName"] for instance in result["instances"]}
    assert "0001号整车订单" in instance_names
    assert "开卷落料-工序实例02" in instance_names


def test_api_question_uses_mock_fallback(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(api_module, "API_OUTPUT_ROOT", tmp_path / "api_jobs")
    client = TestClient(api_module.create_app())
    create_response = client.post(
        "/ontology/jobs",
        data={"backend": "mock", "profile": "default"},
        files={"file": ("sample.txt", "用户张三有一个订单。", "text/plain")},
    )
    job_id = create_response.json()["jobId"]

    response = client.post(
        f"/ontology/jobs/{job_id}/question",
        json={"question": "有哪些对象？", "backend": "mock"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["answer"]["summary"]
    assert "highlight" in payload
    assert "_qa_log" not in payload["meta"]

    qa_log_path = tmp_path / "api_jobs" / job_id / "qa_events.jsonl"
    assert qa_log_path.exists()
    qa_events = [
        json.loads(line)
        for line in qa_log_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(qa_events) == 1
    assert qa_events[0]["question"] == "有哪些对象？"
    assert qa_events[0]["backend"] == "mock"
    assert qa_events[0]["success"] is True
    assert "有哪些对象？" in qa_events[0]["prompt"]
    assert qa_events[0]["rawAnswer"]
    assert qa_events[0]["parsedAnswer"]["summary"]


def test_api_question_skips_qa_log_when_disabled(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(api_module, "API_OUTPUT_ROOT", tmp_path / "api_jobs")
    config = api_module.load_config()
    config.qa_logging.enabled = False
    monkeypatch.setattr(api_module, "load_config", lambda: config)
    client = TestClient(api_module.create_app())
    create_response = client.post(
        "/ontology/jobs",
        data={"backend": "mock", "profile": "default"},
        files={"file": ("sample.txt", "用户张三有一个订单。", "text/plain")},
    )
    job_id = create_response.json()["jobId"]

    response = client.post(
        f"/ontology/jobs/{job_id}/question",
        json={"question": "有哪些对象？", "backend": "mock"},
    )

    assert response.status_code == 200
    assert not (tmp_path / "api_jobs" / job_id / "qa_events.jsonl").exists()


def test_api_builds_mock_job_from_multiple_files(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(api_module, "API_OUTPUT_ROOT", tmp_path / "api_jobs")
    client = TestClient(api_module.create_app())

    response = client.post(
        "/ontology/jobs",
        data={"backend": "mock", "profile": "default"},
        files=[
            ("files", ("first.txt", "第一份文档。", "text/plain")),
            ("files", ("second.md", "第二份文档。", "text/markdown")),
        ],
    )

    assert response.status_code == 200
    job_id = response.json()["jobId"]
    status = client.get(f"/ontology/jobs/{job_id}/status")
    assert status.status_code == 200
    assert status.json()["status"] == "completed"
    assert status.json()["progress"]["totalChunks"] == 2

    jobs = client.get("/ontology/jobs").json()["jobs"]
    summary = next(job for job in jobs if job["jobId"] == job_id)
    assert summary["fileCount"] == 2
    assert summary["inputNames"] == [
        "input_001_first.txt",
        "input_002_second.md",
    ]


def test_api_builds_mock_job_from_log_file(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(api_module, "API_OUTPUT_ROOT", tmp_path / "api_jobs")
    client = TestClient(api_module.create_app())

    response = client.post(
        "/ontology/jobs",
        data={"backend": "mock", "profile": "default"},
        files={"file": ("service.log", "2026-06-22 ERROR 设备离线", "text/plain")},
    )

    assert response.status_code == 200
    job_id = response.json()["jobId"]
    status = client.get(f"/ontology/jobs/{job_id}/status")
    assert status.status_code == 200
    assert status.json()["status"] == "completed"
    assert status.json()["inputNames"] == ["input_001_service.log"]


def test_api_builds_mock_job_from_json_and_jsonl(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(api_module, "API_OUTPUT_ROOT", tmp_path / "api_jobs")
    client = TestClient(api_module.create_app())

    response = client.post(
        "/ontology/jobs",
        data={"backend": "mock", "profile": "default"},
        files=[
            ("files", ("records.json", '{"users":[{"name":"张三"}]}', "application/json")),
            (
                "files",
                ("events.jsonl", '{"event":"下单"}\n{"event":"支付"}\n', "application/jsonl"),
            ),
        ],
    )

    assert response.status_code == 200
    job_id = response.json()["jobId"]
    status = client.get(f"/ontology/jobs/{job_id}/status")
    assert status.status_code == 200
    assert status.json()["status"] == "completed"

    jobs = client.get("/ontology/jobs").json()["jobs"]
    summary = next(job for job in jobs if job["jobId"] == job_id)
    assert summary["fileCount"] == 2
    assert summary["inputNames"] == [
        "input_001_records.json",
        "input_002_events.jsonl",
    ]


def test_api_imports_formatted_ontology_and_serves_question(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(api_module, "API_OUTPUT_ROOT", tmp_path / "api_jobs")
    client = TestClient(api_module.create_app())

    response = client.post(
        "/ontology/jobs/import",
        files={
            "file": (
                "formatted_ontology.json",
                json.dumps(_formatted_ontology_payload(), ensure_ascii=False),
                "application/json",
            )
        },
    )

    assert response.status_code == 200
    job_id = response.json()["jobId"]

    status = client.get(f"/ontology/jobs/{job_id}/status")
    assert status.status_code == 200
    assert status.json()["status"] == "completed"
    assert status.json()["source"] == "uploaded_ontology"

    graph = client.get(f"/ontology/jobs/{job_id}/graph?view=schema")
    assert graph.status_code == 200
    assert [node["label"] for node in graph.json()["nodes"]] == ["用户", "订单"]
    assert graph.json()["edges"][0]["rel"] == "拥有"

    question = client.post(
        f"/ontology/jobs/{job_id}/question",
        json={"question": "张三拥有什么？", "backend": "mock", "graphView": "instance"},
    )
    assert question.status_code == 200
    assert question.json()["answer"]["summary"]

    jobs = client.get("/ontology/jobs").json()["jobs"]
    summary = next(job for job in jobs if job["jobId"] == job_id)
    assert summary["ontologyName"] == "上传本体"
    assert summary["inputNames"] == ["ontology_formatted_ontology.json"]


def test_api_question_strategy_is_bound_to_backend(tmp_path, monkeypatch) -> None:
    class CapturingClient:
        def __init__(self, backend: str | None):
            self.backend = backend
            self.prompt = ""

        async def complete(self, prompt: str, *, system_prompt: str | None = None) -> str:
            self.prompt = prompt
            return json.dumps(
                {
                    "think": [],
                    "highlight": {"nodes": [], "edges": []},
                    "answer": {"summary": "ok"},
                },
                ensure_ascii=False,
            )

    captured: list[CapturingClient] = []

    def fake_create_llm_client(config, backend=None):
        client = CapturingClient(backend)
        captured.append(client)
        return client

    monkeypatch.setattr(api_module, "API_OUTPUT_ROOT", tmp_path / "api_jobs")
    monkeypatch.setattr(api_module, "create_llm_client", fake_create_llm_client)
    client = TestClient(api_module.create_app())
    response = client.post(
        "/ontology/jobs/import",
        files={
            "file": (
                "formatted_ontology.json",
                json.dumps(_formatted_ontology_payload(), ensure_ascii=False),
                "application/json",
            )
        },
    )
    job_id = response.json()["jobId"]

    cases = [
        ("jiutian", "lom_subgraph", "[CORE_EVIDENCE]", "[EVIDENCE_SUBGRAPH]"),
        ("deepseek", "full_context", "[SCHEMA]", "[CORE_EVIDENCE]"),
        ("kimi", "full_context", "[SCHEMA]", "[CORE_EVIDENCE]"),
        ("jiutian-35b", "weak_graph_rag", "[EVIDENCE_SUBGRAPH]", "[CORE_EVIDENCE]"),
        ("qwen-plus", "lom_subgraph", "[CORE_EVIDENCE]", "[EVIDENCE_SUBGRAPH]"),
    ]
    for backend, strategy, expected, unexpected in cases:
        result = client.post(
            f"/ontology/jobs/{job_id}/question",
            json={"question": "张三有哪些订单？", "backend": backend},
        )
        assert result.status_code == 200
        payload = result.json()
        assert payload["meta"]["backend"] == backend
        assert payload["meta"]["qa_strategy"] == strategy
        assert expected in captured[-1].prompt
        assert unexpected not in captured[-1].prompt


def test_api_imports_formatted_ontology_jsonl(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(api_module, "API_OUTPUT_ROOT", tmp_path / "api_jobs")
    client = TestClient(api_module.create_app())
    payload = _formatted_ontology_payload()
    rule = {
        "ruleId": 1,
        "ruleName": "大额订单规则",
        "ruleCode": "large_order",
        "scopeObjectId": 2,
        "scopeObjectName": "订单",
        "logic": "AND",
        "conditions": [
            {
                "conditionId": "c1",
                "field": "金额",
                "operator": ">",
                "value": 10000,
                "valueType": "number",
            }
        ],
        "actionIds": [1],
        "actionNames": ["人工复核"],
    }
    action = {
        "actionId": 1,
        "actionName": "人工复核",
        "actionCode": "manual_review",
        "triggerRuleIds": [1],
        "triggerRules": ["大额订单规则"],
        "targetObjects": ["订单"],
    }
    jsonl = "\n".join(
        json.dumps(record, ensure_ascii=False)
        for record in [
            {"ontology": payload["ontology"]},
            {"type": "object", "data": payload["objects"][0]},
            {"type": "object", "data": payload["objects"][1]},
            {"links": payload["links"]},
            {"type": "rule", "data": rule},
            {"type": "action", "data": action},
            {"instances": payload["instances"]},
            {"actionInvocations": payload["actionInvocations"]},
        ]
    )

    response = client.post(
        "/ontology/jobs/import",
        files={"file": ("formatted_ontology.jsonl", jsonl, "application/jsonl")},
    )

    assert response.status_code == 200
    job_id = response.json()["jobId"]
    graph = client.get(f"/ontology/jobs/{job_id}/graph?view=schema")

    assert graph.status_code == 200
    assert [node["label"] for node in graph.json()["nodes"]] == [
        "用户",
        "订单",
        "大额订单规则",
        "人工复核",
    ]
    assert {edge["rel"] for edge in graph.json()["edges"]} >= {
        "适用规则",
        "触发",
        "作用于",
    }
    jobs = client.get("/ontology/jobs").json()["jobs"]
    summary = next(job for job in jobs if job["jobId"] == job_id)
    assert summary["inputNames"] == ["ontology_formatted_ontology.jsonl"]
    assert summary["ruleCount"] == 1


def test_api_accepts_structured_config_yaml(tmp_path, monkeypatch) -> None:
    async def fake_aclose(_client):
        return None

    class FakeLLM:
        async def complete(self, *_args, **_kwargs):
            return '{"objects": []}'

    monkeypatch.setattr(api_module, "API_OUTPUT_ROOT", tmp_path / "api_jobs")
    monkeypatch.setattr(
        api_module,
        "create_embedding_client",
        lambda _config: object(),
    )
    monkeypatch.setattr(
        api_module,
        "create_llm_client",
        lambda _config: FakeLLM(),
    )
    monkeypatch.setattr(api_module, "aclose_llm_client", fake_aclose)
    monkeypatch.setattr(
        api_module,
        "build_structured_ontology",
        lambda _config, **_kwargs: (
            GlobalOntology.from_entities_and_triples(
                [Entity(id="orders", name="订单", attributes={"说明": "订单"})],
                [],
                instances=[
                    OntologyInstance(
                        id="orders:ORD-001",
                        name="0001号订单",
                        class_id="orders",
                    )
                ],
            ),
            type(
                "Stats",
                (),
                    {
                        "object_count": 1,
                        "schema_relation_count": 0,
                        "instance_count": 1,
                        "instance_link_count": 0,
                        "profile_table_count": 1,
                        "candidate_class_count": 1,
                        "candidate_relation_count": 0,
                        "mapped_class_count": 1,
                        "mapped_relation_count": 0,
                        "database_profile": {"tables": []},
                        "structured_candidates": {"classes": []},
                        "structured_mapping": {"classes": []},
                    },
                )(),
            ),
    )
    client = TestClient(api_module.create_app())

    response = client.post(
        "/ontology/structured-configs",
        files={
            "file": (
                "pg_source.yaml",
                (
                    "version: 1\n"
                    "source:\n"
                    "  type: postgresql\n"
                    "  connection:\n"
                    "    host: localhost\n"
                    "    database: factory\n"
                    "    user: readonly\n"
                ),
                "application/x-yaml",
            )
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "queued"
    assert payload["inputName"] == "structured_config_pg_source.yaml"
    assert payload["configKeys"] == ["source", "version"]

    status = client.get(f"/ontology/jobs/{payload['jobId']}/status")
    assert status.status_code == 200
    assert status.json()["source"] == "structured_config"
    assert status.json()["status"] == "completed"
    assert status.json()["profileTableCount"] == 1
    job_dir = tmp_path / "api_jobs" / payload["jobId"]
    assert (job_dir / "database_profile.json").exists()
    assert (job_dir / "structured_candidates.json").exists()
    assert (job_dir / "structured_mapping.json").exists()
    graph = client.get(f"/ontology/jobs/{payload['jobId']}/graph?view=instance")
    assert graph.status_code == 200
    assert graph.json()["nodes"]
    canonical = client.get(f"/ontology/jobs/{payload['jobId']}/canonical")
    assert canonical.status_code == 200
    assert canonical.json()["graph"]["objects"]


def test_api_rejects_invalid_structured_config_yaml(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(api_module, "API_OUTPUT_ROOT", tmp_path / "api_jobs")
    client = TestClient(api_module.create_app())

    response = client.post(
        "/ontology/structured-configs",
        files={"file": ("pg_source.yaml", "- just\n- a list\n", "application/x-yaml")},
    )

    assert response.status_code == 400
    assert "non-empty mapping" in response.json()["detail"]


def test_api_reports_structured_ai_environment_failure(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(api_module, "API_OUTPUT_ROOT", tmp_path / "api_jobs")
    monkeypatch.setattr(
        api_module,
        "create_embedding_client",
        lambda _config: (_ for _ in ()).throw(RuntimeError("Missing embedding API key environment variable")),
    )
    client = TestClient(api_module.create_app())

    response = client.post(
        "/ontology/structured-configs",
        files={
            "file": (
                "pg_source.yaml",
                (
                    "version: 1\n"
                    "source:\n"
                    "  type: postgresql\n"
                    "  connection:\n"
                    "    host: localhost\n"
                    "    database: factory\n"
                    "    user: readonly\n"
                ),
                "application/x-yaml",
            )
        },
    )

    assert response.status_code == 200
    status = client.get(f"/ontology/jobs/{response.json()['jobId']}/status")
    assert status.status_code == 200
    assert status.json()["status"] == "failed"
    assert "Missing embedding API key" in status.json()["error"]


def test_api_reports_extraction_failure_before_merge(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(api_module, "API_OUTPUT_ROOT", tmp_path / "api_jobs")

    def fail_extract(chunks, output_dir, **_kwargs):
        from ontology_construct.storage import ProgressTracker

        tracker = ProgressTracker(output_dir / "progress.sqlite")
        try:
            tracker.register_chunks(chunks)
            for chunk in chunks:
                tracker.mark_failed(chunk.id, "RuntimeError: LLM unavailable")
        finally:
            tracker.close()
        (output_dir / "local").mkdir(parents=True, exist_ok=True)
        return 0, len(chunks), 0

    monkeypatch.setattr(api_module, "_run_extract", fail_extract)
    client = TestClient(api_module.create_app())

    response = client.post(
        "/ontology/jobs",
        data={"backend": "mock", "profile": "default"},
        files={"file": ("sample.txt", "用户张三有一个订单。", "text/plain")},
    )

    assert response.status_code == 200
    job_id = response.json()["jobId"]
    status = client.get(f"/ontology/jobs/{job_id}/status")

    assert status.status_code == 200
    payload = status.json()
    assert payload["status"] == "failed"
    assert "Incomplete ontology extraction" in payload["error"]
    assert "1/1 chunks failed after workflow retries" in payload["error"]
    assert "RuntimeError: LLM unavailable" in payload["error"]
