from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
import json
import os
import time
from threading import Lock
from typing import Any, Literal
from uuid import uuid4

import click
import yaml
from fastapi import BackgroundTasks, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ontology_construct.cli import (
    _apply_action_builder,
    _apply_build_quality,
    _apply_llm_overrides,
    _apply_profile_overrides,
    _apply_instance_builder,
    _apply_instance_postprocess,
    _compose_supplemental,
    _limit_extracted_instances,
    _missing_local_ontologies_message,
    _json_model_files,
    _merge_local_ontologies,
    _prepare_chunks_for_inputs,
    _run_extract,
    _run_schema_first_build,
    _save_build_outputs,
    _schema_identity,
    _ensure_complete_chunk_extraction,
)
from ontology_construct.config import QAConfig, load_config, project_root
from ontology_construct.embedding import create_embedding_client
from ontology_construct.input.text_reader import SUPPORTED_TEXT_SUFFIXES
from ontology_construct.llm import aclose_llm_client, create_llm_client
from ontology_construct.models import GlobalOntology
from ontology_construct.ontology_agent import (
    AgentInvocationRequest,
    AgentStore,
    OntologyAgentCreate,
    OntologyAgentRecord,
    OntologyAgentUpdate,
    OntologyContextSnapshot,
    OntologyPiAgentRuntime,
    RevisionConflict,
    new_message,
    public_agent_payload,
    public_catalog_payload,
)
from ontology_construct.ontology_update import (
    UPDATE_SCOPES,
    build_update_plan,
    merge_update_selection,
    normalize_update_scopes,
)
from ontology_construct.output.formatted_ontology import FormattedOntology
from ontology_construct.output import build_canonical_ontology, format_global_ontology
from ontology_construct.qa import (
    QAContextMode,
    answer_question,
    formatted_ontology_to_graph,
)
from ontology_construct.storage import ProgressTracker, load_json_model, save_json_model
from ontology_construct.structured_builder import (
    build_structured_ontology,
    load_structured_build_config,
    polish_structured_schema,
)


API_OUTPUT_ROOT = project_root() / "outputs" / "api_jobs"
ONTOLOGY_IMPORT_SUFFIXES = {".json", ".jsonl"}
STRUCTURED_CONFIG_SUFFIXES = {".yaml", ".yml"}
BUILTIN_UPDATE_DOCUMENTS = {
    "cooling-system": {
        "name": "冷却系统运行规程",
        "description": "工业冷却设备、传感器、工单、判定规则与联动行动。",
        "path": project_root() / "tests" / "fixtures" / "phase2_cooling_system.txt",
    },
    "greenhouse-tomato": {
        "name": "设施番茄水肥一体化规程",
        "description": "种植区、作物、环境传感器、水肥设备与农事行动。",
        "path": project_root() / "tests" / "fixtures" / "phase2_greenhouse_tomato.txt",
    },
}


@dataclass(frozen=True)
class QAStrategy:
    name: str
    context_mode: QAContextMode
    qa_config: QAConfig


def resolve_qa_strategy(
    *,
    backend: str | None,
    requested_context_mode: QAContextMode,
    qa_config: QAConfig,
) -> QAStrategy:
    selected = (backend or "").strip()
    if selected in {"deepseek", "kimi"}:
        return QAStrategy(
            name="full_context",
            context_mode="direct",
            qa_config=qa_config,
        )
    if selected == "jiutian-35b":
        return QAStrategy(
            name="weak_graph_rag",
            context_mode="graph_rag",
            qa_config=qa_config.model_copy(
                update={"evidence_contract": "graph_rag_v1"}
            ),
        )
    if selected == "mock":
        return QAStrategy(
            name="requested",
            context_mode=requested_context_mode,
            qa_config=qa_config,
        )
    return QAStrategy(
        name="lom_subgraph",
        context_mode="graph_rag",
        qa_config=qa_config.model_copy(
            update={"evidence_contract": "graph_qa_evidence_v1"}
        ),
    )


class QuestionRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    question: str = Field(min_length=1)
    backend: str | None = None
    compare_session_id: str | None = Field(default=None, alias="compareSessionId")
    compare_slot: int | None = Field(default=None, alias="compareSlot")
    graph_view: Literal["auto", "schema", "instance"] = Field(
        default="auto",
        alias="graphView",
    )
    context_mode: QAContextMode = Field(default="graph_rag", alias="contextMode")
    fallback_enabled: bool = Field(default=True, alias="fallbackEnabled")


class UpdateSelectionRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    change_ids: list[str] = Field(alias="changeIds", min_length=1)


def _event_state(state: dict[str, object]) -> dict[str, object]:
    allowed_keys = {
        "jobId",
        "status",
        "stage",
        "stageHint",
        "stageUpdatedAt",
        "stageHistory",
        "profile",
        "inputNames",
        "constructionMode",
        "progress",
        "extracted",
        "skipped",
        "failed",
        "error",
        "objectCount",
        "linkCount",
        "instanceCount",
        "instanceLinkCount",
        "profileTableCount",
        "candidateClassCount",
        "candidateRelationCount",
        "mappedClassCount",
        "mappedRelationCount",
        "instanceBuildMode",
        "instanceBuildInputFiles",
        "instanceBuildTextFiles",
        "instanceBuildLlmEnabled",
        "instanceBuildMaxLogRecordsPerFile",
        "instanceBuildMaxLogRecordsPerTemplate",
        "instanceBuildLlmChunkBatchSize",
        "instanceBuildLlmConcurrency",
        "instanceBuildCandidates",
        "instanceBuildInstances",
        "instanceBuildLinks",
        "instanceBuildFailedBatches",
        "instanceBuildSkippedCandidates",
        "instancePostprocessLinks",
        "instancePostprocessDropped",
        "actionBuildActions",
        "actionBuildEvents",
        "qualityStatus",
        "qualityWarnings",
        "qualityCoveredEntities",
        "qualityUncoveredEntities",
        "qualityQuarantinedEntities",
        "embeddingStatus",
        "embeddingBackend",
        "embeddingModel",
        "embeddingWarning",
        "baseJobId",
        "updateScopes",
        "planReady",
        "changeCount",
        "addedChangeCount",
        "modifiedChangeCount",
    }
    return {key: value for key, value in state.items() if key in allowed_keys}


