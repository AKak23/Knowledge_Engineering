import json

from fastapi.testclient import TestClient

from ontology_construct import api as api_module
from ontology_construct.ontology_update import (
    build_update_plan,
    merge_update_selection,
)
from ontology_construct.output.formatted_ontology import FormattedOntology
from ontology_construct.storage import save_json_model


def _ontology_payload(*, candidate: bool = False) -> dict:
    objects = [
        {
            "objectId": 1,
            "objectName": "客户",
            "objectCode": "customer",
            "description": "已有客户定义",
            "attributes": [
                {
                    "attributeId": 1,
                    "attributeName": "姓名",
                    "attributeCode": "name",
                    "sampleValue": "张三",
                }
            ],
            "sourceChunks": ["base_001"],
        }
    ]
    links = []
    rules = []
    actions = []
    instances = []
    if candidate:
        objects[0] = {
            **objects[0],
            "description": "候选文档中的客户定义",
            "attributes": [
                *objects[0]["attributes"],
                {
                    "attributeId": 2,
                    "attributeName": "流失概率",
                    "attributeCode": "churn_probability",
                    "dataType": "number",
                    "sampleValue": 0.82,
                },
            ],
            "sourceChunks": ["update_001"],
        }
        objects.append(
            {
                "objectId": 2,
                "objectName": "流失风险",
                "objectCode": "churn_risk",
                "attributes": [],
                "sourceChunks": ["update_001"],
            }
        )
        links = [
            {
                "linkId": 1,
                "sourceObjectId": 1,
                "sourceObjectName": "客户",
                "targetObjectId": 2,
                "targetObjectName": "流失风险",
                "linkInfo": "产生",
                "sourceChunks": ["update_001"],
            }
        ]
        rules = [
            {
                "ruleId": 1,
                "ruleName": "高流失风险判定",
                "ruleCode": "high_churn_risk",
                "scopeObjectId": 2,
                "scopeObjectName": "流失风险",
                "logic": "AND",
                "conditions": [
                    {
                        "conditionId": "c1",
                        "field": "概率",
                        "operator": ">",
                        "value": 0.8,
                        "valueType": "number",
                    }
                ],
                "sourceChunks": ["update_001"],
            }
        ]
        actions = [
            {
                "actionId": 1,
                "actionName": "创建客户挽留任务",
                "actionCode": "create_retention_task",
                "triggerRuleIds": [1],
                "triggerRules": ["高流失风险判定"],
                "targetObjectIds": [1],
                "targetObjects": ["客户"],
                "actionType": "task",
                "actionPlan": "交给客户经理复核",
                "sourceChunks": ["update_001"],
            }
        ]
        instances = [
            {
                "instanceId": 1,
                "instanceCode": "risk_zhangsan",
                "instanceName": "张三流失风险",
                "objectId": 2,
                "objectName": "流失风险",
                "sourceChunks": ["update_001"],
            }
        ]
    return {
        "ontology": {
            "ontologyId": 111,
            "ontologyName": "客户运营本体",
            "ontologyDesc": "客户运营与流失风险",
            "profile": "default",
            "createdAt": "2026-09-04 10:00:00",
            "updatedAt": "2026-09-04 10:00:00",
        },
        "objects": objects,
        "links": links,
        "rules": rules,
        "actions": actions,
        "instances": instances,
        "actionInvocations": [],
    }


def test_update_plan_hard_filters_unselected_scopes() -> None:
    base = FormattedOntology.model_validate(_ontology_payload())
    candidate = FormattedOntology.model_validate(_ontology_payload(candidate=True))

    plan = build_update_plan(base, candidate, scopes=["objects", "relations"])

    assert {item["kind"] for item in plan["changes"]} == {"object", "relation"}
    assert plan["counts"]["rules"] == 0
    assert plan["counts"]["actions"] == 0
    assert plan["safety"]["additiveOnly"] is True


def test_update_merge_is_non_destructive_and_resolves_dependencies() -> None:
    base = FormattedOntology.model_validate(_ontology_payload())
    candidate = FormattedOntology.model_validate(_ontology_payload(candidate=True))
    plan = build_update_plan(
        base,
        candidate,
        scopes=["objects", "relations", "rules", "actions"],
    )
    action_change = next(item for item in plan["changes"] if item["kind"] == "action")

    merged, applied = merge_update_selection(
        base,
        candidate,
        plan,
        [action_change["changeId"]],
    )

    assert len(applied) == 3  # action + its new rule and object dependencies
    assert [item.objectName for item in merged.objects] == ["客户", "流失风险"]
    assert merged.objects[0].description == "已有客户定义"
    assert [item.attributeName for item in merged.objects[0].attributes] == ["姓名"]
    assert merged.rules[0].scopeObjectId == merged.objects[1].objectId
    assert merged.actions[0].triggerRuleIds == [merged.rules[0].ruleId]
    assert merged.actions[0].targetObjectIds == [merged.objects[0].objectId]
    assert merged.objects[1].instanceCount == 1


