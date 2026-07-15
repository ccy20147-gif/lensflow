"""TF-ASR-001: Agent API Routes — PostgreSQL-backed.

Endpoints for Agent definitions, draft/revision lifecycle with CAS,
validation, and dry-run compilation.
"""
from __future__ import annotations

from uuid import UUID
from typing import Any

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from src.core.exceptions import ConflictError, ForbiddenError, NotFoundError, PolicyBlockedError, ValidationError_
from src.infra.db.agent_repository import SqlAgentService
from src.infra.db.skill_repository import SqlSkillRepository
from src.schemas.enums import AgentKind
from src.schemas.models import AgentRevision, OwnerScope
from src.domain.agent.request_input import AgentRequestInputService
from src.domain.agent.invocation_service import AgentInvocationService
from src.infra.db.identity_repository import get_session_store

_agent = SqlAgentService()
_request_input = AgentRequestInputService()
_invocations = AgentInvocationService()
_sessions = get_session_store()

router = APIRouter(prefix="/api/v1/agents", tags=["agents"])


# -- Request / Response models --


class CreateAgentRequest(BaseModel):
    name: str
    description: str = ""
    agent_kind: AgentKind = AgentKind.CONFIGURABLE


class UpdateAgentRequest(BaseModel):
    name: str | None = None
    description: str | None = None


class CreateRevisionRequest(BaseModel):
    body: dict
    base_hash: str | None = None


class SaveAgentDraftRequest(BaseModel):
    body: dict
    base_draft_version: int


class SubmitAgentDraftRequest(BaseModel):
    base_draft_version: int


class DryRunAgentDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    draft_version: int
    budget: dict[str, Any] = {}
    fixed_input: dict[str, Any] = {}


class CloneAgentRequest(BaseModel):
    name: str


class TrialRequestInputRequest(BaseModel):
    schema_ref: str
    question: str
    input_schema: dict[str, Any]


class ResolveTrialRequestInputRequest(BaseModel):
    task_version: int
    answer: dict[str, Any]


class AgentValidateRequest(BaseModel):
    body: dict


class AgentDryRunRequest(BaseModel):
    body: dict


class CreateRequestInputRequest(BaseModel):
    run_id: UUID
    node_run_id: UUID
    attempt_id: UUID
    schema_ref: str
    question: str
    timeout_minutes: int = 60
    idempotency_token: str
    input_schema: dict[str, Any]


class ResolveRequestInputRequest(BaseModel):
    task_version: int
    idempotency_token: str
    answer: dict[str, Any]


def _request_input_owner(authorization: str | None) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing authorization header")
    try:
        account_id = _sessions.account_for_token(authorization.removeprefix("Bearer "))
    except (NotFoundError, ConflictError) as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_dict()) from exc
    return f"user:{account_id}"


def _require_mutable_agent(agent_id: UUID) -> None:
    """Managed presets are platform-owned immutable contracts, never UI drafts."""
    definition = _agent._repo.get_definition(agent_id)
    if str(definition.agent_kind) == AgentKind.MANAGED_PRESET.value:
        raise ForbiddenError("Managed preset Agent is locked and cannot be edited or deleted")


def _require_agent_owner(agent_id: UUID, authorization: str | None) -> str:
    """Resolve ownership from the bearer rather than request-controlled data."""
    owner_scope = _request_input_owner(authorization)
    definition = _agent._repo.get_definition(agent_id)
    if definition.owner_scope != owner_scope:
        raise ForbiddenError("Agent belongs to a different owner_scope")
    return owner_scope


class PrepareAgentInvokeRequest(BaseModel):
    agent_revision_id: UUID
    typed_inputs: dict[str, Any] = {}
    budget: dict[str, Any] = {}


class ExecuteAgentInvokeRequest(PrepareAgentInvokeRequest):
    node_run_attempt_id: UUID
    idempotency_key: str


