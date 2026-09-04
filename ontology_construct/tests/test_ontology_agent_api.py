import json

from fastapi.testclient import TestClient

from ontology_construct import api as api_module


def _ontology_payload() -> dict:
    return {
        "ontology": {
            "ontologyId": 1111,
            "ontologyName": "客户运营本体",
            "ontologyDesc": "客户、区域与订单关系",
            "profile": "imported",
            "createdAt": "2026-09-02 10:00:00",
            "updatedAt": "2026-09-02 10:00:00",
        },
        "objects": [
            {
                "objectId": 1,
                "objectName": "客户",
                "objectCode": "customer",
            },
            {
                "objectId": 2,
                "objectName": "区域",
                "objectCode": "region",
            },
        ],
        "links": [
            {
                "linkId": 1,
                "sourceObjectId": 1,
                "sourceObjectName": "客户",
                "targetObjectId": 2,
                "targetObjectName": "区域",
                "linkInfo": "位于",
            }
        ],
        "rules": [],
        "actions": [],
        "instances": [],
        "actionInvocations": [],
    }


def _create_job(client: TestClient) -> str:
    response = client.post(
        "/ontology/jobs/import",
        files={
            "file": (
                "customer_ontology.json",
                json.dumps(_ontology_payload(), ensure_ascii=False),
                "application/json",
            )
        },
    )
    assert response.status_code == 200
    return response.json()["jobId"]


def _agent_payload(job_id: str) -> dict:
    return {
        "name": "客户洞察智能体团队",
        "description": "识别客户价值群、流失风险与行动机会。",
        "collaborationPrompt": "协同成员先独立分析，主智能体再依据本体证据综合。",
        "ontologyJobId": job_id,
        "members": [
            {
                "agentId": "insight-analysis",
                "responsibility": "负责对象画像与关系洞察",
            },
            {
                "agentId": "location-analysis",
                "responsibility": "负责区域差异与区位影响",
            },
        ],
        "externalCallEnabled": True,
        "backend": "mock",
    }


def test_ontology_agent_lifecycle_and_pi_conversation(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(api_module, "API_OUTPUT_ROOT", tmp_path / "api_jobs")
    client = TestClient(api_module.create_app())
    job_id = _create_job(client)

    catalog = client.get("/ontology/agent-catalog")
    assert catalog.status_code == 200
    assert {item["agentId"] for item in catalog.json()["agents"]} >= {
        "insight-analysis",
        "location-analysis",
        "data-questioning",
    }
    assert all("systemPrompt" not in item for item in catalog.json()["agents"])

    created = client.post("/ontology/agents", json=_agent_payload(job_id))
    assert created.status_code == 201
    agent = created.json()
    agent_id = agent["agentId"]
    assert agent["status"] == "draft"
    assert agent["revision"] == 1
    assert agent["ontologyContext"]["jobId"] == job_id
    assert agent["ontologyContext"]["ontologyId"] == "1111"
    assert agent["ontologyContext"]["objectCount"] == 2

    listed = client.get("/ontology/agents", params={"ontology_job_id": job_id})
    assert listed.status_code == 200
    assert [item["agentId"] for item in listed.json()["agents"]] == [agent_id]

    session = client.post(f"/ontology/agents/{agent_id}/sessions")
    assert session.status_code == 201
    session_id = session.json()["sessionId"]
    answer = client.post(
        f"/ontology/agents/{agent_id}/sessions/{session_id}/messages",
        json={"message": "请分析客户与区域的关系。", "backend": "mock"},
    )
    assert answer.status_code == 200
    answer_payload = answer.json()
    assert answer_payload["message"]["role"] == "assistant"
    assert answer_payload["message"]["content"]
    assert answer_payload["answer"]["meta"]["agent_runtime"] == "pi-agent"
    assert answer_payload["answer"]["meta"]["tool_execution"] == "parallel"
    assert answer_payload["answer"]["meta"]["ontology_version"] == job_id
    assert [item["status"] for item in answer_payload["collaboration"]] == [
        "completed",
        "completed",
    ]
    assert len(answer_payload["session"]["messages"]) == 2

    sessions = client.get(f"/ontology/agents/{agent_id}/sessions")
    assert sessions.status_code == 200
    assert sessions.json()["sessions"][0]["title"].startswith("请分析")


def test_ontology_agent_revision_publish_and_external_auth(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(api_module, "API_OUTPUT_ROOT", tmp_path / "api_jobs")
    client = TestClient(api_module.create_app())
    job_id = _create_job(client)
    created = client.post("/ontology/agents", json=_agent_payload(job_id)).json()
    agent_id = created["agentId"]

    update_payload = _agent_payload(job_id)
    update_payload.update({"revision": 1, "description": "更新后的智能体描述"})
    updated = client.put(f"/ontology/agents/{agent_id}", json=update_payload)
    assert updated.status_code == 200
    assert updated.json()["revision"] == 2
    assert updated.json()["status"] == "draft"

    stale = client.put(f"/ontology/agents/{agent_id}", json=update_payload)
    assert stale.status_code == 409
    assert stale.json()["detail"]["currentRevision"] == 2

    published = client.post(f"/ontology/agents/{agent_id}/publish")
    assert published.status_code == 200
    publication = published.json()
    assert publication["agent"]["status"] == "published"
    assert publication["agent"]["publishedVersion"] == 1
    assert publication["invokePath"].startswith("/v1/ontology-agents/")
    access_key = publication["accessKey"]
    assert access_key.startswith("knova_")
    assert "accessKeyHash" not in publication["agent"]

    missing_key = client.post(
        publication["invokePath"],
        json={"message": "有哪些本体对象？", "backend": "mock"},
    )
    assert missing_key.status_code == 401

    invoked = client.post(
        publication["invokePath"],
        headers={"X-Agent-Key": access_key},
        json={"message": "有哪些本体对象？", "backend": "mock"},
    )
    assert invoked.status_code == 200
    assert invoked.json()["answer"]["meta"]["agent_runtime"] == "pi-agent"


def test_ontology_agent_rejects_unknown_member_and_unfinished_context(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(api_module, "API_OUTPUT_ROOT", tmp_path / "api_jobs")
    client = TestClient(api_module.create_app())
    job_id = _create_job(client)
    unknown_member = _agent_payload(job_id)
    unknown_member["members"] = [
        {"agentId": "untrusted-agent", "responsibility": "尝试越界加入"}
    ]

    rejected_member = client.post("/ontology/agents", json=unknown_member)
    assert rejected_member.status_code == 422

    missing_context = _agent_payload("job_missing")
    rejected_context = client.post("/ontology/agents", json=missing_context)
    assert rejected_context.status_code == 404