def test_update_api_lists_sources_previews_and_confirms_new_version(
    tmp_path, monkeypatch
) -> None:
    root = tmp_path / "api_jobs"
    monkeypatch.setattr(api_module, "API_OUTPUT_ROOT", root)
    client = TestClient(api_module.create_app())
    imported = client.post(
        "/ontology/jobs/import",
        files={
            "file": (
                "customer.json",
                json.dumps(_ontology_payload(), ensure_ascii=False),
                "application/json",
            )
        },
    )
    assert imported.status_code == 200
    base_job_id = imported.json()["jobId"]

    sources = client.get(f"/ontology/jobs/{base_job_id}/sources")
    assert sources.status_code == 200
    assert sources.json()["documents"][0]["name"] == "customer.json"
    assert {item["documentId"] for item in sources.json()["builtinDocuments"]} == {
        "cooling-system",
        "greenhouse-tomato",
    }

    store = api_module.JobStore(root=root, replay_enabled=False)
    update_id = store.create()
    candidate = FormattedOntology.model_validate(_ontology_payload(candidate=True))
    save_json_model(candidate, store.job_dir(update_id) / "formatted_ontology.json")
    base = FormattedOntology.model_validate(_ontology_payload())
    plan = build_update_plan(
        base,
        candidate,
        scopes=["objects", "relations", "rules", "actions"],
        source_documents=[{"name": "risk.txt", "kind": "uploaded"}],
    )
    (store.job_dir(update_id) / "update_plan.json").write_text(
        json.dumps(plan, ensure_ascii=False), encoding="utf-8"
    )
    store.update(
        update_id,
        {
            "status": "completed",
            "baseJobId": base_job_id,
            "hiddenFromHistory": True,
            "sourceDocuments": [{"name": "risk.txt", "kind": "uploaded"}],
        },
    )
    selected = [item["changeId"] for item in plan["changes"]]

    preview = client.post(
        f"/ontology/jobs/{base_job_id}/updates/{update_id}/preview",
        json={"changeIds": selected},
    )
    assert preview.status_code == 200
    assert len(preview.json()["graph"]["nodes"]) >= 4

    confirmed = client.post(
        f"/ontology/jobs/{base_job_id}/updates/{update_id}/confirm",
        json={"changeIds": selected},
    )
    assert confirmed.status_code == 200
    version_job_id = confirmed.json()["jobId"]
    assert version_job_id != base_job_id
    result = client.get(f"/ontology/jobs/{version_job_id}/result")
    assert result.status_code == 200
    assert [item["objectName"] for item in result.json()["objects"]] == [
        "客户",
        "流失风险",
    ]
    jobs = client.get("/ontology/jobs").json()["jobs"]
    assert update_id not in {item["jobId"] for item in jobs}
    assert version_job_id in {item["jobId"] for item in jobs}


def test_update_api_runs_document_analysis_with_backend_scope_constraint(
    tmp_path, monkeypatch
) -> None:
    root = tmp_path / "api_jobs"
    monkeypatch.setattr(api_module, "API_OUTPUT_ROOT", root)
    client = TestClient(api_module.create_app())
    imported = client.post(
        "/ontology/jobs/import",
        files={
            "file": (
                "customer.json",
                json.dumps(_ontology_payload(), ensure_ascii=False),
                "application/json",
            )
        },
    )
    base_job_id = imported.json()["jobId"]

    created = client.post(
        f"/ontology/jobs/{base_job_id}/updates",
        data={
            "backend": "mock",
            "builtin_document_ids": json.dumps(["cooling-system"]),
            "scopes": json.dumps(["objects"]),
        },
    )

    assert created.status_code == 202
    update_id = created.json()["updateId"]
    status = client.get(f"/ontology/jobs/{update_id}/status")
    assert status.status_code == 200
    assert status.json()["status"] == "completed"
    assert status.json()["updateScopes"] == ["objects"]
    assert "不得产出或推断" in status.json()["supplementalPrompt"]
    plan = client.get(
        f"/ontology/jobs/{base_job_id}/updates/{update_id}/plan"
    )
    assert plan.status_code == 200
    assert plan.json()["plan"]["scopes"] == ["objects"]
    assert all(
        item["kind"] == "object" for item in plan.json()["plan"]["changes"]
    )