class ValidateOrchestrationRequest(BaseModel):
    graph: dict[str, Any]


class DryRunResponse(BaseModel):
    valid: bool
    step_count: int


# -- Definition endpoints --


@router.post("")
async def create_agent(body: CreateAgentRequest, authorization: str | None = Header(None)) -> dict:
    """Create a new Agent definition."""
    try:
        row = _agent._repo.create_definition(
            name=body.name,
            description=body.description,
            agent_kind=body.agent_kind.value,
            owner_scope=_request_input_owner(authorization),
        )
    except ValidationError_ as e:
        raise HTTPException(status_code=e.status_code, detail=e.to_dict())
    return {
        "agent_id": str(row.agent_id),
        "name": row.name,
        "description": row.description,
        "agent_kind": row.agent_kind,
        "owner_scope": row.owner_scope,
        "created_at": row.created_at.isoformat() if row.created_at else "",
        "updated_at": row.updated_at.isoformat() if row.updated_at else "",
    }


@router.get("/published")
async def list_published_agents(authorization: str | None = Header(None)) -> dict[str, Any]:
    """Owner-scoped active AgentRevision catalog for dynamic canvas nodes.

    It must precede ``/{agent_id}`` so FastAPI does not parse the literal
    ``published`` as a UUID path parameter.
    """
    return await _published_agents(authorization)


@router.get("/studio/dependencies")
async def studio_dependency_catalog(authorization: str | None = Header(None)) -> dict[str, list[dict[str, Any]]]:
    """Return only owner-accessible immutable dependencies for Agent Studio.

    This endpoint deliberately returns revision IDs, not arbitrary free-text
    handles. Cross-owner Skills must carry an active grant snapshot; Tools
    remain same-owner and must have platform approval.
    """
    from src.infra.db.models import (
        ResourceGrantSnapshotModel, SkillContentModel, SkillRevisionModel,
        ToolDefinitionModel, ToolRevisionModel,
    )
    owner_scope = _request_input_owner(authorization)
    skills: list[dict[str, Any]] = []
    tools: list[dict[str, Any]] = []
    with _agent._factory() as session:
        for revision in session.scalars(select(SkillRevisionModel).where(SkillRevisionModel.status == "active")):
            skill = session.get(SkillContentModel, revision.skill_id)
            if skill is None:
                continue
            ref: dict[str, Any] | None = None
            if skill.owner_scope == owner_scope:
                ref = {"revision_id": str(revision.revision_id)}
            else:
                grant = session.scalar(select(ResourceGrantSnapshotModel).where(
                    ResourceGrantSnapshotModel.resource_revision_id == revision.revision_id,
                    ResourceGrantSnapshotModel.grantee_scope == owner_scope,
                    ResourceGrantSnapshotModel.status == "active",
                ))
                if grant is not None and {"reference", "execute"}.issubset(set(grant.capability_actions or [])):
                    ref = {"resource_id": str(skill.skill_id), "resource_type": "skill",
                           "revision_id": str(revision.revision_id), "grant_snapshot_id": str(grant.grant_snapshot_id)}
            if ref is not None:
                skills.append({"name": skill.name, "description": skill.description,
                               "owner_scope": skill.owner_scope, "ref": ref})
        for revision in session.scalars(select(ToolRevisionModel).where(
            ToolRevisionModel.status == "active", ToolRevisionModel.approval_status == "approved",
        )):
            tool = session.get(ToolDefinitionModel, revision.tool_id)
            if tool is None or tool.owner_scope != owner_scope:
                continue
            tools.append({"revision_id": str(revision.revision_id), "name": tool.name,
                          "description": tool.description, "operations": [
                              {"operation_id": item.get("id"), "disclosure_fields": item.get("disclosure_fields", [])}
                              for item in (revision.body or {}).get("operations", []) if isinstance(item, dict)
                          ]})
    return {"skills": sorted(skills, key=lambda item: (item["name"], str(item["ref"]))),
            "tools": sorted(tools, key=lambda item: (item["name"], item["revision_id"]))}