class JobStore:
    def __init__(self, root: Path | None = None, replay_enabled: bool = True):
        self.root = root or API_OUTPUT_ROOT
        self.replay_enabled = replay_enabled
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()

    def job_dir(self, job_id: str) -> Path:
        return self.root / job_id

    def create(self) -> str:
        job_id = f"job_{uuid4().hex}"
        job_dir = self.job_dir(job_id)
        job_dir.mkdir(parents=True, exist_ok=False)
        self.update(job_id, {"jobId": job_id, "status": "queued"})
        return job_id

    def update(self, job_id: str, updates: dict[str, object]) -> None:
        with self._lock:
            state = self.read(job_id, missing_ok=True)
            previous_stage = str(state.get("stage") or "")
            state.update(updates)
            state["jobId"] = job_id
            current_stage = str(updates.get("stage") or "")
            if current_stage and current_stage != previous_stage:
                history = list(state.get("stageHistory") or [])
                history.append(
                    {
                        "stage": current_stage,
                        "hint": str(updates.get("stageHint") or ""),
                        "timestamp": str(
                            updates.get("stageUpdatedAt")
                            or datetime.now(UTC).isoformat()
                        ),
                    }
                )
                state["stageHistory"] = history[-50:]
            target = self.job_dir(job_id) / "job.json"
            # Write atomically: a concurrent reader (e.g. the /status endpoint,
            # which reads without the lock) must never observe a truncated or
            # half-written file. write_text truncates first, leaving a window
            # where the file is empty; os.replace swaps the fully written temp
            # file in as a single atomic rename instead.
            tmp = target.with_name(f"{target.name}.{os.getpid()}.tmp")
            tmp.write_text(
                json.dumps(state, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(tmp, target)
            self.append_event(job_id, state)

    def read(self, job_id: str, missing_ok: bool = False) -> dict[str, object]:
        state_path = self.job_dir(job_id) / "job.json"
        try:
            raw = state_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            if missing_ok:
                return {}
            raise FileNotFoundError(job_id) from None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            # Defensive fallback: with atomic writes this should not happen, but
            # never surface a transient partial read as a 500 to the caller.
            if missing_ok:
                return {}
            raise FileNotFoundError(job_id) from None

    def append_event(self, job_id: str, state: dict[str, object]) -> None:
        if not self.replay_enabled:
            return
        event = {
            "timestamp": datetime.now(UTC).isoformat(),
            "state": _event_state(state),
        }
        event_path = self.job_dir(job_id) / "job_events.jsonl"
        with event_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")

    def read_events(self, job_id: str) -> list[dict[str, object]]:
        event_path = self.job_dir(job_id) / "job_events.jsonl"
        if not event_path.exists():
            return []
        events: list[dict[str, object]] = []
        for line in event_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                events.append(event)
        return events


def create_app():
    app = FastAPI(title="Ontology Construct API")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app_config = load_config()
    store = JobStore(replay_enabled=app_config.replay.enabled)
    agent_store = AgentStore(API_OUTPUT_ROOT.parent / "ontology_agents")
    agent_runtime = OntologyPiAgentRuntime()
    frontend_dir = _frontend_dir()
    index_path = frontend_dir.parent / "index_V0427.html"

    if frontend_dir.exists():
        app.mount("/frontend", StaticFiles(directory=str(frontend_dir)), name="frontend")

    @app.get("/")
    def frontend_index():
        if not index_path.exists():
            raise HTTPException(status_code=404, detail="Frontend index not found")
        return FileResponse(index_path)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/ontology/profiles")
    def ontology_profiles():
        config = load_config()
        return {
            "profiles": [
                {
                    "name": name,
                    "ontologyName": profile.ontology_name,
                    "ontologyDesc": profile.ontology_desc,
                }
                for name, profile in config.ontology_profiles.items()
            ],
            "defaultBackend": config.llm.backend,
            "buildDefaults": _frontend_build_defaults(config),
        }

    @app.get("/ontology/jobs")
    def list_jobs():
        return {"jobs": _list_job_summaries(store)}

    @app.get("/ontology/jobs/{job_id}/sources")
    def ontology_job_sources(job_id: str):
        formatted = _read_formatted_ontology(store, job_id)
        try:
            state = store.read(job_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Job not found") from exc
        return {
            "jobId": job_id,
            "ontology": formatted.ontology.model_dump(mode="json"),
            "documents": _source_documents_for_state(state),
            "builtinDocuments": _builtin_update_document_payloads(),
        }

    @app.get("/ontology/jobs/{job_id}/versions")
    def ontology_job_versions(job_id: str):
        return _ontology_version_history(store, job_id)

    @app.post("/ontology/jobs/{job_id}/updates", status_code=202)
    async def create_ontology_update(
        job_id: str,
        background_tasks: BackgroundTasks,
        files: list[UploadFile] | None = File(None),
        builtin_document_ids: str = Form("[]"),
        scopes: str = Form('["objects","relations","rules","actions"]'),
        backend: str | None = Form(None),
        supplemental_prompt: str = Form(""),
        use_embedding: bool = Form(False),
    ):
        base = _read_formatted_ontology(store, job_id)
        selected_scopes = normalize_update_scopes(_parse_json_string_list(scopes))
        if not selected_scopes:
            raise HTTPException(
                status_code=400,
                detail="Select at least one ontology update scope",
            )
        builtin_ids = _parse_json_string_list(builtin_document_ids)
        unknown_builtin_ids = set(builtin_ids).difference(BUILTIN_UPDATE_DOCUMENTS)
        if unknown_builtin_ids:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Unknown builtin document ids: "
                    + ", ".join(sorted(unknown_builtin_ids))
                ),
            )
        upload_files = [item for item in (files or []) if item and item.filename]
        _validate_upload_files(upload_files)
        if not upload_files and not builtin_ids:
            raise HTTPException(
                status_code=400,
                detail="Upload or select at least one supplemental document",
            )

        update_id = store.create()
        update_dir = store.job_dir(update_id)
        input_paths: list[Path] = []
        source_documents: list[dict[str, Any]] = []
        original_names: list[str] = []
        for index, upload in enumerate(upload_files, start=1):
            original_name = Path(upload.filename or f"upload_{index}.txt").name
            suffix = Path(original_name).suffix.lower() or ".txt"
            stem = _safe_input_stem(Path(original_name).stem)
            input_path = update_dir / f"update_{index:03d}_{stem}{suffix}"
            content = await upload.read()
            input_path.write_bytes(content)
            input_paths.append(input_path)
            original_names.append(original_name)
            source_documents.append(
                {
                    "documentId": f"upload:{index}",
                    "name": original_name,
                    "kind": "uploaded",
                    "size": len(content),
                }
            )
        for builtin_id in builtin_ids:
            definition = BUILTIN_UPDATE_DOCUMENTS[builtin_id]
            builtin_path = Path(definition["path"])
            if not builtin_path.exists():
                raise HTTPException(
                    status_code=503,
                    detail=f"Builtin document is unavailable: {builtin_id}",
                )
            index = len(input_paths) + 1
            suffix = builtin_path.suffix.lower() or ".txt"
            input_path = update_dir / f"update_{index:03d}_{builtin_id}{suffix}"
            content = builtin_path.read_bytes()
            input_path.write_bytes(content)
            input_paths.append(input_path)
            original_names.append(str(definition["name"]) + suffix)
            source_documents.append(
                {
                    "documentId": f"builtin:{builtin_id}",
                    "name": definition["name"],
                    "kind": "builtin",
                    "size": len(content),
                    "description": definition["description"],
                }
            )

        config = load_config()
        requested_profile = base.ontology.profile
        profile = (
            requested_profile
            if requested_profile in config.ontology_profiles
            else config.frontend_build.default_profile
        )
        constraint_prompt = _ontology_update_scope_prompt(selected_scopes)
        effective_prompt = "\n\n".join(
            value
            for value in (
                _normalize_supplemental_prompt(supplemental_prompt),
                constraint_prompt,
            )
            if value
        )
        store.update(
            update_id,
            {
                "status": "queued",
                "source": "ontology_update_analysis",
                "hiddenFromHistory": True,
                "baseJobId": job_id,
                "profile": profile,
                "inputPath": str(input_paths[0]),
                "inputPaths": [str(path) for path in input_paths],
                "inputNames": [path.name for path in input_paths],
                "inputOriginalNames": original_names,
                "sourceDocuments": source_documents,
                "updateScopes": selected_scopes,
                "supplementalPrompt": effective_prompt,
                "constructionMode": "standard",
                "useEmbedding": use_embedding,
            },
        )
        background_tasks.add_task(
            _run_update_job,
            store,
            update_id,
            job_id,
            input_paths,
            profile,
            backend,
            use_embedding,
            effective_prompt,
            selected_scopes,
            source_documents,
        )
        return {
            "updateId": update_id,
            "baseJobId": job_id,
            "status": "queued",
            "scopes": selected_scopes,
            "sourceDocuments": source_documents,
        }

    @app.get("/ontology/jobs/{job_id}/updates/{update_id}/plan")
    def ontology_update_plan(job_id: str, update_id: str):
        state, plan = _read_update_plan(store, job_id, update_id)
        return {
            "updateId": update_id,
            "baseJobId": job_id,
            "status": state.get("status"),
            "plan": plan,
        }

    @app.post("/ontology/jobs/{job_id}/updates/{update_id}/preview")
    def preview_ontology_update(
        job_id: str,
        update_id: str,
        payload: UpdateSelectionRequest,
    ):
        _state, plan = _read_update_plan(store, job_id, update_id)
        base = _read_formatted_ontology(store, job_id)
        candidate = _read_update_candidate(store, update_id)
        try:
            merged, applied_ids = merge_update_selection(
                base,
                candidate,
                plan,
                payload.change_ids,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        preview_path = store.job_dir(update_id) / "update_preview.json"
        save_json_model(merged, preview_path)
        store.update(
            update_id,
            {"previewChangeIds": applied_ids, **_stage_status("画布预览已生成")},
        )
        graph = formatted_ontology_to_graph(merged, view="schema")
        return {
            "updateId": update_id,
            "baseJobId": job_id,
            "appliedChangeIds": applied_ids,
            "ontology": merged.ontology.model_dump(mode="json"),
            "graph": graph.model_dump(mode="json"),
        }

    @app.post("/ontology/jobs/{job_id}/updates/{update_id}/confirm")
    def confirm_ontology_update(
        job_id: str,
        update_id: str,
        payload: UpdateSelectionRequest,
    ):
        update_state, plan = _read_update_plan(store, job_id, update_id)
        existing_version_id = str(update_state.get("confirmedJobId") or "")
        if existing_version_id:
            formatted = _read_formatted_ontology(store, existing_version_id)
            graph = formatted_ontology_to_graph(formatted, view="schema")
            return {
                "jobId": existing_version_id,
                "baseJobId": job_id,
                "updateId": update_id,
                "status": "completed",
                "graph": graph.model_dump(mode="json"),
            }
        base = _read_formatted_ontology(store, job_id)
        candidate = _read_update_candidate(store, update_id)
        try:
            merged, applied_ids = merge_update_selection(
                base,
                candidate,
                plan,
                payload.change_ids,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        try:
            base_state = store.read(job_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Base job not found") from exc
        version_job_id = store.create()
        version_dir = store.job_dir(version_job_id)
        save_json_model(merged, version_dir / "formatted_ontology.json")
        base_documents = _source_documents_for_state(base_state)
        update_documents = list(update_state.get("sourceDocuments") or [])
        all_documents = _deduplicate_source_documents(
            [*base_documents, *update_documents]
        )
        input_paths = [
            *[str(value) for value in base_state.get("inputPaths") or []],
            *[str(value) for value in update_state.get("inputPaths") or []],
        ]
        original_names = [
            str(item.get("name") or "") for item in all_documents if item.get("name")
        ]
        store.update(
            version_job_id,
            {
                "status": "completed",
                "source": "ontology_update",
                "baseJobId": job_id,
                "updateAnalysisId": update_id,
                "appliedChangeIds": applied_ids,
                "updateScopes": plan.get("scopes") or [],
                "profile": merged.ontology.profile,
                "inputPath": input_paths[0] if input_paths else "",
                "inputPaths": input_paths,
                "inputNames": original_names,
                "inputOriginalNames": original_names,
                "sourceDocuments": all_documents,
                "constructionMode": "ontology-update",
                **_stage_status("增量更新已确认"),
            },
        )
        store.update(
            update_id,
            {
                "confirmedJobId": version_job_id,
                "confirmedChangeIds": applied_ids,
                **_stage_status("已生成新本体版本"),
            },
        )
        graph = formatted_ontology_to_graph(merged, view="schema")
        return {
            "jobId": version_job_id,
            "baseJobId": job_id,
            "updateId": update_id,
            "status": "completed",
            "appliedChangeIds": applied_ids,
            "graph": graph.model_dump(mode="json"),
        }

    @app.get("/ontology/agent-catalog")
    def ontology_agent_catalog():
        return {"agents": public_catalog_payload()}

    @app.get("/ontology/agents")
    def list_ontology_agents(ontology_job_id: str | None = None):
        return {
            "agents": [
                public_agent_payload(record)
                for record in agent_store.list(ontology_job_id)
            ]
        }

    @app.post("/ontology/agents", status_code=201)
    def create_ontology_agent(payload: OntologyAgentCreate):
        context = _ontology_agent_context(store, payload.ontology_job_id)
        record = agent_store.create(payload, context)
        return public_agent_payload(record)

    @app.get("/ontology/agents/{agent_id}")
    def get_ontology_agent(agent_id: str):
        return public_agent_payload(_read_agent_or_404(agent_store, agent_id))

    @app.put("/ontology/agents/{agent_id}")
    def update_ontology_agent(agent_id: str, payload: OntologyAgentUpdate):
        _read_agent_or_404(agent_store, agent_id)
        context = _ontology_agent_context(store, payload.ontology_job_id)
        try:
            record = agent_store.update(agent_id, payload, context)
        except RevisionConflict as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "Agent configuration has changed; reload before saving",
                    "currentRevision": exc.current_revision,
                },
            ) from exc
        return public_agent_payload(record)

    @app.post("/ontology/agents/{agent_id}/publish")
    def publish_ontology_agent(agent_id: str):
        record = _read_agent_or_404(agent_store, agent_id)
        _ontology_agent_context(store, record.ontology_context.job_id)
        result = agent_store.publish(agent_id)
        return {
            "agent": public_agent_payload(result.agent),
            "accessKey": result.access_key,
            "invokePath": result.invoke_path,
        }

    @app.get("/ontology/agents/{agent_id}/sessions")
    def list_ontology_agent_sessions(agent_id: str):
        _read_agent_or_404(agent_store, agent_id)
        return {
            "sessions": [
                session.model_dump(mode="json", by_alias=True)
                for session in agent_store.list_sessions(agent_id)
            ]
        }

    @app.post("/ontology/agents/{agent_id}/sessions", status_code=201)
    def create_ontology_agent_session(agent_id: str):
        _read_agent_or_404(agent_store, agent_id)
        session = agent_store.create_session(agent_id)
        return session.model_dump(mode="json", by_alias=True)

    @app.get("/ontology/agents/{agent_id}/sessions/{session_id}")
    def get_ontology_agent_session(agent_id: str, session_id: str):
        _read_agent_or_404(agent_store, agent_id)
        try:
            session = agent_store.read_session(agent_id, session_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Agent session not found") from exc
        return session.model_dump(mode="json", by_alias=True)

    async def invoke_agent(
        *,
        record: OntologyAgentRecord,
        payload: AgentInvocationRequest,
        session_id: str | None,
    ) -> dict[str, object]:
        if record.status == "disabled":
            raise HTTPException(status_code=409, detail="Agent is disabled")
        formatted = _read_formatted_ontology(store, record.ontology_context.job_id)
        if session_id:
            try:
                session = agent_store.read_session(record.agent_id, session_id)
            except KeyError as exc:
                raise HTTPException(status_code=404, detail="Agent session not found") from exc
        else:
            session = agent_store.create_session(record.agent_id)
        history = list(session.messages)
        user_message = new_message("user", payload.message.strip())
        agent_store.append_message(record.agent_id, session.session_id, user_message)
        config = load_config()
        effective_backend = payload.backend or record.backend or config.llm.backend
        qa_strategy = resolve_qa_strategy(
            backend=effective_backend,
            requested_context_mode="graph_rag",
            qa_config=config.qa,
        )
        try:
            llm_client = create_llm_client(config.llm, effective_backend)
            try:
                result = await agent_runtime.invoke(
                    agent=record,
                    ontology=formatted,
                    question=payload.message.strip(),
                    history=history,
                    llm_client=llm_client,
                    context_mode=qa_strategy.context_mode,
                    qa_config=qa_strategy.qa_config,
                    graph_view=payload.graph_view,
                    fallback_enabled=True,
                )
            finally:
                await aclose_llm_client(llm_client)
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        result.response.meta.pop("_qa_log", None)
        result.response.meta.update(
            {"qa_strategy": qa_strategy.name, "backend": effective_backend}
        )
        assistant_message = new_message(
            "assistant",
            result.response.answer.summary,
            collaboration=result.collaboration,
            evidence={
                "highlight": result.response.highlight.model_dump(mode="json"),
                "answer": result.response.answer.model_dump(mode="json"),
            },
        )
        updated_session = agent_store.append_message(
            record.agent_id,
            session.session_id,
            assistant_message,
        )
        return {
            "session": updated_session.model_dump(mode="json", by_alias=True),
            "message": assistant_message.model_dump(mode="json", by_alias=True),
            "answer": result.response.model_dump(mode="json"),
            "collaboration": result.collaboration,
        }

    @app.post("/ontology/agents/{agent_id}/sessions/{session_id}/messages")
    async def send_ontology_agent_message(
        agent_id: str,
        session_id: str,
        payload: AgentInvocationRequest,
    ):
        record = _read_agent_or_404(agent_store, agent_id)
        return await invoke_agent(record=record, payload=payload, session_id=session_id)

    @app.post("/v1/ontology-agents/{invoke_name}/invoke")
    async def invoke_published_ontology_agent(
        invoke_name: str,
        payload: AgentInvocationRequest,
        x_agent_key: str | None = Header(default=None, alias="X-Agent-Key"),
    ):
        try:
            record = agent_store.find_by_invoke_name(invoke_name)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Published agent not found") from exc
        if record.status != "published" or not record.external_call_enabled:
            raise HTTPException(status_code=409, detail="External invocation is not enabled")
        if not agent_store.verify_access_key(record, x_agent_key):
            raise HTTPException(status_code=401, detail="Invalid agent access key")
        return await invoke_agent(
            record=record,
            payload=payload,
            session_id=payload.session_id,
        )

    @app.post("/ontology/jobs")
    async def create_job(
        background_tasks: BackgroundTasks,
        files: list[UploadFile] | None = File(None),
        file: UploadFile | None = File(None),
        profile: str = Form("default"),
        backend: str | None = Form(None),
        instance_count: int = Form(0),
        max_extracted_instances: int | None = Form(None),
        use_embedding: bool = Form(False),
        supplemental_prompt: str = Form(""),
        llm_timeout: int | None = Form(None),
        llm_retries: int | None = Form(None),
        llm_concurrency: int | None = Form(None),
        construction_mode: str = Form("standard"),
        max_concepts: int | None = Form(None),
        min_concept_evidence: int | None = Form(None),
        abstraction_level: str | None = Form(None),
        drop_instances: bool = Form(False),
        extract_real_instances: bool = Form(False),
    ):
        if construction_mode not in {"standard", "schema-first"}:
            raise HTTPException(
                status_code=400,
                detail="Unsupported construction_mode. Use standard or schema-first.",
            )
        if abstraction_level not in {None, "", "high", "medium", "low"}:
            raise HTTPException(
                status_code=400,
                detail="Unsupported abstraction_level. Use high, medium, or low.",
            )
        upload_files = [item for item in (files or []) if item and item.filename]
        if file and file.filename:
            upload_files.append(file)
        if not upload_files:
            raise HTTPException(status_code=400, detail="Please upload at least one file")
        _validate_upload_files(upload_files)
        if construction_mode == "schema-first":
            extract_real_instances = False
        job_id = store.create()
        job_dir = store.job_dir(job_id)
        input_paths = []
        for index, upload in enumerate(upload_files, start=1):
            suffix = Path(upload.filename or "input.txt").suffix.lower() or ".txt"
            stem = _safe_input_stem(Path(upload.filename or f"input{suffix}").stem)
            input_path = job_dir / f"input_{index:03d}_{stem}{suffix}"
            input_path.write_bytes(await upload.read())
            input_paths.append(input_path)
        store.update(
            job_id,
            {
                "status": "queued",
                "profile": profile,
                "inputPath": str(input_paths[0]),
                "inputPaths": [str(path) for path in input_paths],
                "inputNames": [path.name for path in input_paths],
                "inputOriginalNames": [
                    Path(upload.filename or path.name).name
                    for upload, path in zip(upload_files, input_paths, strict=True)
                ],
                "sourceDocuments": [
                    {
                        "documentId": f"current:{index}",
                        "name": Path(upload.filename or path.name).name,
                        "kind": "uploaded",
                        "size": path.stat().st_size,
                    }
                    for index, (upload, path) in enumerate(
                        zip(upload_files, input_paths, strict=True), start=1
                    )
                ],
                "supplementalPrompt": _normalize_supplemental_prompt(
                    supplemental_prompt
                ),
                "constructionMode": construction_mode,
                "useEmbedding": use_embedding,
                "maxConcepts": max_concepts,
                "minConceptEvidence": min_concept_evidence,
                "abstractionLevel": abstraction_level or "",
                "dropInstances": drop_instances,
                "extractRealInstances": extract_real_instances,
            },
        )
        background_tasks.add_task(
            _run_job,
            store,
            job_id,
            input_paths,
            profile,
            backend,
            instance_count,
            max_extracted_instances,
            use_embedding,
            supplemental_prompt,
            llm_timeout,
            llm_retries,
            llm_concurrency,
            construction_mode,
            max_concepts,
            min_concept_evidence,
            abstraction_level,
            drop_instances,
            extract_real_instances,
        )
        return {"jobId": job_id, "status": "queued"}

    @app.post("/ontology/jobs/import")
    async def import_ontology(file: UploadFile = File(...)):
        if not file.filename:
            raise HTTPException(status_code=400, detail="Please upload an ontology file")
        suffix = Path(file.filename).suffix.lower()
        if suffix not in ONTOLOGY_IMPORT_SUFFIXES:
            raise HTTPException(
                status_code=400,
                detail="Unsupported ontology file type. Supported: .json, .jsonl",
            )
        content = await file.read()
        try:
            formatted = _load_imported_ontology(content, suffix)
        except (ValueError, ValidationError) as exc:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Invalid ontology file. Please upload a formatted ontology "
                    f"JSON or JSONL file. {_validation_error_message(exc)}"
                ),
            ) from exc

        job_id = store.create()
        job_dir = store.job_dir(job_id)
        stem = _safe_input_stem(Path(file.filename).stem)
        input_path = job_dir / f"ontology_{stem}{suffix}"
        input_path.write_bytes(content)
        save_json_model(formatted, job_dir / "formatted_ontology.json")
        store.update(
            job_id,
            {
                "status": "completed",
                "profile": formatted.ontology.profile,
                "inputPath": str(input_path),
                "inputPaths": [str(input_path)],
                "inputNames": [input_path.name],
                "inputOriginalNames": [Path(file.filename).name],
                "sourceDocuments": [
                    {
                        "documentId": "current:1",
                        "name": Path(file.filename).name,
                        "kind": "ontology-import",
                        "size": len(content),
                    }
                ],
                "source": "uploaded_ontology",
                "extracted": 0,
                "skipped": 0,
                "failed": 0,
            },
        )
        return {"jobId": job_id, "status": "completed"}

    @app.post("/ontology/structured-configs")
    async def upload_structured_config(
        background_tasks: BackgroundTasks,
        file: UploadFile = File(...),
    ):
        if not file.filename:
            raise HTTPException(
                status_code=400,
                detail="Please upload a structured data YAML config file",
            )
        suffix = Path(file.filename).suffix.lower()
        if suffix not in STRUCTURED_CONFIG_SUFFIXES:
            raise HTTPException(
                status_code=400,
                detail="Unsupported structured config file type. Supported: .yaml, .yml",
            )
        content = await file.read()
        config_payload = _load_structured_config_yaml(content)
        try:
            structured_config = load_structured_build_config(config_payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except ValidationError as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid structured config YAML. {_validation_error_message(exc)}",
            ) from exc

        job_id = store.create()
        job_dir = store.job_dir(job_id)
        stem = _safe_input_stem(Path(file.filename).stem)
        input_path = job_dir / f"structured_config_{stem}{suffix}"
        input_path.write_bytes(content)
        config_keys = sorted(str(key) for key in config_payload.keys())
        store.update(
            job_id,
            {
                "status": "queued",
                "profile": structured_config.ontology.profile,
                "inputPath": str(input_path),
                "inputPaths": [str(input_path)],
                "inputNames": [input_path.name],
                "inputOriginalNames": [Path(file.filename).name],
                "sourceDocuments": [
                    {
                        "documentId": "current:1",
                        "name": Path(file.filename).name,
                        "kind": "structured-config",
                        "size": len(content),
                    }
                ],
                "source": "structured_config",
                "constructionMode": "structured",
                "configKeys": config_keys,
            },
        )
        background_tasks.add_task(_run_structured_job, store, job_id, input_path)
        return {
            "jobId": job_id,
            "status": "queued",
            "inputName": input_path.name,
            "configKeys": config_keys,
        }

    @app.get("/ontology/jobs/{job_id}/status")
    def job_status(job_id: str):
        try:
            state = store.read(job_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Job not found") from exc
        progress = _read_progress(store.job_dir(job_id))
        progress["overallPercent"] = _overall_build_percent(state, progress)
        state["progress"] = progress
        store.append_event(job_id, state)
        return state

    @app.get("/ontology/jobs/{job_id}/replay")
    def job_replay(job_id: str):
        try:
            state = store.read(job_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Job not found") from exc
        events = store.read_events(job_id)
        if not events:
            fallback = dict(state)
            progress = _read_progress(store.job_dir(job_id))
            progress["overallPercent"] = _overall_build_percent(fallback, progress)
            fallback["progress"] = progress
            events = [
                {
                    "timestamp": datetime.now(UTC).isoformat(),
                    "state": _event_state(fallback),
                }
            ]
        return {"jobId": job_id, "events": events}

    @app.get("/ontology/jobs/{job_id}/result")
    def job_result(job_id: str):
        try:
            state = store.read(job_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Job not found") from exc
        if state.get("status") != "completed":
            raise HTTPException(status_code=409, detail="Job is not completed")
        result_path = store.job_dir(job_id) / "formatted_ontology.json"
        if not result_path.exists():
            raise HTTPException(status_code=404, detail="Result not found")
        return JSONResponse(
            json.loads(result_path.read_text(encoding="utf-8"))
        )

    @app.get("/ontology/jobs/{job_id}/canonical")
    def job_canonical(job_id: str):
        try:
            state = store.read(job_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Job not found") from exc
        if state.get("status") != "completed":
            raise HTTPException(status_code=409, detail="Job is not completed")
        schema_path = store.job_dir(job_id) / "schema.json"
        graph_path = store.job_dir(job_id) / "graph.json"
        if not schema_path.exists() or not graph_path.exists():
            raise HTTPException(status_code=404, detail="Canonical result not found")
        return JSONResponse(
            {
                "schema": json.loads(schema_path.read_text(encoding="utf-8")),
                "graph": json.loads(graph_path.read_text(encoding="utf-8")),
            }
        )

    @app.get("/ontology/jobs/{job_id}/quality")
    def job_quality(job_id: str):
        try:
            store.read(job_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Job not found") from exc
        quality_path = store.job_dir(job_id) / "build_quality.json"
        if not quality_path.exists():
            raise HTTPException(status_code=404, detail="Build quality report not found")
        return JSONResponse(json.loads(quality_path.read_text(encoding="utf-8")))

    @app.get("/ontology/jobs/{job_id}/graph")
    def job_graph(job_id: str, view: Literal["auto", "schema", "instance"] = "auto"):
        formatted = _read_formatted_ontology(store, job_id)
        return JSONResponse(
            formatted_ontology_to_graph(formatted, view=view).model_dump(mode="json")
        )

    @app.post("/ontology/jobs/{job_id}/question")
    async def job_question(job_id: str, payload: QuestionRequest):
        formatted = _read_formatted_ontology(store, job_id)
        config = load_config()
        effective_backend = payload.backend or config.llm.backend
        qa_strategy = resolve_qa_strategy(
            backend=effective_backend,
            requested_context_mode=payload.context_mode,
            qa_config=config.qa,
        )
        started_at = datetime.now(UTC)
        started_perf = time.perf_counter()
        try:
            llm_client = create_llm_client(config.llm, payload.backend)
            try:
                response = await answer_question(
                    question=payload.question,
                    ontology=formatted,
                    llm_client=llm_client,
                    graph_view=payload.graph_view,
                    context_mode=qa_strategy.context_mode,
                    fallback_enabled=payload.fallback_enabled,
                    qa_config=qa_strategy.qa_config,
                )
                qa_log_payload = (response.meta or {}).pop("_qa_log", {})
                response.meta = {
                    **(response.meta or {}),
                    "qa_strategy": qa_strategy.name,
                    "backend": effective_backend,
                }
                _append_qa_event(
                    store=store,
                    config=config,
                    job_id=job_id,
                    payload=payload,
                    backend=effective_backend,
                    qa_strategy=qa_strategy.name,
                    response=response,
                    qa_log_payload=qa_log_payload,
                    started_at=started_at,
                    duration_ms=round((time.perf_counter() - started_perf) * 1000, 2),
                    error=None,
                )
            finally:
                await aclose_llm_client(llm_client)
        except (RuntimeError, ValueError) as exc:
            _append_qa_event(
                store=store,
                config=config,
                job_id=job_id,
                payload=payload,
                backend=effective_backend,
                qa_strategy=qa_strategy.name,
                response=None,
                qa_log_payload={},
                started_at=started_at,
                duration_ms=round((time.perf_counter() - started_perf) * 1000, 2),
                error=str(exc),
            )
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return JSONResponse(response.model_dump(mode="json"))

    @app.post("/ontology/jobs/{job_id}/instances/rebuild")
    def rebuild_job_instances(
        job_id: str,
        background_tasks: BackgroundTasks,
        backend: str | None = None,
    ):
        try:
            state = store.read(job_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Job not found") from exc
        if state.get("status") not in {"completed", "failed"}:
            raise HTTPException(status_code=409, detail="Job is not ready for rebuild")
        job_dir = store.job_dir(job_id)
        if not (job_dir / "concept_global.json").exists():
            raise HTTPException(
                status_code=404,
                detail="concept_global.json not found; only schema-first jobs can rebuild instances",
            )
        input_paths = [Path(str(path)) for path in state.get("inputPaths") or []]
        if not input_paths or any(not path.exists() for path in input_paths):
            raise HTTPException(status_code=404, detail="Original input files not found")
        store.update(job_id, {"status": "running", **_stage_status("重新抽取实例")})
        background_tasks.add_task(
            _run_instance_rebuild_job,
            store,
            job_id,
            input_paths,
            backend,
        )
        return {"jobId": job_id, "status": "running", **_stage_status("重新抽取实例")}

    return app


def _run_job(
    store: JobStore,
    job_id: str,
    input_paths: list[Path],
    profile: str,
    backend: str | None,
    instance_count: int,
    max_extracted_instances: int | None,
    use_embedding: bool,
    supplemental_prompt: str | None,
    llm_timeout: int | None,
    llm_retries: int | None,
    llm_concurrency: int | None,
    construction_mode: str,
    max_concepts: int | None,
    min_concept_evidence: int | None,
    abstraction_level: str | None,
    drop_instances: bool,
    extract_real_instances: bool,
    finalize: bool = True,
) -> None:
    output_dir = store.job_dir(job_id)
    try:
        def update_stage(stage: str) -> None:
            store.update(job_id, _stage_status(stage))

        store.update(job_id, {"status": "running", **_stage_status("加载配置")})
        config = load_config()
        _apply_llm_overrides(config, llm_timeout, llm_retries, llm_concurrency)
        if max_concepts is not None:
            config.concept.max_concepts = max_concepts
        if min_concept_evidence is not None:
            config.concept.min_evidence = min_concept_evidence
        if abstraction_level:
            config.concept.abstraction_level = abstraction_level
        config.concept.extract_real_instances = extract_real_instances
        if construction_mode == "schema-first" and config.instance_builder.enabled:
            config.concept.extract_real_instances = False
            config.concept.extract_instance_relations = False
        profile_config = config.get_profile(profile)
        _apply_profile_overrides(config, profile_config)
        embedding_requested = bool(
            use_embedding or config.concept.use_embedding_clustering
        )
        if embedding_requested:
            update_stage("检查 embedding 语义聚类")
        use_embedding = _prepare_optional_embedding(
            store=store,
            job_id=job_id,
            config=config,
            requested=embedding_requested,
        )
        if embedding_requested:
            update_stage(
                "Embedding 语义聚类已启用"
                if use_embedding
                else "Embedding 语义聚类已跳过"
            )
        effective_supplemental = _compose_supplemental(
            profile_config, supplemental_prompt
        )
        instance_build_report = None
        action_build_stats = None
        quality_report = None
        update_stage("文本清洗与分块")
        chunks = _prepare_chunks_for_inputs(input_paths, config)
        if construction_mode == "schema-first":
            global_ontology, success_count, failure_count, skipped_count = (
                _run_schema_first_build(
                    chunks=chunks,
                    output_dir=output_dir,
                    config=config,
                    backend=backend,
                    supplemental_prompt=effective_supplemental,
                    drop_instances=drop_instances,
                    use_embedding=use_embedding,
                    stage_callback=update_stage,
                )
            )
            store.update(
                job_id,
                _instance_build_runtime_status(input_paths, config),
            )
            global_ontology, instance_build_report = _apply_instance_builder(
                global_ontology=global_ontology,
                input_paths=input_paths,
                source_chunks=chunks,
                config=config,
                backend=backend,
                output_dir=output_dir,
                supplemental_prompt=effective_supplemental,
                stage_callback=update_stage,
            )
            global_ontology, postprocess_stats = _apply_instance_postprocess(
                global_ontology=global_ontology,
                config=config,
                backend=backend,
                output_dir=output_dir,
                stage_callback=update_stage,
            )
            global_ontology, action_build_stats = _apply_action_builder(
                global_ontology=global_ontology,
                config=config,
                profile_config=profile_config,
                output_dir=output_dir,
                stage_callback=update_stage,
            )
            update_stage("构建质量校验")
            global_ontology, quality_report = _apply_build_quality(
                global_ontology=global_ontology,
                config=config,
                output_dir=output_dir,
                total_chunk_count=len(chunks),
                failed_chunk_count=failure_count,
                instance_build_report=instance_build_report,
            )
            update_stage("格式化输出")
            _save_build_outputs(
                global_ontology=global_ontology,
                output_dir=output_dir,
                profile=profile,
                profile_config=profile_config,
                instance_count=instance_count,
            )
        else:
            update_stage("chunk 本体抽取")
            success_count, failure_count, skipped_count = _run_extract(
                chunks=chunks,
                output_dir=output_dir,
                config=config,
                backend=backend,
                prompt_path=profile_config.prompt,
                supplemental_prompt=effective_supplemental,
            )
            _ensure_complete_chunk_extraction(
                output_dir=output_dir,
                total_chunk_count=len(chunks),
                failure_count=failure_count,
                config=config,
                phase="ontology extraction",
            )
            local_files = _json_model_files(output_dir / "local")
            if not local_files and failure_count:
                raise RuntimeError(
                    _missing_local_ontologies_message(output_dir, failure_count)
                )
            update_stage("合并局部本体")
            global_ontology = _merge_local_ontologies(
                local_files=local_files,
                config=config,
                use_embedding=use_embedding,
            )
            global_ontology = _limit_extracted_instances(
                global_ontology,
                max_extracted_instances,
            )
            if config.build_quality.complete_standard_instances:
                store.update(
                    job_id,
                    _instance_build_runtime_status(input_paths, config),
                )
                global_ontology, instance_build_report = _apply_instance_builder(
                    global_ontology=global_ontology,
                    input_paths=input_paths,
                    source_chunks=chunks,
                    config=config,
                    backend=backend,
                    output_dir=output_dir,
                    supplemental_prompt=effective_supplemental,
                    stage_callback=update_stage,
                )
                global_ontology = _limit_extracted_instances(
                    global_ontology,
                    max_extracted_instances,
                )
            global_ontology, postprocess_stats = _apply_instance_postprocess(
                global_ontology=global_ontology,
                config=config,
                backend=backend,
                output_dir=output_dir,
                stage_callback=update_stage,
            )
            global_ontology, action_build_stats = _apply_action_builder(
                global_ontology=global_ontology,
                config=config,
                profile_config=profile_config,
                output_dir=output_dir,
                stage_callback=update_stage,
            )
            update_stage("构建质量校验")
            global_ontology, quality_report = _apply_build_quality(
                global_ontology=global_ontology,
                config=config,
                output_dir=output_dir,
                total_chunk_count=len(chunks),
                failed_chunk_count=failure_count,
                instance_build_report=instance_build_report,
            )
            save_json_model(global_ontology, output_dir / "global.json")
            update_stage("格式化输出")
            formatted = format_global_ontology(
                global_ontology,
                ontology_name=profile_config.ontology_name,
                ontology_desc=profile_config.ontology_desc,
                profile=profile,
                generated_instance_count=instance_count,
            )
            save_json_model(formatted, output_dir / "formatted_ontology.json")
            schema_id, scenario, sub_scenario = _schema_identity(
                profile, profile_config.ontology_name
            )
            export = build_canonical_ontology(
                global_ontology,
                schema_id=schema_id,
                scenario=scenario,
                sub_scenario=sub_scenario,
            )
            save_json_model(export.schema_def, output_dir / "schema.json")
            save_json_model(export.graph, output_dir / "graph.json")
        completed_stage = (
            "已完成（有质量告警）"
            if quality_report and quality_report.warnings
            else "已完成"
        )
        store.update(
            job_id,
            _with_build_quality_status(
                _with_action_build_status(
                    _with_instance_build_status(
                        _with_instance_postprocess_status(
                            {
                                "status": "completed" if finalize else "running",
                                **_stage_status(
                                    completed_stage if finalize else "生成变更方案"
                                ),
                                "extracted": success_count,
                                "skipped": skipped_count,
                                "failed": failure_count,
                            },
                            postprocess_stats,
                        ),
                        instance_build_report,
                    ),
                    action_build_stats,
                ),
                quality_report,
            ),
        )
    except Exception as exc:  # noqa: BLE001 - background job must persist failures.
        store.update(job_id, {"status": "failed", **_stage_status("构建失败"), "error": str(exc)})


def _run_update_job(
    store: JobStore,
    update_id: str,
    base_job_id: str,
    input_paths: list[Path],
    profile: str,
    backend: str | None,
    use_embedding: bool,
    supplemental_prompt: str,
    scopes: list[str],
    source_documents: list[dict[str, Any]],
) -> None:
    _run_job(
        store,
        update_id,
        input_paths,
        profile,
        backend,
        0,
        None,
        use_embedding,
        supplemental_prompt,
        None,
        None,
        None,
        "standard",
        None,
        None,
        None,
        False,
        False,
        False,
    )
    state = store.read(update_id, missing_ok=True)
    if state.get("status") == "failed":
        return
    try:
        base = _read_formatted_ontology(store, base_job_id)
        candidate = _read_update_candidate(store, update_id)
        plan = build_update_plan(
            base,
            candidate,
            scopes=scopes,
            source_documents=source_documents,
        )
        _save_json_payload(plan, store.job_dir(update_id) / "update_plan.json")
        counts = plan["counts"]
        store.update(
            update_id,
            {
                "status": "completed",
                "planReady": True,
                "changeCount": len(plan["changes"]),
                "addedChangeCount": counts["added"],
                "modifiedChangeCount": counts["modified"],
                **_stage_status("更新影响分析已完成"),
            },
        )
    except Exception as exc:  # noqa: BLE001 - background analysis must persist failures.
        store.update(
            update_id,
            {
                "status": "failed",
                **_stage_status("更新影响分析失败"),
                "error": str(exc),
            },
        )


def _run_structured_job(
    store: JobStore,
    job_id: str,
    config_path: Path,
) -> None:
    output_dir = store.job_dir(job_id)
    try:
        def update_stage(stage: str) -> None:
            store.update(job_id, _stage_status(stage))

        store.update(job_id, {"status": "running", **_stage_status("读取结构化配置")})
        config_payload = _load_structured_config_yaml(config_path.read_bytes())
        structured_config = load_structured_build_config(config_payload)
        semantic_mode = structured_config.semantic_mapping is not None
        app_config = load_config()
        embedding_client = (
            create_embedding_client(app_config.merge)
            if structured_config.construction.use_embedding
            else None
        )
        llm_client = (
            create_llm_client(app_config.llm)
            if structured_config.construction.use_llm and structured_config.llm.enabled
            else None
        )
        update_stage("读取数据库元信息")
        try:
            global_ontology, stats = build_structured_ontology(
                structured_config,
                embedding_client=embedding_client,
                llm_client=llm_client,
                stage_callback=update_stage,
            )
            _save_structured_artifacts(output_dir, stats)
            if (
                structured_config.llm.enabled
                and structured_config.llm.usage.schema_polish
            ):
                if llm_client is None:
                    raise RuntimeError("Missing LLM client for structured schema polish")
                update_stage("润色结构化 schema")
                global_ontology = asyncio.run(
                    polish_structured_schema(global_ontology, llm_client)
                )
        finally:
            if llm_client is not None:
                asyncio.run(aclose_llm_client(llm_client))
        postprocess_stats = None
        if not semantic_mode:
            global_ontology, postprocess_stats = _apply_instance_postprocess(
                global_ontology=global_ontology,
                config=app_config,
                backend=None,
                output_dir=output_dir,
                stage_callback=update_stage,
            )
        global_ontology, action_build_stats = _apply_action_builder(
            global_ontology=global_ontology,
            config=app_config,
            profile_config=None,
            output_dir=output_dir,
            stage_callback=update_stage,
        )
        save_json_model(global_ontology, output_dir / "global.json")
        save_json_model(global_ontology, output_dir / "ontology.json")
        update_stage("格式化输出")
        formatted = format_global_ontology(
            global_ontology,
            ontology_name=structured_config.ontology.name,
            ontology_desc="从 PostgreSQL 结构化数据、元信息、主外键和显式关系构建",
            profile=structured_config.ontology.profile,
            generated_instance_count=0,
        )
        save_json_model(formatted, output_dir / "formatted_ontology.json")
        schema_id, scenario, sub_scenario = _schema_identity(
            structured_config.ontology.profile,
            structured_config.ontology.name,
        )
        export = build_canonical_ontology(
            global_ontology,
            schema_id=schema_id,
            scenario=scenario,
            sub_scenario=sub_scenario,
        )
        save_json_model(export.schema_def, output_dir / "schema.json")
        save_json_model(export.graph, output_dir / "graph.json")
        store.update(
            job_id,
            _with_action_build_status(
                _with_instance_postprocess_status(
                    {
                        "status": "completed",
                        **_stage_status("已完成"),
                        "profileTableCount": getattr(stats, "profile_table_count", 0),
                        "candidateClassCount": getattr(stats, "candidate_class_count", 0),
                        "candidateRelationCount": getattr(
                            stats,
                            "candidate_relation_count",
                            0,
                        ),
                        "mappedClassCount": getattr(stats, "mapped_class_count", 0),
                        "mappedRelationCount": getattr(stats, "mapped_relation_count", 0),
                        "objectCount": stats.object_count,
                        "linkCount": stats.schema_relation_count,
                        "instanceCount": stats.instance_count,
                        "instanceLinkCount": stats.instance_link_count,
                        "structuredQuality": getattr(stats, "structured_quality", {}),
                        "extracted": stats.instance_count,
                        "skipped": 0,
                        "failed": 0,
                    },
                    postprocess_stats,
                ),
                action_build_stats,
            ),
        )
    except Exception as exc:  # noqa: BLE001 - background job must persist failures.
        store.update(job_id, {"status": "failed", **_stage_status("构建失败"), "error": str(exc)})


def _run_instance_rebuild_job(
    store: JobStore,
    job_id: str,
    input_paths: list[Path],
    backend: str | None,
) -> None:
    output_dir = store.job_dir(job_id)
    try:
        def update_stage(stage: str) -> None:
            store.update(job_id, _stage_status(stage))

        state = store.read(job_id)
        config = load_config()
        profile = str(state.get("profile") or config.frontend_build.default_profile)
        profile_config = config.get_profile(profile)
        _apply_profile_overrides(config, profile_config)
        ontology = load_json_model(GlobalOntology, output_dir / "concept_global.json")
        source_chunks = _prepare_chunks_for_inputs(input_paths, config)
        store.update(job_id, _instance_build_runtime_status(input_paths, config))
        ontology, instance_build_report = _apply_instance_builder(
            global_ontology=ontology,
            input_paths=input_paths,
            source_chunks=source_chunks,
            config=config,
            backend=backend,
            output_dir=output_dir,
            supplemental_prompt=str(state.get("supplementalPrompt") or ""),
            stage_callback=update_stage,
        )
        ontology, postprocess_stats = _apply_instance_postprocess(
            global_ontology=ontology,
            config=config,
            backend=backend,
            output_dir=output_dir,
            stage_callback=update_stage,
        )
        ontology, action_build_stats = _apply_action_builder(
            global_ontology=ontology,
            config=config,
            profile_config=profile_config,
            output_dir=output_dir,
            stage_callback=update_stage,
        )
        update_stage("构建质量校验")
        ontology, quality_report = _apply_build_quality(
            global_ontology=ontology,
            config=config,
            output_dir=output_dir,
            total_chunk_count=len(source_chunks),
            failed_chunk_count=0,
            instance_build_report=instance_build_report,
        )
        update_stage("格式化输出")
        _save_build_outputs(
            global_ontology=ontology,
            output_dir=output_dir,
            profile=profile,
            profile_config=profile_config,
            instance_count=int(state.get("instanceCount") or 0),
        )
        store.update(
            job_id,
            _with_build_quality_status(
                _with_action_build_status(
                    _with_instance_build_status(
                        _with_instance_postprocess_status(
                            {
                                "status": "completed",
                                **_stage_status(
                                    "已完成（有质量告警）"
                                    if quality_report.warnings
                                    else "已完成"
                                ),
                            },
                            postprocess_stats,
                        ),
                        instance_build_report,
                    ),
                    action_build_stats,
                ),
                quality_report,
            ),
        )
    except Exception as exc:  # noqa: BLE001 - background job must persist failures.
        store.update(job_id, {"status": "failed", **_stage_status("实例重建失败"), "error": str(exc)})


def _save_structured_artifacts(output_dir: Path, stats: Any) -> None:
    artifacts = {
        "database_profile.json": getattr(stats, "database_profile", {}),
        "structured_candidates.json": getattr(stats, "structured_candidates", {}),
        "structured_mapping.json": getattr(stats, "structured_mapping", {}),
        "structured_quality.json": getattr(stats, "structured_quality", {}),
    }
    for filename, payload in artifacts.items():
        (output_dir / filename).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )


def _append_qa_event(
    *,
    store: JobStore,
    config: Any,
    job_id: str,
    payload: QuestionRequest,
    backend: str,
    qa_strategy: str,
    response: Any,
    qa_log_payload: dict[str, Any],
    started_at: datetime,
    duration_ms: float,
    error: str | None,
) -> None:
    if not config.qa_logging.enabled:
        return
    response_payload = response.model_dump(mode="json") if response is not None else None
    meta = dict((response_payload or {}).get("meta") or {})
    event = {
        "timestamp": started_at.isoformat(),
        "durationMs": duration_ms,
        "jobId": job_id,
        "compareSessionId": payload.compare_session_id or "",
        "compareSlot": payload.compare_slot,
        "question": payload.question,
        "backend": backend,
        "requestedBackend": payload.backend or "",
        "qaStrategy": qa_strategy,
        "graphView": payload.graph_view,
        "contextMode": payload.context_mode,
        "fallbackEnabled": payload.fallback_enabled,
        "success": error is None,
        "error": error or "",
        "prompt": qa_log_payload.get("prompt", ""),
        "systemPrompt": qa_log_payload.get("system_prompt", ""),
        "rawAnswer": qa_log_payload.get("raw_answer", ""),
        "promptChars": qa_log_payload.get("prompt_chars", 0),
        "rawAnswerChars": qa_log_payload.get("raw_answer_chars", 0),
        "parsedAnswer": (response_payload or {}).get("answer"),
        "think": (response_payload or {}).get("think") or [],
        "highlight": (response_payload or {}).get("highlight") or {},
        "meta": meta,
        "linkedEntityCount": len(meta.get("linked_entities") or []),
        "coreItemCount": meta.get("core_item_count", 0),
        "supportingNodeCount": meta.get("supporting_node_count", 0),
        "supportingEdgeCount": meta.get("supporting_edge_count", 0),
    }
    event_path = store.job_dir(job_id) / "qa_events.jsonl"
    with event_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")


def _with_instance_postprocess_status(
    updates: dict[str, object],
    stats: Any,
) -> dict[str, object]:
    if stats is None:
        return updates
    payload = stats.as_dict()
    updates.update(
        {
            "instanceIsolatedBefore": payload["isolatedBefore"],
            "instanceIsolatedAfter": payload["isolatedAfter"],
            "instancePostprocessLinks": (
                int(payload["llmLinksAdded"]) + int(payload["fallbackLinksAdded"])
            ),
            "instancePostprocessDropped": payload["instancesDropped"],
            "instancePostprocessActions": payload["actionsAdded"],
        }
    )
    return updates


def _with_action_build_status(
    updates: dict[str, object],
    stats: Any,
) -> dict[str, object]:
    if stats is None:
        return updates
    payload = stats.as_dict()
    updates.update(
        {
            "actionBuildSourceEvents": payload["sourceEventInstances"],
            "actionBuildActions": payload["actionsAdded"],
            "actionBuildEvents": payload["eventsAdded"],
        }
    )
    return updates


def _with_instance_build_status(
    updates: dict[str, object],
    report: Any,
) -> dict[str, object]:
    if report is None:
        return updates
    payload = report.as_dict() if hasattr(report, "as_dict") else {}
    updates.update(
        {
            "instanceBuildMode": payload.get("mode", ""),
            "instanceBuildCandidates": payload.get("candidateCount", 0),
            "instanceBuildInstances": payload.get("instanceCount", 0),
            "instanceBuildLinks": payload.get("linkCount", 0),
            "instanceBuildFailedBatches": payload.get("failedBatchCount", 0),
            "instanceBuildSkippedCandidates": payload.get("skippedCandidateCount", 0),
        }
    )
    return updates


def _with_build_quality_status(
    updates: dict[str, object],
    report: Any,
) -> dict[str, object]:
    if report is None:
        return updates
    payload = report.as_dict() if hasattr(report, "as_dict") else {}
    updates.update(
        {
            "qualityStatus": payload.get("status", ""),
            "qualityWarnings": payload.get("warnings", []),
            "qualityCoveredEntities": payload.get("coveredEntityCount", 0),
            "qualityUncoveredEntities": payload.get("uncoveredEntityCount", 0),
            "qualityQuarantinedEntities": payload.get(
                "quarantinedEntityCount", 0
            ),
        }
    )
    return updates


def _instance_build_runtime_status(
    input_paths: list[Path],
    config: Any,
) -> dict[str, object]:
    text_files = [
        path
        for path in input_paths
        if path.suffix.lower() not in {".log", ".json", ".jsonl"}
    ]
    builder = config.instance_builder
    return {
        "instanceBuildInputFiles": len(input_paths),
        "instanceBuildTextFiles": len(text_files),
        "instanceBuildLlmEnabled": bool(
            builder.llm_enabled_for_unresolved and text_files
        ),
        "instanceBuildMaxLogRecordsPerFile": builder.max_log_records_per_file,
        "instanceBuildMaxLogRecordsPerTemplate": builder.max_log_records_per_template,
        "instanceBuildLlmChunkBatchSize": builder.llm_chunk_batch_size,
        "instanceBuildLlmConcurrency": builder.llm_concurrency,
    }


def _stage_status(stage: str) -> dict[str, object]:
    return {
        "stage": stage,
        "stageHint": _stage_hint(stage),
        "stageUpdatedAt": datetime.now(UTC).isoformat(),
    }


def _stage_hint(stage: str) -> str:
    if "变更方案" in stage or "更新影响" in stage:
        return "正在将补充文档的抽取结果与当前本体比对，生成可逐项确认的新增和修改清单。"
    if "embedding" in stage.lower():
        if "跳过" in stage:
            return "未找到可用 embedding key，已降级为名称和证据驱动的确定性聚类。"
        return "正在检查或调用文本向量模型，用于合并语义相近的候选概念。"
    if "实例" in stage:
        return (
            "实例阶段会读取原始输入并生成实例、属性和实例关系；"
            "chunk 进度条主要反映前面的概念抽取阶段，因此这里可能暂时不动。"
        )
    if "分块" in stage:
        return "正在读取上传文件、清洗文本并切分 chunk。"
    if "schema" in stage.lower() or "概念" in stage:
        return "正在抽取和归并本体类型、属性与关系。"
    if "后处理" in stage:
        return "正在补齐实例图连接关系并减少孤立实例。"
    if "动作" in stage:
        return "正在根据事件类实例派生动作层。"
    if "质量" in stage:
        return "正在检查 chunk 完整性、类—实例覆盖率并隔离无实例无关系的孤立类。"
    if "格式化" in stage:
        return "正在生成前端图谱、问答和导出使用的标准结果文件。"
    return ""


def _prepare_optional_embedding(
    *,
    store: JobStore,
    job_id: str,
    config: Any,
    requested: bool,
) -> bool:
    if not requested:
        store.update(
            job_id,
            {
                "embeddingStatus": "disabled",
                "embeddingBackend": config.merge.embedding_backend,
                "embeddingModel": config.merge.embedding_model,
            },
        )
        return False
    try:
        client = create_embedding_client(config.merge)
    except (RuntimeError, ValueError) as exc:
        config.concept.use_embedding_clustering = False
        store.update(
            job_id,
            {
                "embeddingStatus": "skipped",
                "embeddingBackend": config.merge.embedding_backend,
                "embeddingModel": config.merge.embedding_model,
                "embeddingWarning": str(exc),
            },
        )
        return False
    runtime_config = getattr(client, "config", config.merge)
    store.update(
        job_id,
        {
            "embeddingStatus": "enabled",
            "embeddingBackend": runtime_config.embedding_backend,
            "embeddingModel": runtime_config.embedding_model,
            "embeddingWarning": "",
        },
    )
    config.merge = runtime_config
    config.concept.use_embedding_clustering = True
    return True


def _overall_build_percent(
    state: dict[str, object],
    chunk_progress: dict[str, int | float],
) -> float:
    status = str(state.get("status") or "")
    if status == "completed":
        return 100.0
    stage = str(state.get("stage") or "")
    chunk_percent = float(chunk_progress.get("percent") or 0)
    if "加载配置" in stage:
        return 3.0
    if "embedding" in stage.lower():
        return 6.0
    if "分块" in stage:
        return 9.0
    if "候选概念抽取" in stage or "chunk 本体抽取" in stage:
        return round(10.0 + chunk_percent * 0.25, 2)
    if "聚类" in stage or "初筛" in stage:
        return 38.0
    if "全局概念" in stage:
        return 47.0
    if "概念关系" in stage or "合并局部" in stage:
        return 57.0
    if "真实实例" in stage or "独立实例" in stage:
        return 70.0
    if "实例关系" in stage or "实例连通性" in stage:
        return 82.0
    if "动作" in stage:
        return 89.0
    if "质量" in stage:
        return 94.0
    if "格式化" in stage or "写出" in stage:
        return 98.0
    if "变更" in stage or "更新影响" in stage:
        return 99.0
    if "完成" in stage:
        return 100.0
    if status == "failed":
        return min(99.0, max(4.0, chunk_percent))
    return min(34.0, max(1.0, chunk_percent * 0.25))


def _read_progress(job_dir: Path) -> dict[str, int | float]:
    db_path = job_dir / "progress.sqlite"
    if not db_path.exists():
        return {
            "totalChunks": 0,
            "doneChunks": 0,
            "failedChunks": 0,
            "pendingChunks": 0,
            "percent": 0,
        }
    tracker = ProgressTracker(db_path)
    try:
        counts = tracker.counts()
    finally:
        tracker.close()
    total = counts["pending"] + counts["done"] + counts["failed"]
    percent = round((counts["done"] + counts["failed"]) / total * 100, 2) if total else 0
    return {
        "totalChunks": total,
        "doneChunks": counts["done"],
        "failedChunks": counts["failed"],
        "pendingChunks": counts["pending"],
        "percent": percent,
    }


def _frontend_build_defaults(config: Any) -> dict[str, object]:
    defaults = config.frontend_build
    return {
        "replayEnabled": config.replay.enabled,
        "defaultProfile": defaults.default_profile,
        "defaultBackend": defaults.default_backend,
        "constructionMode": defaults.construction_mode,
        "maxConcepts": defaults.max_concepts,
        "semanticClustering": defaults.semantic_clustering,
        "instanceMode": defaults.instance_mode,
        "instanceCount": defaults.instance_count,
        "qaContextMode": defaults.qa_context_mode,
        "qaFallbackEnabled": defaults.qa_fallback_enabled,
        "llmTimeout": defaults.llm_timeout,
        "llmRetries": defaults.llm_retries,
        "llmConcurrency": defaults.llm_concurrency,
        "minConceptEvidence": defaults.min_concept_evidence,
        "abstractionLevel": defaults.abstraction_level or "",
    }


def _load_imported_ontology(content: bytes, suffix: str) -> FormattedOntology:
    if suffix == ".json":
        return FormattedOntology.model_validate_json(content)
    if suffix == ".jsonl":
        return _load_imported_ontology_jsonl(content)
    raise ValueError(f"Unsupported ontology suffix: {suffix}")


def _load_structured_config_yaml(content: bytes) -> dict[str, Any]:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(
            status_code=400,
            detail="Structured config YAML must be UTF-8 encoded",
        ) from exc
    try:
        payload = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid structured config YAML. {exc}",
        ) from exc
    if not isinstance(payload, dict) or not payload:
        raise HTTPException(
            status_code=400,
            detail="Structured config YAML must be a non-empty mapping",
        )
    return payload


def _load_imported_ontology_jsonl(content: bytes) -> FormattedOntology:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("Ontology JSONL must be UTF-8 encoded") from exc

    records: list[Any] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            records.append(json.loads(stripped))
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Invalid JSONL at line {line_number}: {exc.msg}"
            ) from exc

    if not records:
        raise ValueError("Ontology JSONL contains no records")
    if len(records) == 1:
        return FormattedOntology.model_validate(records[0])
    return FormattedOntology.model_validate(
        _merge_formatted_ontology_records(records)
    )


def _merge_formatted_ontology_records(records: list[Any]) -> dict[str, Any]:
    merged: dict[str, Any] = {
        "ontology": None,
        "objects": [],
        "links": [],
        "rules": [],
        "actions": [],
        "instances": [],
        "actionInvocations": [],
    }

    for record in records:
        if not isinstance(record, dict):
            raise ValueError("Each ontology JSONL record must be an object")
        if _merge_section_record(merged, record):
            continue
        if _merge_typed_record(merged, record):
            continue
        if _merge_inferred_item_record(merged, record):
            continue
        raise ValueError("Ontology JSONL record does not match a supported section")

    if merged["ontology"] is None:
        raise ValueError("Ontology JSONL is missing an ontology metadata record")
    return merged


def _merge_section_record(merged: dict[str, Any], record: dict[str, Any]) -> bool:
    matched = False
    for key in (
        "ontology",
        "objects",
        "links",
        "rules",
        "actions",
        "instances",
        "actionInvocations",
    ):
        if key not in record:
            continue
        matched = True
        if key == "ontology":
            merged[key] = record[key]
        else:
            _append_ontology_items(merged[key], record[key])
    return matched


def _merge_typed_record(merged: dict[str, Any], record: dict[str, Any]) -> bool:
    raw_kind = record.get("type") or record.get("section") or record.get("kind")
    if raw_kind is None:
        return False
    section = _ontology_jsonl_section(str(raw_kind))
    if section is None:
        return False
    payload = record.get("data", record.get("payload", record.get("value", record)))
    if section == "ontology":
        merged["ontology"] = payload
    else:
        _append_ontology_items(merged[section], payload)
    return True


def _merge_inferred_item_record(merged: dict[str, Any], record: dict[str, Any]) -> bool:
    section = _infer_ontology_item_section(record)
    if section is None:
        return False
    merged[section].append(record)
    return True


def _ontology_jsonl_section(value: str) -> str | None:
    normalized = value.replace("_", "").replace("-", "").lower()
    aliases = {
        "metadata": "ontology",
        "ontology": "ontology",
        "object": "objects",
        "objects": "objects",
        "link": "links",
        "links": "links",
        "rule": "rules",
        "rules": "rules",
        "businessrule": "rules",
        "businessrules": "rules",
        "action": "actions",
        "actions": "actions",
        "instance": "instances",
        "instances": "instances",
        "actioninvocation": "actionInvocations",
        "actioninvocations": "actionInvocations",
        "invocation": "actionInvocations",
        "invocations": "actionInvocations",
    }
    return aliases.get(normalized)


def _infer_ontology_item_section(record: dict[str, Any]) -> str | None:
    if "objectId" in record and "objectName" in record:
        return "objects"
    if "linkId" in record and "sourceObjectId" in record and "targetObjectId" in record:
        return "links"
    if "ruleId" in record and "ruleName" in record:
        return "rules"
    if "actionId" in record and "actionName" in record:
        return "actions"
    if "instanceId" in record and "instanceName" in record:
        return "instances"
    if "invocationId" in record and "actionName" in record:
        return "actionInvocations"
    return None


def _append_ontology_items(target: list[Any], value: Any) -> None:
    if isinstance(value, list):
        target.extend(value)
    else:
        target.append(value)


def _validation_error_message(exc: ValueError | ValidationError) -> str:
    if isinstance(exc, ValidationError):
        errors = exc.errors()
        return str(errors[0]["msg"]) if errors else str(exc)
    return str(exc)


def _list_job_summaries(store: JobStore) -> list[dict[str, object]]:
    if not store.root.exists():
        return []
    summaries = [
        summary
        for job_dir in store.root.iterdir()
        if job_dir.is_dir() and (summary := _job_summary(job_dir)) is not None
    ]
    return sorted(
        summaries,
        key=lambda item: str(item.get("updatedAt") or item.get("modifiedAt") or ""),
        reverse=True,
    )


def _job_summary(job_dir: Path) -> dict[str, object] | None:
    state_path = job_dir / "job.json"
    result_path = job_dir / "formatted_ontology.json"
    if not state_path.exists() or not result_path.exists():
        return None
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        result = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if state.get("hiddenFromHistory"):
        return None

    ontology = result.get("ontology") or {}
    instances = result.get("instances") or []
    instance_links = sum(len(instance.get("linkValues") or []) for instance in instances)
    input_path = state.get("inputPath")
    input_paths = state.get("inputPaths") or ([input_path] if input_path else [])
    input_names = state.get("inputNames") or [
        Path(str(path)).name for path in input_paths if path
    ]
    return {
        "jobId": state.get("jobId") or job_dir.name,
        "status": state.get("status", "unknown"),
        "profile": state.get("profile") or ontology.get("profile"),
        "ontologyName": ontology.get("ontologyName") or job_dir.name,
        "ontologyDesc": ontology.get("ontologyDesc") or "",
        "createdAt": ontology.get("createdAt") or "",
        "updatedAt": ontology.get("updatedAt") or "",
        "modifiedAt": job_dir.stat().st_mtime,
        "inputName": input_names[0] if input_names else "",
        "inputNames": input_names,
        "fileCount": len(input_names),
        "supplementalPrompt": state.get("supplementalPrompt") or "",
        "constructionMode": state.get("constructionMode") or "standard",
        "maxConcepts": state.get("maxConcepts"),
        "abstractionLevel": state.get("abstractionLevel") or "",
        "dropInstances": state.get("dropInstances"),
        "extractRealInstances": state.get("extractRealInstances"),
        "objectCount": len(result.get("objects") or []),
        "linkCount": len(result.get("links") or []),
        "ruleCount": len(result.get("rules") or []),
        "instanceCount": len(instances),
        "instanceLinkCount": instance_links,
        "qualityStatus": state.get("qualityStatus") or "",
        "qualityWarnings": state.get("qualityWarnings") or [],
        "qualityUncoveredEntities": state.get("qualityUncoveredEntities") or 0,
        "qualityQuarantinedEntities": state.get("qualityQuarantinedEntities") or 0,
        "hasCanonical": (job_dir / "schema.json").exists() and (job_dir / "graph.json").exists(),
        "hasReplay": (job_dir / "job_events.jsonl").exists(),
    }


def _ontology_version_history(store: JobStore, current_job_id: str) -> dict[str, Any]:
    records: dict[str, dict[str, Any]] = {}
    if store.root.exists():
        for job_dir in store.root.iterdir():
            state_path = job_dir / "job.json"
            result_path = job_dir / "formatted_ontology.json"
            if not job_dir.is_dir() or not state_path.exists() or not result_path.exists():
                continue
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if state.get("hiddenFromHistory") or state.get("status") != "completed":
                continue
            job_id = str(state.get("jobId") or job_dir.name)
            records[job_id] = {
                "state": state,
                "resultPath": result_path,
                "modifiedAt": job_dir.stat().st_mtime,
            }

    if current_job_id not in records:
        raise HTTPException(status_code=404, detail="Completed ontology version not found")

    root_job_id = current_job_id
    seen = {root_job_id}
    while True:
        parent_id = str(records[root_job_id]["state"].get("baseJobId") or "")
        if not parent_id or parent_id not in records or parent_id in seen:
            break
        root_job_id = parent_id
        seen.add(parent_id)

    children: dict[str, list[str]] = {}
    for job_id, record in records.items():
        parent_id = str(record["state"].get("baseJobId") or "")
        if parent_id in records:
            children.setdefault(parent_id, []).append(job_id)
    for values in children.values():
        values.sort(key=lambda value: _version_sort_key(records[value]))

    family_ids: list[str] = []

    def append_family(job_id: str) -> None:
        if job_id in family_ids:
            return
        family_ids.append(job_id)
        for child_id in children.get(job_id, []):
            append_family(child_id)

    append_family(root_job_id)
    for job_id in family_ids:
        try:
            records[job_id]["result"] = json.loads(
                records[job_id]["resultPath"].read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError) as exc:
            raise HTTPException(
                status_code=500, detail=f"Ontology version {job_id} is unreadable"
            ) from exc
    versions = []
    for version_number, job_id in enumerate(family_ids, start=1):
        record = records[job_id]
        state = record["state"]
        result = record["result"]
        parent_id = str(state.get("baseJobId") or "")
        parent = records.get(parent_id)
        change_counts, change_titles = _version_change_summary(store, state)
        current_documents = _source_documents_for_state(state)
        parent_documents = (
            _source_documents_for_state(parent["state"]) if parent else []
        )
        parent_document_keys = {
            (str(item.get("kind") or ""), str(item.get("name") or ""))
            for item in parent_documents
        }
        added_documents = [
            item
            for item in current_documents
            if (str(item.get("kind") or ""), str(item.get("name") or ""))
            not in parent_document_keys
        ]
        ontology = result.get("ontology") or {}
        schema_graph = formatted_ontology_to_graph(result, view="schema")
        versions.append(
            {
                "jobId": job_id,
                "parentJobId": parent_id or None,
                "version": version_number,
                "versionLabel": f"V{version_number}",
                "changeType": "document-update" if parent else "initial",
                "ontologyName": ontology.get("ontologyName") or job_id,
                "updatedAt": ontology.get("updatedAt") or "",
                "modifiedAt": record["modifiedAt"],
                "isCurrent": job_id == current_job_id,
                "nodeCount": len(schema_graph.nodes),
                "relationCount": len(schema_graph.edges),
                "addedNodeIds": _added_schema_node_ids(
                    parent["result"] if parent else None, result
                ),
                "appliedChangeCount": len(state.get("appliedChangeIds") or []),
                "changeCounts": change_counts,
                "changeTitles": change_titles,
                "updateScopes": state.get("updateScopes") or [],
                "addedDocuments": added_documents,
                "documents": current_documents,
            }
        )
    current_version = next(
        item["version"] for item in versions if item["jobId"] == current_job_id
    )
    return {
        "ontologyName": versions[0]["ontologyName"],
        "rootJobId": root_job_id,
        "currentJobId": current_job_id,
        "currentVersion": current_version,
        "versions": versions,
    }


def _version_sort_key(record: dict[str, Any]) -> tuple[str, float]:
    ontology = (record.get("result") or {}).get("ontology") or {}
    state = record.get("state") or {}
    return (
        str(
            ontology.get("updatedAt")
            or ontology.get("createdAt")
            or state.get("updatedAt")
            or state.get("createdAt")
            or ""
        ),
        float(record.get("modifiedAt") or 0),
    )


def _version_change_summary(
    store: JobStore, state: dict[str, Any]
) -> tuple[dict[str, int], list[str]]:
    counts = {"added": 0, "modified": 0, "objects": 0, "relations": 0, "rules": 0, "actions": 0}
    update_id = str(state.get("updateAnalysisId") or "")
    plan_path = store.job_dir(update_id) / "update_plan.json" if update_id else None
    if plan_path is None or not plan_path.exists():
        return counts, []
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return counts, []
    applied = {str(value) for value in state.get("appliedChangeIds") or []}
    titles: list[str] = []
    for change in plan.get("changes") or []:
        if str(change.get("changeId") or "") not in applied:
            continue
        operation = str(change.get("operation") or "")
        kind = str(change.get("kind") or "")
        if operation in {"added", "modified"}:
            counts[operation] += 1
        plural = {
            "object": "objects",
            "relation": "relations",
            "rule": "rules",
            "action": "actions",
        }.get(kind)
        if plural:
            counts[plural] += 1
        title = str(change.get("title") or "").strip()
        if title and title not in titles:
            titles.append(title)
    return counts, titles[:8]


def _added_schema_node_ids(
    parent: dict[str, Any] | None, current: dict[str, Any]
) -> list[str]:
    if parent is None:
        return []

    def ids(payload: dict[str, Any], section: str, field: str, prefix: str) -> set[str]:
        return {
            f"{prefix}:{item[field]}"
            for item in payload.get(section) or []
            if item.get(field) is not None
        }

    parent_ids = {
        *ids(parent, "objects", "objectId", "obj"),
        *ids(parent, "rules", "ruleId", "rule"),
        *ids(parent, "actions", "actionId", "action"),
    }
    current_ids = {
        *ids(current, "objects", "objectId", "obj"),
        *ids(current, "rules", "ruleId", "rule"),
        *ids(current, "actions", "actionId", "action"),
    }
    return sorted(current_ids.difference(parent_ids))


def _normalize_supplemental_prompt(value: str | None) -> str:
    return (value or "").strip()[:4000]


def _parse_json_string_list(raw: str) -> list[str]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Expected a JSON string list") from exc
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise HTTPException(status_code=400, detail="Expected a JSON string list")
    return list(dict.fromkeys(item.strip() for item in value if item.strip()))


def _source_documents_for_state(state: dict[str, object]) -> list[dict[str, Any]]:
    stored = state.get("sourceDocuments")
    if isinstance(stored, list):
        return _deduplicate_source_documents(
            [dict(item) for item in stored if isinstance(item, dict)]
        )
    names = state.get("inputOriginalNames") or state.get("inputNames") or []
    if not isinstance(names, list):
        names = [str(names)] if names else []
    return [
        {
            "documentId": f"current:{index}",
            "name": _display_input_name(str(name)),
            "kind": "current",
            "size": None,
        }
        for index, name in enumerate(names, start=1)
    ]


def _display_input_name(value: str) -> str:
    for prefix in ("input_", "update_"):
        if not value.startswith(prefix):
            continue
        parts = value.split("_", 2)
        if len(parts) == 3 and parts[1].isdigit():
            return parts[2]
    return value


def _builtin_update_document_payloads() -> list[dict[str, Any]]:
    payloads = []
    for document_id, definition in BUILTIN_UPDATE_DOCUMENTS.items():
        path = Path(definition["path"])
        payloads.append(
            {
                "documentId": document_id,
                "name": definition["name"],
                "description": definition["description"],
                "kind": "builtin",
                "fileType": path.suffix.lstrip(".").upper() or "TXT",
                "size": path.stat().st_size if path.exists() else None,
                "available": path.exists(),
            }
        )
    return payloads


def _deduplicate_source_documents(
    documents: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in documents:
        key = (str(item.get("kind") or ""), str(item.get("name") or ""))
        if key in seen:
            continue
        seen.add(key)
        result.append(dict(item))
    return result


def _ontology_update_scope_prompt(scopes: list[str]) -> str:
    labels = {
        "objects": "实体对象（含属性和实例）",
        "relations": "对象关系",
        "rules": "业务规则与指标",
        "actions": "行动与下游影响",
    }
    selected = "、".join(labels[scope] for scope in scopes)
    excluded = "、".join(
        labels[scope] for scope in UPDATE_SCOPES if scope not in scopes
    )
    exclusion = (
        f"不得产出或推断以下类型的新增内容：{excluded}。"
        if excluded
        else ""
    )
    return (
        "当前任务是为一个已有本体分析追加文档，不是重建或删除已有本体。"
        f"只允许提取以下更新范围：{selected}。{exclusion}"
        "为解析关系或行动引用而识别的端点概念，只能用于引用校验，"
        "未选实体对象时不得将其作为新对象更新。"
    )


def _read_update_plan(
    store: JobStore, base_job_id: str, update_id: str
) -> tuple[dict[str, object], dict[str, Any]]:
    try:
        state = store.read(update_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Ontology update not found") from exc
    if str(state.get("baseJobId") or "") != base_job_id:
        raise HTTPException(status_code=404, detail="Ontology update not found")
    if state.get("status") != "completed":
        raise HTTPException(status_code=409, detail="Ontology update analysis is not completed")
    path = store.job_dir(update_id) / "update_plan.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Ontology update plan not found")
    try:
        plan = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail="Ontology update plan is invalid") from exc
    return state, plan


def _read_update_candidate(store: JobStore, update_id: str) -> FormattedOntology:
    path = store.job_dir(update_id) / "formatted_ontology.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Ontology update candidate not found")
    return FormattedOntology.model_validate_json(path.read_text(encoding="utf-8"))


def _save_json_payload(payload: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _validate_upload_files(files: list[UploadFile]) -> None:
    unsupported = [
        file.filename or "input"
        for file in files
        if (Path(file.filename or "input.txt").suffix.lower() or ".txt")
        not in SUPPORTED_TEXT_SUFFIXES
    ]
    if unsupported:
        raise HTTPException(
            status_code=400,
            detail=(
                "Unsupported file type. Supported: "
                "TXT / Markdown / PDF / DOCX / LOG / JSON / JSONL. "
                f"Unsupported files: {', '.join(unsupported)}"
            ),
        )


def _safe_input_stem(value: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in value)
    return safe[:48].strip("_") or "input"


def _read_agent_or_404(agent_store: AgentStore, agent_id: str) -> OntologyAgentRecord:
    try:
        return agent_store.read(agent_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Ontology agent not found") from exc


def _ontology_agent_context(
    store: JobStore,
    job_id: str,
) -> OntologyContextSnapshot:
    formatted = _read_formatted_ontology(store, job_id)
    payload = formatted.model_dump(mode="json", by_alias=True)
    metadata = payload.get("ontology") or {}
    ontology_id = metadata.get("ontologyId")
    return OntologyContextSnapshot(
        jobId=job_id,
        ontologyId=str(ontology_id if ontology_id is not None else job_id),
        ontologyName=str(metadata.get("ontologyName") or job_id),
        profile=str(metadata.get("profile") or "default"),
        version=job_id,
        objectCount=len(payload.get("objects") or []),
        ruleCount=len(payload.get("rules") or []),
        actionCount=len(payload.get("actions") or []),
        instanceCount=len(payload.get("instances") or []),
    )


def _read_formatted_ontology(store: JobStore, job_id: str) -> FormattedOntology:
    try:
        state = store.read(job_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Job not found") from exc
    if state.get("status") != "completed":
        raise HTTPException(status_code=409, detail="Job is not completed")
    result_path = store.job_dir(job_id) / "formatted_ontology.json"
    if not result_path.exists():
        raise HTTPException(status_code=404, detail="Result not found")
    return FormattedOntology.model_validate_json(result_path.read_text(encoding="utf-8"))


def _frontend_dir() -> Path:
    return project_root().parent / "frontend"


@click.command()
@click.option("--host", default="0.0.0.0", show_default=True, help="API bind host.")
@click.option("--port", default=8000, show_default=True, type=int, help="API bind port.")
def main(host: str, port: int) -> None:
    """Start the Ontology Construct API server."""
    try:
        import uvicorn
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "uvicorn is not installed. Install API dependencies before starting the API."
        ) from exc
    uvicorn.run("ontology_construct.api:create_app", factory=True, host=host, port=port)
