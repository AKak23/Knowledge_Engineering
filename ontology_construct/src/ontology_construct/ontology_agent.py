from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
from threading import Lock
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ontology_construct.config import QAConfig
from ontology_construct.llm.client import LLMClient
from ontology_construct.output.formatted_ontology import FormattedOntology
from ontology_construct.qa import QAContextMode, QAResponse, answer_question


AgentStatus = Literal["draft", "published", "disabled"]
GraphView = Literal["auto", "schema", "instance"]
RESOURCE_ID_RE = re.compile(r"^(agent|session)_[0-9a-f]{32}$")
INVOKE_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{2,63}$")


def _now() -> str:
    return datetime.now(UTC).isoformat()


class AgentModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class AtomicAgentDefinition(AgentModel):
    agent_id: str = Field(alias="agentId")
    name: str
    description: str
    default_responsibility: str = Field(alias="defaultResponsibility")
    capabilities: list[str] = Field(default_factory=list)
    official: bool = True
    system_prompt: str = Field(exclude=True, repr=False, alias="systemPrompt")


ATOMIC_AGENT_CATALOG = (
    AtomicAgentDefinition(
        agentId="insight-analysis",
        name="洞察分析 Agent",
        description="识别关键对象、关系模式、异常信号与可验证的业务机会。",
        defaultResponsibility="负责对象画像、关系洞察与证据分析",
        capabilities=["entity_profile", "relationship_insight", "anomaly_analysis"],
        systemPrompt=(
            "你是洞察分析原子 Agent。只能依据提供的本体摘要与用户问题给出"
            "简洁建议；明确区分已知事实、推断和缺失证据，不得伪造数据。"
        ),
    ),
    AtomicAgentDefinition(
        agentId="location-analysis",
        name="区位分析 Agent",
        description="面向空间、区域、站点和地理关联完成区位比较与影响判断。",
        defaultResponsibility="负责空间分布、区域差异与区位影响分析",
        capabilities=["spatial_reasoning", "region_comparison", "location_impact"],
        systemPrompt=(
            "你是区位分析原子 Agent。检查本体中是否存在位置、区域、站点、距离或"
            "空间关系证据；若不存在，必须明确说明不足，不得补写地理事实。"
        ),
    ),
    AtomicAgentDefinition(
        agentId="data-questioning",
        name="问数分析 Agent",
        description="围绕指标、属性、口径和群组对比形成可追溯的数据结论。",
        defaultResponsibility="负责指标口径、属性统计、分组对比与数据缺口判断",
        capabilities=["metric_analysis", "aggregation", "data_gap_detection"],
        systemPrompt=(
            "你是问数分析原子 Agent。优先检查指标口径、属性值和可比维度。不允许"
            "凭空计算；无原始数值时只给出可执行的补数建议。"
        ),
    ),
    AtomicAgentDefinition(
        agentId="rule-reasoning",
        name="规则推理 Agent",
        description="检查本体规则、命中条件、触发行动与证据闭环。",
        defaultResponsibility="负责规则条件、命中解释、行动建议与风险约束",
        capabilities=["rule_explain", "action_advice", "risk_guardrail"],
        systemPrompt=(
            "你是规则推理原子 Agent。仅解释已提供的规则、条件和行动定义。"
            "可提出行动建议，但不得声称已经执行任何有副作用的操作。"
        ),
    ),
)
ATOMIC_AGENT_BY_ID = {item.agent_id: item for item in ATOMIC_AGENT_CATALOG}


class AgentMemberConfig(AgentModel):
    agent_id: str = Field(alias="agentId")
    responsibility: str = Field(min_length=1, max_length=500)

    @field_validator("agent_id")
    @classmethod
    def known_atomic_agent(cls, value: str) -> str:
        if value not in ATOMIC_AGENT_BY_ID:
            raise ValueError(f"Unknown atomic agent: {value}")
        return value