@router.get("/{agent_id}")
async def get_agent(agent_id: UUID, authorization: str | None = Header(None)) -> dict:
    """Get an Agent definition by ID."""
    try:
        _require_agent_owner(agent_id, authorization)
        row = _agent._repo.get_definition(agent_id)
    except (NotFoundError, ForbiddenError) as e:
        raise HTTPException(status_code=e.status_code, detail=e.to_dict())
    return {
        "agent_id": str(row.agent_id),
        "name": row.name,
        "description": row.description,
        "agent_kind": row.agent_kind,
        "owner_scope": row.owner_scope,
        "created_at": row.created_at.isoformat() if row.created_at else "",
        "updated_at": row.updated_at.isoformat() if row.updated_at else "",
    }


@router.get("")
async def list_agents(authorization: str | None = Header(None)) -> list[dict]:
    """List all Agent definitions, optionally filtered by owner_scope."""
    rows = _agent._repo.list_definitions(owner_scope=_request_input_owner(authorization))
    return [
        {
            "agent_id": str(r.agent_id),
            "name": r.name,
            "description": r.description,
            "agent_kind": r.agent_kind,
            "owner_scope": r.owner_scope,
            "created_at": r.created_at.isoformat() if r.created_at else "",
            "updated_at": r.updated_at.isoformat() if r.updated_at else "",
        }
        for r in rows
    ]


@router.patch("/{agent_id}")
async def update_agent(agent_id: UUID, body: UpdateAgentRequest, authorization: str | None = Header(None)) -> dict:
    """Update an Agent definition's metadata."""
    try:
        _require_agent_owner(agent_id, authorization)
        _require_mutable_agent(agent_id)
        row = _agent._repo.update_definition(
            agent_id, name=body.name, description=body.description
        )
    except (NotFoundError, ForbiddenError) as e:
        raise HTTPException(status_code=e.status_code, detail=e.to_dict())
    return {
        "agent_id": str(row.agent_id),
        "name": row.name,
        "description": row.description,
        "agent_kind": row.agent_kind,
        "owner_scope": row.owner_scope,
        "created_at": row.created_at.isoformat() if row.created_at else "",
        "updated_at": row.updated_at.isoformat() if row.updated_at else "",
    }


@router.delete("/{agent_id}")
async def delete_agent(agent_id: UUID, authorization: str | None = Header(None)) -> dict:
    """Delete an Agent definition and all its revisions."""
    try:
        _require_agent_owner(agent_id, authorization)
        _require_mutable_agent(agent_id)
        _agent._repo.delete_definition(agent_id)
    except (NotFoundError, ForbiddenError) as e:
        raise HTTPException(status_code=e.status_code, detail=e.to_dict())
    return {"status": "deleted"}


# -- Revision endpoints --


@router.get("/{agent_id}/draft")
async def get_agent_draft(agent_id: UUID, authorization: str | None = Header(None)) -> dict:
    try:
        _require_agent_owner(agent_id, authorization)
        row = _agent._repo.get_draft(agent_id)
        return {
            "agent_id": str(row.agent_id), "draft_version": row.draft_version,
            "base_revision_id": str(row.base_revision_id) if row.base_revision_id else None,
            "body": row.body, "content_hash": row.content_hash,
        }
    except (NotFoundError, ForbiddenError) as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_dict()) from exc


@router.put("/{agent_id}/draft")
async def save_agent_draft(agent_id: UUID, body: SaveAgentDraftRequest,
                           authorization: str | None = Header(None)) -> dict:
    try:
        _require_agent_owner(agent_id, authorization)
        _require_mutable_agent(agent_id)
        row = _agent._repo.save_draft(agent_id, body=body.body, base_draft_version=body.base_draft_version)
        return {
            "agent_id": str(row.agent_id), "draft_version": row.draft_version,
            "base_revision_id": str(row.base_revision_id) if row.base_revision_id else None,
            "body": row.body, "content_hash": row.content_hash,
        }
    except (NotFoundError, ConflictError, ForbiddenError, ValidationError_) as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_dict()) from exc


