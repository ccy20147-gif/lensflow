"""TF-ASR-001: Skill API Routes — PostgreSQL-backed.

Endpoints for Skill content CRUD, assembly plans, validation,
and dry-run compilation.
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from src.core.exceptions import ConflictError, ForbiddenError, NotFoundError, ValidationError_
from src.infra.db.skill_repository import SqlSkillService
from src.schemas.models import ResourceRef, SkillContent, SkillAssemblyPlan
from src.api.auth import require_owner

_skill = SqlSkillService()

router = APIRouter(prefix="/api/v1/skills", tags=["skills"])


def _require_skill_owner(skill_id: UUID, authorization: str | None) -> str:
    owner_scope = require_owner(authorization)[1].scoped_id
    skill = _skill._repo.get_skill(skill_id)
    if skill.owner_scope != owner_scope:
        raise ForbiddenError("Skill belongs to a different owner_scope")
    return owner_scope


# -- Request / Response models --


class CreateSkillRequest(BaseModel):
    name: str
    description: str = ""
    body: dict | None = None


class UpdateSkillRequest(BaseModel):
    body: dict
    base_hash: str | None = None


class SubmitSkillRevisionRequest(BaseModel):
    base_hash: str


class CreatePlanRequest(BaseModel):
    agent_revision_id: UUID
    body: dict


class SkillValidateRequest(BaseModel):
    body: dict


class SkillDryRunRequest(BaseModel):
    body: dict


class AssembleSkillsRequest(BaseModel):
    agent_revision_id: UUID
    # Same-owner callers may use frozen revision IDs.  Cross-owner usage is
    # intentionally possible only through a grant-bearing fixed ResourceRef.
    skill_revision_ids: list[UUID | ResourceRef]
    token_budget: int = 4096


class SkillDryRunResponse(BaseModel):
    valid: bool
    resolved_sections: list[dict] = []
    token_accounting: dict[str, int] = {}
    conflicts: list[str] = []
    security_decisions: list[str] = []
    final_context_hash: str = ""

class SkillPolicyRequest(BaseModel):
    reason: str = ""

class SkillPackageEmbedRequest(BaseModel):
    resource_ref: ResourceRef


def _skill_orm_to_dict(row) -> dict:
    return {
        "skill_id": str(row.skill_id),
        "name": row.name,
        "description": row.description,
        "owner_scope": row.owner_scope,
        "body": row.body,
        "content_hash": row.content_hash,
        "status": row.status,
        "created_at": row.created_at.isoformat() if row.created_at else "",
        "updated_at": row.updated_at.isoformat() if row.updated_at else "",
    }


def _skill_revision_to_dict(row) -> dict:
    return {
        "revision_id": str(row.revision_id),
        "skill_id": str(row.skill_id),
        "revision_number": row.revision_number,
        "body": row.body,
        "content_hash": row.content_hash,
        "status": row.status,
        "created_at": row.created_at.isoformat() if row.created_at else "",
    }


# -- Skill Content endpoints --


@router.post("")
async def create_skill(body: CreateSkillRequest, authorization: str | None = Header(None)) -> dict:
    """Create a new Skill content entry."""
    try:
        _skill.validate(body.body or {})
    except ValidationError_ as e:
        raise HTTPException(status_code=e.status_code, detail=e.to_dict())
    try:
        row = _skill._repo.create_skill(
            name=body.name,
            description=body.description,
            owner_scope=require_owner(authorization)[1].scoped_id,
            body=body.body,
        )
    except ValidationError_ as e:
        raise HTTPException(status_code=e.status_code, detail=e.to_dict())
    return _skill_orm_to_dict(row)


@router.get("/{skill_id}")
async def get_skill(skill_id: UUID, authorization: str | None = Header(None)) -> dict:
    """Get a Skill content by ID."""
    try:
        _require_skill_owner(skill_id, authorization)
        row = _skill._repo.get_skill(skill_id)
    except (NotFoundError, ForbiddenError) as e:
        raise HTTPException(status_code=e.status_code, detail=e.to_dict())
    return _skill_orm_to_dict(row)


@router.get("")
async def list_skills(authorization: str | None = Header(None)) -> list[dict]:
    """List all Skills, optionally filtered by owner_scope."""
    rows = _skill._repo.list_skills(owner_scope=require_owner(authorization)[1].scoped_id)
    return [_skill_orm_to_dict(r) for r in rows]


@router.get("/published")
async def list_published_skills() -> dict:
    """List Skills with at least one content row.

    The canvas palette uses this to surface runnable Skills as node
    candidates. Provider availability is evaluated at request time and no
    credential is ever returned.
    """
    from src.domain.provider.atlascloud import AtlasCloudAdapter
    from src.infra.db.models import SkillContentModel
    from sqlalchemy import select
    provider_configured = AtlasCloudAdapter().configured
    with _skill._factory() as session:
        skills = _skill._repo.list_skills()
        out = []
        for s in skills:
            content = session.scalar(
                select(SkillContentModel)
                .where(SkillContentModel.skill_id == s.skill_id)
                .order_by(SkillContentModel.created_at.desc())
                .limit(1)
            )
            if content is None:
                continue
            out.append({
                "skill_id": str(s.skill_id),
                "name": s.name,
                "description": s.description,
                "owner_scope": s.owner_scope,
                "skill_revision_id": str(content.skill_id),
                "content_hash": content.content_hash,
                "status": content.status,
                "provider_configured": provider_configured,
            })
    return {"skills": out, "count": len(out), "provider_configured": provider_configured}


@router.patch("/{skill_id}")
async def update_skill(skill_id: UUID, body: UpdateSkillRequest, authorization: str | None = Header(None)) -> dict:
    """Update Skill content with CAS base_hash check."""
    try:
        _skill.validate(body.body)
    except ValidationError_ as e:
        raise HTTPException(status_code=e.status_code, detail=e.to_dict())
    try:
        _require_skill_owner(skill_id, authorization)
        row = _skill._repo.update_skill(skill_id, body=body.body, base_hash=body.base_hash)
    except (NotFoundError, ConflictError, ForbiddenError) as e:
        raise HTTPException(status_code=e.status_code, detail=e.to_dict())
    return _skill_orm_to_dict(row)


@router.post("/{skill_id}/activate")
async def activate_skill(skill_id: UUID, authorization: str | None = Header(None)) -> dict:
    try:
        _require_skill_owner(skill_id, authorization)
        return _skill_orm_to_dict(_skill._repo.activate_skill(skill_id))
    except (NotFoundError, ConflictError, ForbiddenError) as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_dict())


@router.post("/{skill_id}/revisions", status_code=201)
async def submit_skill_revision(skill_id: UUID, body: SubmitSkillRevisionRequest, authorization: str | None = Header(None)) -> dict:
    try:
        _require_skill_owner(skill_id, authorization)
        row = _skill._repo.submit_revision(skill_id, base_hash=body.base_hash)
    except (NotFoundError, ConflictError, ForbiddenError) as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_dict())
    return _skill_revision_to_dict(row)


@router.get("/{skill_id}/revisions")
async def list_skill_revisions(skill_id: UUID, authorization: str | None = Header(None)) -> list[dict]:
    """List immutable revision history for an owned Skill."""
    try:
        _require_skill_owner(skill_id, authorization)
        return [_skill_revision_to_dict(row) for row in _skill._repo.list_revisions(skill_id)]
    except (NotFoundError, ForbiddenError) as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_dict())


@router.post("/{skill_id}/revisions/{revision_id}/retire")
async def retire_skill_revision(skill_id: UUID, revision_id: UUID, authorization: str | None = Header(None)) -> dict:
    """Retire a frozen revision while retaining it for audit and old runs."""
    try:
        _require_skill_owner(skill_id, authorization)
        revision = _skill._repo.get_revision(revision_id)
        if revision.skill_id != skill_id:
            raise NotFoundError("SkillRevision", str(revision_id))
        return _skill_revision_to_dict(_skill._repo.retire_revision(revision_id))
    except (NotFoundError, ForbiddenError) as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_dict())


@router.post("/{skill_id}/retire")
async def retire_skill(skill_id: UUID, authorization: str | None = Header(None)) -> dict:
    try:
        _require_skill_owner(skill_id, authorization)
        return _skill_orm_to_dict(_skill._repo.retire_skill(skill_id))
    except (NotFoundError, ConflictError, ForbiddenError) as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_dict())

@router.post("/{skill_id}/revisions/{revision_id}/suspend")
async def suspend_skill_revision(skill_id: UUID, revision_id: UUID, body: SkillPolicyRequest, authorization: str | None = Header(None)) -> dict:
    try:
        _require_skill_owner(skill_id, authorization)
        revision = _skill._repo.get_revision(revision_id)
        if revision.skill_id != skill_id:
            raise NotFoundError("SkillRevision", str(revision_id))
        _skill._repo.set_policy_state(revision_id, state="suspended", reason=body.reason)
        return {"revision_id": str(revision_id), "state": "suspended"}
    except (NotFoundError, ForbiddenError, ValidationError_) as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_dict())

@router.post("/{skill_id}/revisions/{revision_id}/package-embed", status_code=201)
async def install_skill_package_embed(skill_id: UUID, revision_id: UUID, body: SkillPackageEmbedRequest, authorization: str | None = Header(None)) -> dict:
    try:
        owner = require_owner(authorization)[1]
        if body.resource_ref.resource_id != skill_id or body.resource_ref.revision_id != revision_id:
            raise ValidationError_("Package embed ResourceRef does not match URL")
        embed_id = _skill._repo.install_package_embed(skill_revision_id=revision_id, ref=body.resource_ref, installer=owner)
        return {"embed_id": str(embed_id), "skill_revision_id": str(revision_id)}
    except (NotFoundError, ForbiddenError, ValidationError_) as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_dict())


@router.delete("/{skill_id}")
async def delete_skill(skill_id: UUID, authorization: str | None = Header(None)) -> dict:
    """Delete a Skill and all its assembly plans."""
    try:
        _require_skill_owner(skill_id, authorization)
        _skill._repo.delete_skill(skill_id)
    except (NotFoundError, ForbiddenError) as e:
        raise HTTPException(status_code=e.status_code, detail=e.to_dict())
    return {"status": "deleted"}


# -- Content schema (Pydantic) endpoint --


@router.get("/{skill_id}/content", response_model=SkillContent)
async def get_skill_content(skill_id: UUID, authorization: str | None = Header(None)) -> SkillContent:
    """Get the SkillContent Pydantic body from a skill."""
    try:
        _require_skill_owner(skill_id, authorization)
        return _skill._repo.get_skill_content_schema(skill_id)
    except (NotFoundError, ForbiddenError) as e:
        raise HTTPException(status_code=e.status_code, detail=e.to_dict())


# -- Assembly Plan endpoints --


@router.post("/{skill_id}/plans", response_model=SkillAssemblyPlan)
async def create_skill_plan(skill_id: UUID, body: CreatePlanRequest, authorization: str | None = Header(None)) -> SkillAssemblyPlan:
    """Create an assembly plan for a Skill."""
    try:
        _require_skill_owner(skill_id, authorization)
    except (NotFoundError, ForbiddenError) as e:
        raise HTTPException(status_code=e.status_code, detail=e.to_dict())
    try:
        return _skill._repo.create_plan(
            skill_id=skill_id,
            agent_revision_id=body.agent_revision_id,
            body=body.body,
        )
    except ValidationError_ as e:
        raise HTTPException(status_code=e.status_code, detail=e.to_dict())


@router.get("/{skill_id}/plans", response_model=list[SkillAssemblyPlan])
async def list_skill_plans(skill_id: UUID, authorization: str | None = Header(None)) -> list[SkillAssemblyPlan]:
    """List all assembly plans for a Skill."""
    try:
        _require_skill_owner(skill_id, authorization)
    except (NotFoundError, ForbiddenError) as e:
        raise HTTPException(status_code=e.status_code, detail=e.to_dict())
    return _skill._repo.list_plans(skill_id)


@router.get("/plans/{plan_id}", response_model=SkillAssemblyPlan)
async def get_skill_plan(plan_id: UUID) -> SkillAssemblyPlan:
    """Get a specific assembly plan."""
    try:
        return _skill._repo.get_plan(plan_id)
    except NotFoundError as e:
        raise HTTPException(status_code=e.status_code, detail=e.to_dict())


# -- Validation / Dry-run endpoints --


@router.post("/validate")
async def validate_skill_body(body: SkillValidateRequest) -> dict:
    """Static validation of a Skill body without persisting."""
    try:
        _skill.validate(body.body)
        return {"valid": True}
    except ValidationError_ as e:
        raise HTTPException(status_code=e.status_code, detail=e.to_dict())


@router.post("/dry-run")
async def dry_run_skill(body: SkillDryRunRequest) -> SkillDryRunResponse:
    """Validate and return structural info without persisting."""
    try:
        return SkillDryRunResponse(**_skill.dry_run(body.body))
    except ValidationError_ as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_dict())


@router.post("/assemble", response_model=SkillAssemblyPlan)
async def assemble_skills(body: AssembleSkillsRequest, authorization: str | None = Header(None)) -> SkillAssemblyPlan:
    """Compile deterministic non-executable Skill context for an Agent."""
    try:
        return _skill._repo.assemble(agent_revision_id=body.agent_revision_id, skill_ids=body.skill_revision_ids, token_budget=body.token_budget, owner_scope=require_owner(authorization)[1].scoped_id)
    except (NotFoundError, ValidationError_, ForbiddenError) as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_dict())