class OntologyContextSnapshot(AgentModel):
    job_id: str = Field(alias="jobId")
    ontology_id: str = Field(alias="ontologyId")
    ontology_name: str = Field(alias="ontologyName")
    profile: str = "default"
    version: str
    object_count: int = Field(default=0, alias="objectCount")
    rule_count: int = Field(default=0, alias="ruleCount")
    action_count: int = Field(default=0, alias="actionCount")
    instance_count: int = Field(default=0, alias="instanceCount")


class OntologyAgentCreate(AgentModel):
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=1000)
    collaboration_prompt: str = Field(default="", max_length=8000, alias="collaborationPrompt")
    ontology_job_id: str = Field(min_length=1, alias="ontologyJobId")
    members: list[AgentMemberConfig] = Field(default_factory=list, max_length=8)
    external_call_enabled: bool = Field(default=False, alias="externalCallEnabled")
    backend: str | None = Field(default=None, max_length=80)

    @field_validator("members")
    @classmethod
    def unique_members(cls, members: list[AgentMemberConfig]) -> list[AgentMemberConfig]:
        ids = [member.agent_id for member in members]
        if len(ids) != len(set(ids)):
            raise ValueError("Atomic agent members must be unique")
        return members


class OntologyAgentUpdate(OntologyAgentCreate):
    revision: int = Field(ge=1)


class OntologyAgentRecord(AgentModel):
    agent_id: str = Field(alias="agentId")
    name: str
    description: str = ""
    collaboration_prompt: str = Field(default="", alias="collaborationPrompt")
    ontology_context: OntologyContextSnapshot = Field(alias="ontologyContext")
    members: list[AgentMemberConfig] = Field(default_factory=list)
    external_call_enabled: bool = Field(default=False, alias="externalCallEnabled")
    backend: str | None = None
    status: AgentStatus = "draft"
    revision: int = 1
    published_version: int = Field(default=0, alias="publishedVersion")
    invoke_name: str = Field(alias="invokeName")
    credential_preview: str | None = Field(default=None, alias="credentialPreview")
    access_key_hash: str | None = Field(default=None, alias="accessKeyHash")
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")
    published_at: str | None = Field(default=None, alias="publishedAt")


class AgentPublishResult(AgentModel):
    agent: OntologyAgentRecord
    access_key: str | None = Field(default=None, alias="accessKey")
    invoke_path: str | None = Field(default=None, alias="invokePath")


class AgentMessage(AgentModel):
    message_id: str = Field(alias="messageId")
    role: Literal["user", "assistant"]
    content: str
    created_at: str = Field(alias="createdAt")
    collaboration: list[dict[str, Any]] = Field(default_factory=list)
    evidence: dict[str, Any] = Field(default_factory=dict)


class AgentSession(AgentModel):
    session_id: str = Field(alias="sessionId")
    agent_id: str = Field(alias="agentId")
    title: str
    messages: list[AgentMessage] = Field(default_factory=list)
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")


class AgentInvocationRequest(AgentModel):
    message: str = Field(min_length=1, max_length=10000)
    backend: str | None = Field(default=None, max_length=80)
    graph_view: GraphView = Field(default="auto", alias="graphView")
    session_id: str | None = Field(default=None, alias="sessionId")