@router.post("/{agent_id}/draft/submit", response_model=AgentRevision)
async def submit_agent_draft(agent_id: UUID, body: SubmitAgentDraftRequest,
                             authorization: str | None = Header(None)) -> AgentRevision:
    try:
        _require_agent_owner(agent_id, authorization)
        _require_mutable_agent(agent_id)
        return _agent._repo.submit_draft(agent_id, base_draft_version=body.base_draft_version)
    except (NotFoundError, ConflictError, ForbiddenError, ValidationError_) as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_dict()) from exc


@router.post("/{agent_id}/draft/dry-run")
async def dry_run_agent_draft(agent_id: UUID, body: DryRunAgentDraftRequest,
                              authorization: str | None = Header(None)) -> dict:
    try:
        _require_agent_owner(agent_id, authorization)
        return _agent._repo.run_isolated_runtime_trial(agent_id, draft_version=body.draft_version, budget=body.budget,
            fixed_input=body.fixed_input)
    except (NotFoundError, ConflictError, ForbiddenError, ValidationError_) as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_dict()) from exc


@router.post("/{agent_id}/clone")
async def clone_agent(agent_id: UUID, body: CloneAgentRequest,
                      authorization: str | None = Header(None)) -> dict:
    try:
        owner_scope = _request_input_owner(authorization)
        _require_agent_owner(agent_id, authorization)
        clone = _agent._repo.clone_definition(agent_id, owner_scope=owner_scope, name=body.name)
        return {"agent_id": str(clone.agent_id), "cloned_from_agent_id": str(agent_id),
                "credential_bindings": "scrubbed_rebind_required",
                "credential_rebind_required_tool_revision_ids": _agent._repo.clone_rebind_requirements(clone.agent_id)}
    except (NotFoundError, ConflictError, ForbiddenError, ValidationError_) as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_dict()) from exc


@router.post("/trials/{trial_id}/request-input", status_code=201)
async def create_trial_request_input(trial_id: UUID, body: TrialRequestInputRequest,
                                     authorization: str | None = Header(None)) -> dict:
    try:
        owner = _request_input_owner(authorization)
        from src.infra.db.models import AgentTrialRunModel
        with _agent._factory() as session:
            trial = session.get(AgentTrialRunModel, trial_id)
            if trial is None:
                raise NotFoundError("AgentTrialRun", str(trial_id))
            if trial.owner_scope != owner:
                raise ForbiddenError("Agent trial belongs to a different owner")
            if not all((trial.runtime_run_id, trial.runtime_node_run_id, trial.runtime_attempt_id, trial.runtime_agent_revision_id)):
                raise ValidationError_("Agent trial has no durable runtime attempt")
            values = (trial.runtime_run_id, trial.runtime_node_run_id, trial.runtime_attempt_id, trial.runtime_agent_revision_id)
        row = _request_input.create(
            agent_revision_id=values[3], run_id=values[0], node_run_id=values[1], attempt_id=values[2],
            schema_ref=body.schema_ref, question=body.question, timeout_minutes=60,
            idempotency_token=f"trial:{trial_id}:request-input", input_schema=body.input_schema,
            requester_scope=owner,
        )
        return {"task_id": str(row.task_id), "status": row.status.value, "task_version": row.task_version}
    except (NotFoundError, ConflictError, ForbiddenError, ValidationError_) as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_dict()) from exc


@router.get("/trials/{trial_id}/request-input")
async def list_trial_request_inputs(trial_id: UUID, authorization: str | None = Header(None)) -> list[dict]:
    try:
        owner = _request_input_owner(authorization)
        from src.infra.db.models import AgentTrialRunModel, HumanTaskModel
        with _agent._factory() as session:
            trial = session.get(AgentTrialRunModel, trial_id)
            if trial is None or trial.owner_scope != owner:
                raise NotFoundError("AgentTrialRun", str(trial_id))
            if trial.runtime_attempt_id is None:
                return []
            rows = list(session.query(HumanTaskModel).filter(
                HumanTaskModel.attempt_id == trial.runtime_attempt_id,
                HumanTaskModel.task_kind == "request_input", HumanTaskModel.owner_layer == "agent",
            ).order_by(HumanTaskModel.created_at.desc()))
        return [{"task_id": str(row.task_id), "trial_id": str(trial_id), "schema_ref": row.schema_ref,
                 "question": (row.timeout_policy or {}).get("question", ""),
                 "input_schema": (row.timeout_policy or {}).get("input_schema", {}),
                 "status": row.status.value, "task_version": row.task_version, "answer": None} for row in rows]
    except (NotFoundError, ForbiddenError) as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_dict()) from exc


@router.get("/trial-request-input/{task_id}")
async def get_trial_request_input(task_id: UUID, authorization: str | None = Header(None)) -> dict:
    try:
        owner = _request_input_owner(authorization)
        from src.infra.db.models import HumanTaskModel, WorkflowRunModel
        with _agent._factory() as session:
            row = session.get(HumanTaskModel, task_id)
            run = session.get(WorkflowRunModel, row.run_id) if row else None
            if row is None or run is None or run.owner_scope != owner or row.task_kind != "request_input" or row.owner_layer != "agent":
                raise NotFoundError("AgentTrialRequestInput", str(task_id))
        return {"task_id": str(row.task_id), "schema_ref": row.schema_ref,
                "question": (row.timeout_policy or {}).get("question", ""),
                "input_schema": (row.timeout_policy or {}).get("input_schema", {}),
                "status": row.status.value, "task_version": row.task_version, "answer": None}
    except (NotFoundError, ForbiddenError) as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_dict()) from exc


@router.post("/trial-request-input/{task_id}/answer")
async def resolve_trial_request_input(task_id: UUID, body: ResolveTrialRequestInputRequest,
                                     authorization: str | None = Header(None)) -> dict:
    try:
        row = _request_input.resolve(task_id=task_id, task_version=body.task_version,
            idempotency_token=f"trial:{task_id}:answer:{body.task_version}", answer=body.answer,
            requester_scope=_request_input_owner(authorization))
        return {"task_id": str(row.task_id), "status": row.status.value, "task_version": row.task_version}
    except (NotFoundError, ConflictError, ForbiddenError, ValidationError_) as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_dict()) from exc


@router.post("/{agent_id}/revisions", response_model=AgentRevision)
async def create_agent_revision(agent_id: UUID, body: CreateRevisionRequest, authorization: str | None = Header(None)) -> AgentRevision:
    """Create a new draft revision for an Agent (with CAS)."""
    try:
        _agent.validate(body.body)
    except ValidationError_ as e:
        raise HTTPException(status_code=e.status_code, detail=e.to_dict())
    try:
        _require_agent_owner(agent_id, authorization)
        _require_mutable_agent(agent_id)
        return _agent._repo.create_revision(agent_id, body.body, base_hash=body.base_hash)
    except (ConflictError, NotFoundError, ForbiddenError) as e:
        raise HTTPException(status_code=e.status_code, detail=e.to_dict())


@router.get("/{agent_id}/revisions", response_model=list[AgentRevision])
async def list_agent_revisions(agent_id: UUID, authorization: str | None = Header(None)) -> list[AgentRevision]:
    """List all revisions for an Agent."""
    try:
        _require_agent_owner(agent_id, authorization)
    except (NotFoundError, ForbiddenError) as e:
        raise HTTPException(status_code=e.status_code, detail=e.to_dict())
    return _agent._repo.list_revisions(agent_id)