class AgentStore:
    """Small file store for agent configuration and resumable conversations."""

    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()

    def create(self, payload: OntologyAgentCreate, context: OntologyContextSnapshot) -> OntologyAgentRecord:
        agent_id = f"agent_{uuid4().hex}"
        timestamp = _now()
        record = OntologyAgentRecord(
            agentId=agent_id,
            name=payload.name.strip(),
            description=payload.description.strip(),
            collaborationPrompt=payload.collaboration_prompt.strip(),
            ontologyContext=context,
            members=payload.members,
            externalCallEnabled=payload.external_call_enabled,
            backend=payload.backend,
            invokeName=_default_invoke_name(payload.name, agent_id),
            createdAt=timestamp,
            updatedAt=timestamp,
        )
        with self._lock:
            agent_dir = self._agent_dir(agent_id)
            agent_dir.mkdir(parents=True, exist_ok=False)
            (agent_dir / "sessions").mkdir()
            self._write_model(agent_dir / "agent.json", record)
        return record

    def list(self, ontology_job_id: str | None = None) -> list[OntologyAgentRecord]:
        records: list[OntologyAgentRecord] = []
        for path in self.root.glob("agent_*/agent.json"):
            try:
                record = OntologyAgentRecord.model_validate_json(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if ontology_job_id and record.ontology_context.job_id != ontology_job_id:
                continue
            records.append(record)
        return sorted(records, key=lambda item: item.updated_at, reverse=True)

    def read(self, agent_id: str) -> OntologyAgentRecord:
        path = self._agent_dir(agent_id) / "agent.json"
        try:
            return OntologyAgentRecord.model_validate_json(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise KeyError(agent_id) from None

    def find_by_invoke_name(self, invoke_name: str) -> OntologyAgentRecord:
        for record in self.list():
            if record.invoke_name == invoke_name:
                return record
        raise KeyError(invoke_name)

    def update(
        self,
        agent_id: str,
        payload: OntologyAgentUpdate,
        context: OntologyContextSnapshot,
    ) -> OntologyAgentRecord:
        with self._lock:
            current = self.read(agent_id)
            if payload.revision != current.revision:
                raise RevisionConflict(current.revision)
            updated = current.model_copy(
                update={
                    "name": payload.name.strip(),
                    "description": payload.description.strip(),
                    "collaboration_prompt": payload.collaboration_prompt.strip(),
                    "ontology_context": context,
                    "members": payload.members,
                    "external_call_enabled": payload.external_call_enabled,
                    "backend": payload.backend,
                    "status": "draft",
                    "revision": current.revision + 1,
                    "updated_at": _now(),
                    "published_at": None,
                    "access_key_hash": None,
                    "credential_preview": None,
                }
            )
            self._write_model(self._agent_dir(agent_id) / "agent.json", updated)
        return updated

    def publish(self, agent_id: str) -> AgentPublishResult:
        with self._lock:
            current = self.read(agent_id)
            access_key = None
            access_key_hash = None
            credential_preview = None
            if current.external_call_enabled:
                access_key = f"knova_{secrets.token_urlsafe(30)}"
                access_key_hash = _hash_access_key(access_key)
                credential_preview = f"{access_key[:10]}…{access_key[-4:]}"
            updated = current.model_copy(
                update={
                    "status": "published",
                    "revision": current.revision + 1,
                    "published_version": current.published_version + 1,
                    "updated_at": _now(),
                    "published_at": _now(),
                    "access_key_hash": access_key_hash,
                    "credential_preview": credential_preview,
                }
            )
            self._write_model(self._agent_dir(agent_id) / "agent.json", updated)
        invoke_path = (
            f"/v1/ontology-agents/{updated.invoke_name}/invoke"
            if updated.external_call_enabled
            else None
        )
        return AgentPublishResult(agent=updated, accessKey=access_key, invokePath=invoke_path)

    def create_session(self, agent_id: str, title: str = "新对话") -> AgentSession:
        self.read(agent_id)
        session_id = f"session_{uuid4().hex}"
        timestamp = _now()
        session = AgentSession(
            sessionId=session_id,
            agentId=agent_id,
            title=(title.strip() or "新对话")[:80],
            createdAt=timestamp,
            updatedAt=timestamp,
        )
        with self._lock:
            self._write_model(self._session_path(agent_id, session_id), session)
        return session

    def list_sessions(self, agent_id: str) -> list[AgentSession]:
        self.read(agent_id)
        sessions: list[AgentSession] = []
        for path in (self._agent_dir(agent_id) / "sessions").glob("session_*.json"):
            try:
                sessions.append(AgentSession.model_validate_json(path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
        return sorted(sessions, key=lambda item: item.updated_at, reverse=True)

    def read_session(self, agent_id: str, session_id: str) -> AgentSession:
        try:
            return AgentSession.model_validate_json(
                self._session_path(agent_id, session_id).read_text(encoding="utf-8")
            )
        except FileNotFoundError:
            raise KeyError(session_id) from None

    def append_message(self, agent_id: str, session_id: str, message: AgentMessage) -> AgentSession:
        with self._lock:
            session = self.read_session(agent_id, session_id)
            title = session.title
            if not session.messages and message.role == "user":
                title = message.content.strip().replace("\n", " ")[:32] or title
            updated = session.model_copy(
                update={
                    "title": title,
                    "messages": [*session.messages, message],
                    "updated_at": _now(),
                }
            )
            self._write_model(self._session_path(agent_id, session_id), updated)
        return updated

    def verify_access_key(self, record: OntologyAgentRecord, access_key: str | None) -> bool:
        if not record.access_key_hash or not access_key:
            return False
        return hmac.compare_digest(record.access_key_hash, _hash_access_key(access_key))

    def _agent_dir(self, agent_id: str) -> Path:
        _validate_resource_id(agent_id, "agent")
        return self.root / agent_id

    def _session_path(self, agent_id: str, session_id: str) -> Path:
        _validate_resource_id(session_id, "session")
        return self._agent_dir(agent_id) / "sessions" / f"{session_id}.json"

    @staticmethod
    def _write_model(path: Path, model: BaseModel) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        target = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        target.write_text(
            json.dumps(
                model.model_dump(mode="json", by_alias=True),
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        os.replace(target, path)


class RevisionConflict(Exception):
    def __init__(self, current_revision: int):
        self.current_revision = current_revision
        super().__init__(f"Agent revision conflict; current revision is {current_revision}")


@dataclass(frozen=True)
class PiAgentRunResult:
    response: QAResponse
    collaboration: list[dict[str, Any]]


class OntologyPiAgentRuntime:
    """Pi Agent Core-style loop with official atomic agents exposed as tools.

    Pi keeps the core loop small and leaves persistence to the application. This
    runtime follows that boundary: it transforms the persisted message context,
    executes selected agent tools in parallel, appends tool results to the turn,
    and asks the assistant for a final evidence-constrained response. It uses the
    existing Python model adapter and deliberately adds no graph workflow runtime.
    """

    async def invoke(
        self,
        *,
        agent: OntologyAgentRecord,
        ontology: FormattedOntology,
        question: str,
        history: list[AgentMessage],
        llm_client: LLMClient,
        context_mode: QAContextMode,
        qa_config: QAConfig,
        graph_view: GraphView,
        fallback_enabled: bool = True,
    ) -> PiAgentRunResult:
        ontology_summary = _ontology_summary(ontology)
        member_tasks = [
            self._run_atomic_agent(
                definition=ATOMIC_AGENT_BY_ID[member.agent_id],
                responsibility=member.responsibility,
                question=question,
                ontology_summary=ontology_summary,
                llm_client=llm_client,
            )
            for member in agent.members
        ]
        member_results = (
            await asyncio.gather(*member_tasks, return_exceptions=True)
            if member_tasks
            else []
        )
        collaboration: list[dict[str, Any]] = []
        for member, result in zip(agent.members, member_results, strict=True):
            definition = ATOMIC_AGENT_BY_ID[member.agent_id]
            if isinstance(result, Exception):
                collaboration.append(
                    {
                        "agentId": member.agent_id,
                        "name": definition.name,
                        "status": "degraded",
                        "summary": f"协同分析暂不可用：{str(result)[:240]}",
                    }
                )
            else:
                collaboration.append(
                    {
                        "agentId": member.agent_id,
                        "name": definition.name,
                        "status": "completed",
                        "summary": result[:1200],
                    }
                )

        coordinated_question = _transform_pi_context(
            question=question,
            collaboration_prompt=agent.collaboration_prompt,
            history=history,
            collaboration=collaboration,
        )
        response = await answer_question(
            question=coordinated_question,
            ontology=ontology,
            llm_client=llm_client,
            graph_view=graph_view,
            context_mode=context_mode,
            fallback_enabled=fallback_enabled,
            qa_config=qa_config,
        )
        response.meta = {
            **(response.meta or {}),
            "agent_runtime": "pi-agent",
            "tool_execution": "parallel",
            "agent_id": agent.agent_id,
            "agent_revision": agent.revision,
            "ontology_version": agent.ontology_context.version,
            "collaborator_count": len(agent.members),
        }
        return PiAgentRunResult(response=response, collaboration=collaboration)

    @staticmethod
    async def _run_atomic_agent(
        *,
        definition: AtomicAgentDefinition,
        responsibility: str,
        question: str,
        ontology_summary: str,
        llm_client: LLMClient,
    ) -> str:
        prompt = (
            f"[你的协同职责]\n{responsibility}\n\n"
            f"[本体摘要]\n{ontology_summary}\n\n"
            f"[用户问题]\n{question}\n\n"
            "请输出不超过 8 条的协同分析，供主智能体参考。"
        )
        result = await llm_client.complete(prompt, system_prompt=definition.system_prompt)
        return result.strip()


def new_message(
    role: Literal["user", "assistant"],
    content: str,
    *,
    collaboration: list[dict[str, Any]] | None = None,
    evidence: dict[str, Any] | None = None,
) -> AgentMessage:
    return AgentMessage(
        messageId=f"message_{uuid4().hex}",
        role=role,
        content=content,
        createdAt=_now(),
        collaboration=collaboration or [],
        evidence=evidence or {},
    )


def public_agent_payload(record: OntologyAgentRecord) -> dict[str, Any]:
    return record.model_dump(
        mode="json",
        by_alias=True,
        exclude={"access_key_hash"},
    )


def public_catalog_payload() -> list[dict[str, Any]]:
    return [item.model_dump(mode="json", by_alias=True) for item in ATOMIC_AGENT_CATALOG]


def _validate_resource_id(value: str, kind: Literal["agent", "session"]) -> None:
    if not RESOURCE_ID_RE.fullmatch(value) or not value.startswith(f"{kind}_"):
        raise KeyError(value)


def _default_invoke_name(name: str, agent_id: str) -> str:
    ascii_name = name.casefold().strip()
    ascii_name = re.sub(r"[^a-z0-9]+", "-", ascii_name).strip("-")
    suffix = agent_id[-8:]
    candidate = f"{ascii_name[:45]}-{suffix}" if ascii_name else f"ontology-agent-{suffix}"
    if not candidate[0].isalpha():
        candidate = f"agent-{candidate}"
    candidate = candidate[:64].rstrip("-")
    if not INVOKE_NAME_RE.fullmatch(candidate):
        return f"ontology-agent-{suffix}"
    return candidate


def _hash_access_key(access_key: str) -> str:
    return hashlib.sha256(access_key.encode("utf-8")).hexdigest()


def _ontology_summary(ontology: FormattedOntology) -> str:
    payload = ontology.model_dump(mode="json")
    objects = payload.get("objects") or []
    rules = payload.get("rules") or []
    actions = payload.get("actions") or []
    instances = payload.get("instances") or []
    summary = {
        "ontology": payload.get("ontology") or {},
        "objects": [item.get("objectName") for item in objects[:80]],
        "rules": [item.get("ruleName") for item in rules[:40]],
        "actions": [item.get("actionName") for item in actions[:40]],
        "instanceCount": len(instances),
    }
    return json.dumps(summary, ensure_ascii=False)


def _transform_pi_context(
    *,
    question: str,
    collaboration_prompt: str,
    history: list[AgentMessage],
    collaboration: list[dict[str, Any]],
) -> str:
    recent_history = history[-8:]
    history_text = "\n".join(
        f"{'用户' if item.role == 'user' else '智能体'}：{item.content[:1000]}"
        for item in recent_history
    ) or "（无）"
    collaborator_text = "\n".join(
        f"- {item['name']}（{item['status']}）：{item['summary']}"
        for item in collaboration
    ) or "（未配置协同成员）"
    return (
        "[主智能体协同提示]\n"
        f"{collaboration_prompt or '优先依据当前本体和可追溯证据回答。'}\n\n"
        "[近期对话]\n"
        f"{history_text}\n\n"
        "[原子 Agent 建议]\n"
        f"{collaborator_text}\n\n"
        "[当前用户问题]\n"
        f"{question}\n\n"
        "原子 Agent 输出只是建议，不是事实来源。最终结论必须以本体证据为准，"
        "证据不足时要明确说明。"
    )