@router.get("/{agent_id}/revisions/{revision_id}", response_model=AgentRevision)
async def get_agent_revision(agent_id: UUID, revision_id: UUID, authorization: str | None = Header(None)) -> AgentRevision:
    """Get a specific Agent revision."""
    try:
        _require_agent_owner(agent_id, authorization)
        _agent._repo.ensure_revision_belongs_to(agent_id, revision_id)
        return _agent._repo.get_revision(revision_id)
    except (NotFoundError, ForbiddenError) as e:
        raise HTTPException(status_code=e.status_code, detail=e.to_dict())


@router.post("/{agent_id}/revisions/{revision_id}/promote", response_model=AgentRevision)
async def promote_agent_revision(agent_id: UUID, revision_id: UUID, authorization: str | None = Header(None)) -> AgentRevision:
    """Promote a draft revision to active."""
    try:
        _require_agent_owner(agent_id, authorization)
        _agent._repo.ensure_revision_belongs_to(agent_id, revision_id)
        return _agent._repo.promote_revision(revision_id)
    except (NotFoundError, ConflictError, ForbiddenError) as e:
        raise HTTPException(status_code=e.status_code, detail=e.to_dict())


@router.post("/{agent_id}/revisions/{revision_id}/retire", response_model=AgentRevision)
async def retire_agent_revision(agent_id: UUID, revision_id: UUID, authorization: str | None = Header(None)) -> AgentRevision:
    """Retire an active revision."""
    try:
        _require_agent_owner(agent_id, authorization)
        _agent._repo.ensure_revision_belongs_to(agent_id, revision_id)
        return _agent._repo.retire_revision(revision_id)
    except (NotFoundError, ConflictError, ForbiddenError) as e:
        raise HTTPException(status_code=e.status_code, detail=e.to_dict())


# -- Validation / Dry-run endpoints --


def _agent_port(schema_ref: str, port_id: str, *, required: bool) -> dict[str, Any]:
    schema_id, marker, version = schema_ref.rpartition(".v")
    if not marker or not schema_id or not version.isdigit():
        schema_id, version = "toonflow.agent_output", "1"
    return {"port_id": port_id, "type_id": "artifact", "schema_id": schema_id,
            "schema_version": int(version), "cardinality": "required" if required else "optional"}


async def _published_agents(authorization: str | None = Header(None)) -> dict[str, Any]:
    """List Agent definitions with at least one active revision.

    Returns the latest active revision per agent.  The canvas palette
    uses this to surface runnable Agents as node candidates.
    """
    from src.domain.provider.atlascloud import AtlasCloudAdapter
    from src.infra.db.models import AgentDefinitionModel, AgentRevisionModel
    from sqlalchemy import select
    provider_configured = AtlasCloudAdapter().configured
    with _agent._factory() as session:
        owner_scope = _request_input_owner(authorization)
        agents = session.scalars(select(AgentDefinitionModel).where(AgentDefinitionModel.owner_scope == owner_scope)).all()
        out = []
        for a in agents:
            rev = session.scalar(
                select(AgentRevisionModel)
                .where(AgentRevisionModel.agent_id == a.agent_id, AgentRevisionModel.status == "active")
                .order_by(AgentRevisionModel.revision_number.desc())
                .limit(1)
            )
            if rev is None:
                continue
            out.append(
                {
                    "agent_id": str(a.agent_id),
                    "name": a.name,
                    "description": a.description,
                    "agent_kind": a.agent_kind,
                    "owner_scope": a.owner_scope,
                    "revision_id": str(rev.revision_id),
                    "revision_number": rev.revision_number,
                    "content_hash": rev.content_hash,
                    "provider_configured": provider_configured,
                    # The canvas derives this from the frozen revision rather
                    # than maintaining a second handwritten Agent node list.
                    "node_definition": {
                        "type_id": f"agent.invoke.{rev.revision_id}",
                        "revision_id": str(rev.revision_id),
                        "input_schema_ref": (rev.body or {}).get("input_schema_ref", ""),
                        "output_schema_ref": (rev.body or {}).get("output_schema_ref", ""),
                        "executor_ref": "agent_invoke",
                        "input_ports": [_agent_port(str((rev.body or {}).get("input_schema_ref", "toonflow.agent_input.v1")), "input", required=True)],
                        "output_ports": [_agent_port(str((rev.body or {}).get("output_schema_ref", "toonflow.agent_output.v1")), "output", required=True)],
                        "config": {"agent_revision_id": str(rev.revision_id)},
                    },
                }
            )
    return {"agents": out, "count": len(out), "provider_configured": provider_configured}


@router.post("/validate")
async def validate_agent_body(body: AgentValidateRequest) -> dict:
    """Static validation of an Agent body without persisting."""
    try:
        _agent.validate(body.body)
        return {"valid": True}
    except ValidationError_ as e:
        raise HTTPException(status_code=e.status_code, detail=e.to_dict())


@router.post("/dry-run")
async def dry_run_agent(body: AgentDryRunRequest) -> DryRunResponse:
    """Validate and return structural info without persisting."""
    result = _agent.dry_run(body.body)
    return DryRunResponse(valid=result["valid"], step_count=result["step_count"])


@router.post("/invoke/prepare")
async def prepare_agent_invoke(body: PrepareAgentInvokeRequest, authorization: str | None = Header(None)) -> dict:
    """Validate a frozen invocation without fabricating provider output.

    The worker receives the returned immutable contract and is responsible for
    its persistent attempt/outbox dispatch.  A missing AtlasCloud binding is a
    deliberate blocked result, never a successful generated artifact.
    """
    try:
        revision = _agent._repo.get_revision(body.agent_revision_id)
        definition = _agent._repo.get_definition_for_revision(body.agent_revision_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_dict())
    if revision.revision_status.value != "active":
        raise HTTPException(status_code=409, detail="AgentInvoke requires an active frozen revision")
    owner_scope = _request_input_owner(authorization)
    if definition.owner_scope != owner_scope:
        raise HTTPException(status_code=403, detail={"code": "AGENT_OWNER_SCOPE_MISMATCH"})
    from src.domain.agent.agent_compiler import compile_agent
    plan = compile_agent(revision.model_dump(mode="json", exclude={"revision_id", "revision_number", "content_hash", "revision_status", "agent_kind"}))
    # Fixed input refs are revalidated at prepare time and again by the
    # execution worker.  A client-provided owner field is never authority.
    try:
        owner_kind, owner_id = owner_scope.split(":", 1)
        owner = OwnerScope(kind=owner_kind, id=UUID(owner_id))
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail={"code": "INVALID_OWNER_SCOPE"})
    try:
        input_resource_refs = _invocations.validate_typed_input_refs(body.typed_inputs, owner)
        assembly_plan = None
        if revision.skill_revision_refs:
            assembly_plan = SqlSkillRepository(_agent._factory).assemble(
                agent_revision_id=revision.revision_id,
                skill_ids=revision.skill_revision_refs,
                token_budget=int(revision.execution_policy.get("max_skill_tokens", 4096)),
                owner_scope=owner_scope,
            )
    except (NotFoundError, ValidationError_, ForbiddenError) as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_dict()) from exc
    return {
        "state": "prepared",
        "agent_revision_id": str(revision.revision_id),
        "plan_hash": plan["plan_hash"],
        "output_schema_ref": revision.output_schema_ref,
        "provider_ref": plan["provider_ref"],
        "provider_dispatch": "runtime_required",
        "artifact_outputs": "created_only_after_verified_provider_result",
        "skill_assembly_plan_id": str(assembly_plan.plan_id) if assembly_plan else None,
        "skill_assembly_fingerprint": assembly_plan.final_context_hash if assembly_plan else None,
        "input_resource_refs": input_resource_refs,
    }


@router.post("/invoke/execute")
async def execute_agent_invoke(body: ExecuteAgentInvokeRequest, authorization: str | None = Header(None)) -> dict:
    """Dispatch a pinned AgentRevision through Runtime then AtlasCloud."""
    try:
        kind, owner_id = _request_input_owner(authorization).split(":", 1)
        return _invocations.execute(agent_revision_id=body.agent_revision_id, owner_scope=OwnerScope(kind=kind, id=UUID(owner_id)),
            node_run_attempt_id=body.node_run_attempt_id, typed_inputs=body.typed_inputs, idempotency_key=body.idempotency_key)
    except (ValueError, ForbiddenError, PolicyBlockedError, ValidationError_, NotFoundError) as exc:
        status = exc.status_code if hasattr(exc, "status_code") else 422
        detail = exc.to_dict() if hasattr(exc, "to_dict") else {"code": "INVALID_OWNER_SCOPE"}
        raise HTTPException(status_code=status, detail=detail) from exc


@router.post("/orchestrations/validate")
async def validate_orchestration(body: ValidateOrchestrationRequest) -> dict:
    from src.domain.agent.orchestration import MultiAgentOrchestrator
    try:
        return MultiAgentOrchestrator().validate_graph(body.graph)
    except ValidationError_ as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_dict())


@router.get("/{agent_id}/revisions/{revision_id}/diff/{other_revision_id}")
async def diff_agent_revisions(agent_id: UUID, revision_id: UUID, other_revision_id: UUID, authorization: str | None = Header(None)) -> dict:
    """Return a field-level diff; callers must explicitly choose upgrades."""
    try:
        _require_agent_owner(agent_id, authorization)
        _agent._repo.ensure_revision_belongs_to(agent_id, revision_id)
        _agent._repo.ensure_revision_belongs_to(agent_id, other_revision_id)
        return _agent._repo.diff_revisions(revision_id, other_revision_id)
    except (NotFoundError, ForbiddenError) as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_dict())


@router.get("/{agent_id}/revisions/{revision_id}/usage-index")
async def agent_revision_usage_index(agent_id: UUID, revision_id: UUID,
                                     authorization: str | None = Header(None)) -> dict:
    try:
        _require_agent_owner(agent_id, authorization)
        _agent._repo.ensure_revision_belongs_to(agent_id, revision_id)
        return _agent._repo.usage_index(revision_id)
    except (NotFoundError, ForbiddenError) as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_dict())


@router.post("/{agent_id}/revisions/{revision_id}/request-input", status_code=201)
async def create_request_input(agent_id: UUID, revision_id: UUID, body: CreateRequestInputRequest, authorization: str | None = Header(None)) -> dict:
    try:
        _require_agent_owner(agent_id, authorization)
        _agent._repo.ensure_revision_belongs_to(agent_id, revision_id)
        task = _request_input.create(agent_revision_id=revision_id, requester_scope=_request_input_owner(authorization), **body.model_dump())
    except (NotFoundError, ConflictError, ValidationError_, ForbiddenError) as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_dict())
    return {"task_id": str(task.task_id), "status": task.status.value, "task_version": task.task_version, "schema_ref": task.schema_ref}


@router.post("/request-input/{task_id}/resolve")
async def resolve_request_input(task_id: UUID, body: ResolveRequestInputRequest, authorization: str | None = Header(None)) -> dict:
    try:
        task = _request_input.resolve(task_id=task_id, requester_scope=_request_input_owner(authorization), **body.model_dump())
    except (NotFoundError, ConflictError, ValidationError_) as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_dict())
    return {"task_id": str(task.task_id), "status": task.status.value, "task_version": task.task_version}
