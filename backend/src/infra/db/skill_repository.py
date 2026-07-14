"""TF-ASR-001: PostgreSQL-backed Skill repository.

Draft/Revision lifecycle with CAS base_hash enforcement.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from src.core.exceptions import ConflictError, ForbiddenError, NotFoundError, ValidationError_
from src.infra.db.models import (
    AgentDefinitionModel, AgentRevisionModel, ArtifactVersionModel,
    ResourceDraftModel, ResourceModel, ResourceRevisionModel,
    SkillAssemblyPlanModel, SkillContentModel, SkillRevisionModel,
    SkillPolicyStateModel, SkillPackageEmbedModel,
)
from src.schemas.enums import RevisionStatus
from src.infra.db.session import get_session_factory
from src.schemas.models import OwnerScope, ResourceRef, SkillAssemblyPlan, SkillContent


def _compute_hash(body: dict) -> str:
    raw = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def _skill_row_to_schema(row: SkillContentModel) -> SkillContent:
    body = dict(row.body or {})
    return SkillContent.model_validate(body)


def _plan_row_to_schema(row: SkillAssemblyPlanModel) -> SkillAssemblyPlan:
    body = dict(row.body or {})
    body["plan_id"] = str(row.plan_id)
    body["agent_revision_id"] = str(row.agent_revision_id)
    return SkillAssemblyPlan.model_validate(body)


class SqlSkillRepository:
    """Persistent Skill Content + Assembly Plan storage with CAS."""

    def __init__(self, factory: sessionmaker[Session] | None = None) -> None:
        self._factory = factory or get_session_factory()

    # -- Skill Content (returns ORM model for definition-level ops) --

    def create_skill(
        self, *, name: str, description: str, owner_scope: str, body: dict | None = None
    ) -> SkillContentModel:
        if not name:
            raise ValidationError_(message="Skill requires a name")
        body = body or {}
        content_hash = _compute_hash(body)
        with self._factory.begin() as session:
            skill_id = uuid4()
            artifact = self._append_skill_artifact(session, skill_id=skill_id, owner_scope=owner_scope, body=body)
            row = SkillContentModel(
                skill_id=skill_id,
                name=name,
                description=description,
                owner_scope=owner_scope,
                body=body,
                content_hash=content_hash,
                status="draft",
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
            session.add(row)
            # Skill identity is also the canonical Resource identity.  This
            # avoids a second mutable namespace and makes every frozen Skill
            # revision addressable as a fixed ResourceRevision.
            session.add(ResourceModel(resource_id=skill_id, resource_type="skill", owner_scope=owner_scope, created_at=datetime.now(timezone.utc)))
            session.flush()
            session.add(ResourceDraftModel(resource_id=skill_id, draft_version=1, base_revision_id=None,
                content_artifact_version_id=artifact.artifact_version_id, updated_at=datetime.now(timezone.utc)))
            session.flush()
            return row

    def get_skill(self, skill_id: UUID) -> SkillContentModel:
        with self._factory() as session:
            row = session.get(SkillContentModel, skill_id)
            if row is None:
                raise NotFoundError("SkillContent", str(skill_id))
            return row

    def list_skills(self, *, owner_scope: str | None = None) -> list[SkillContentModel]:
        stmt = select(SkillContentModel).order_by(SkillContentModel.created_at.desc())
        if owner_scope is not None:
            stmt = stmt.where(SkillContentModel.owner_scope == owner_scope)
        with self._factory() as session:
            return list(session.scalars(stmt))

    def update_skill(
        self, skill_id: UUID, *, body: dict, base_hash: str | None = None
    ) -> SkillContentModel:
        """Update skill content with CAS base_hash check."""
        content_hash = _compute_hash(body)
        with self._factory.begin() as session:
            row = session.get(SkillContentModel, skill_id)
            if row is None:
                raise NotFoundError("SkillContent", str(skill_id))
            if row.status != "draft":
                raise ConflictError("Only a SkillDraft may be edited; create a new revision instead")
            if base_hash is not None and row.content_hash != base_hash:
                raise ConflictError(
                    message="CAS conflict: base_hash does not match current content_hash"
                )
            row.body = body
            row.content_hash = content_hash
            row.base_hash = base_hash
            row.updated_at = datetime.now(timezone.utc)
            resource_draft = session.get(ResourceDraftModel, skill_id)
            if resource_draft is None:
                # Upgrade path for early Skill rows created before skills were
                # Resource-backed.  Keep the identity stable and never guess a
                # latest revision.
                if session.get(ResourceModel, skill_id) is None:
                    session.add(ResourceModel(resource_id=skill_id, resource_type="skill", owner_scope=row.owner_scope, created_at=datetime.now(timezone.utc)))
                    session.flush()
                artifact = self._append_skill_artifact(session, skill_id=skill_id, owner_scope=row.owner_scope, body=body)
                session.add(ResourceDraftModel(resource_id=skill_id, draft_version=1, base_revision_id=None, content_artifact_version_id=artifact.artifact_version_id, updated_at=datetime.now(timezone.utc)))
            else:
                artifact = self._append_skill_artifact(session, skill_id=skill_id, owner_scope=row.owner_scope, body=body)
                resource_draft.content_artifact_version_id = artifact.artifact_version_id
                resource_draft.draft_version += 1
                resource_draft.updated_at = datetime.now(timezone.utc)
            session.flush()
            return row

    def activate_skill(self, skill_id: UUID) -> SkillContentModel:
        with self._factory.begin() as session:
            row = session.get(SkillContentModel, skill_id)
            if row is None:
                raise NotFoundError("SkillContent", str(skill_id))
            if row.status == "retired":
                raise ConflictError("Retired SkillRevision cannot be activated")
            row.status = "active"
            row.updated_at = datetime.now(timezone.utc)
            session.flush()
            return row

    def retire_skill(self, skill_id: UUID) -> SkillContentModel:
        with self._factory.begin() as session:
            row = session.get(SkillContentModel, skill_id)
            if row is None:
                raise NotFoundError("SkillContent", str(skill_id))
            row.status = "retired"
            row.updated_at = datetime.now(timezone.utc)
            session.flush()
            return row

    def delete_skill(self, skill_id: UUID) -> None:
        with self._factory.begin() as session:
            row = session.get(SkillContentModel, skill_id)
            if row is None:
                raise NotFoundError("SkillContent", str(skill_id))
            session.query(SkillAssemblyPlanModel).filter(
                SkillAssemblyPlanModel.skill_id == skill_id
            ).delete()
            session.delete(row)
            session.flush()

    def get_skill_content_schema(self, skill_id: UUID) -> SkillContent:
        """Get the SkillContent Pydantic schema from body."""
        row = self.get_skill(skill_id)
        return _skill_row_to_schema(row)

    def submit_revision(self, skill_id: UUID, *, base_hash: str) -> SkillRevisionModel:
        """Freeze the current SkillDraft into an immutable revision."""
        with self._factory.begin() as session:
            draft = session.get(SkillContentModel, skill_id)
            if draft is None:
                raise NotFoundError("SkillDraft", str(skill_id))
            if draft.status != "draft" or draft.content_hash != base_hash:
                raise ConflictError("SkillDraft CAS conflict")
            latest = session.scalar(select(SkillRevisionModel).where(SkillRevisionModel.skill_id == skill_id).order_by(SkillRevisionModel.revision_number.desc()).limit(1))
            row = SkillRevisionModel(revision_id=uuid4(), skill_id=skill_id,
                revision_number=(latest.revision_number + 1) if latest else 1,
                body=dict(draft.body or {}), content_hash=draft.content_hash, status="active", created_at=datetime.now(timezone.utc))
            session.add(row)
            resource_draft = session.get(ResourceDraftModel, skill_id)
            if resource_draft is None:
                # A legacy draft may be submitted without an intervening edit.
                if session.get(ResourceModel, skill_id) is None:
                    session.add(ResourceModel(resource_id=skill_id, resource_type="skill", owner_scope=draft.owner_scope, created_at=datetime.now(timezone.utc)))
                    session.flush()
                artifact = self._append_skill_artifact(session, skill_id=skill_id, owner_scope=draft.owner_scope, body=dict(draft.body or {}))
                resource_draft = ResourceDraftModel(resource_id=skill_id, draft_version=1, base_revision_id=None, content_artifact_version_id=artifact.artifact_version_id, updated_at=datetime.now(timezone.utc))
                session.add(resource_draft)
                session.flush()
            session.execute(update(ResourceRevisionModel).where(
                ResourceRevisionModel.resource_id == skill_id,
                ResourceRevisionModel.revision_status == RevisionStatus.ACTIVE,
            ).values(revision_status=RevisionStatus.RETIRED))
            session.add(ResourceRevisionModel(revision_id=row.revision_id, resource_id=skill_id,
                revision_number=row.revision_number, content_artifact_version_id=resource_draft.content_artifact_version_id,
                revision_status=RevisionStatus.ACTIVE, created_from_artifact_version_id=None, created_at=datetime.now(timezone.utc)))
            resource_draft.base_revision_id = row.revision_id
            session.flush()
            return row

    def get_revision(self, revision_id: UUID) -> SkillRevisionModel:
        with self._factory() as session:
            row = session.get(SkillRevisionModel, revision_id)
            if row is None:
                raise NotFoundError("SkillRevision", str(revision_id))
            return row

    def retire_revision(self, revision_id: UUID) -> SkillRevisionModel:
        with self._factory.begin() as session:
            row = session.get(SkillRevisionModel, revision_id)
            if row is None:
                raise NotFoundError("SkillRevision", str(revision_id))
            row.status = "retired"
            resource_revision = session.get(ResourceRevisionModel, revision_id)
            if resource_revision is not None:
                resource_revision.revision_status = RevisionStatus.RETIRED
            return row

    def set_policy_state(self, revision_id: UUID, *, state: str, reason: str) -> None:
        if state not in {"active", "suspended"}:
            raise ValidationError_("Skill policy state is invalid")
        with self._factory.begin() as session:
            if session.get(SkillRevisionModel, revision_id) is None:
                raise NotFoundError("SkillRevision", str(revision_id))
            row = session.get(SkillPolicyStateModel, revision_id)
            if row is None:
                row = SkillPolicyStateModel(revision_id=revision_id, state=state, reason=reason, updated_at=datetime.now(timezone.utc))
                session.add(row)
            else:
                row.state, row.reason, row.updated_at = state, reason, datetime.now(timezone.utc)

    def install_package_embed(self, *, skill_revision_id: UUID, ref: ResourceRef, installer: OwnerScope) -> UUID:
        from src.infra.db.resource_repository import SqlResourceRepository
        SqlResourceRepository(self._factory).resolve_ref(ref.resource_id, ref.revision_id, installer,
            ref.grant_snapshot_id, required_actions={"redistribute", "reference", "execute"})
        with self._factory.begin() as session:
            revision = session.get(SkillRevisionModel, skill_revision_id)
            if revision is None or revision.skill_id != ref.resource_id or revision.status != "active":
                raise ValidationError_("Skill package embed must pin an active matching SkillRevision")
            row = SkillPackageEmbedModel(embed_id=uuid4(), skill_revision_id=skill_revision_id,
                installer_scope=installer.scoped_id, resource_id=ref.resource_id, grant_snapshot_id=ref.grant_snapshot_id,
                created_at=datetime.now(timezone.utc))
            session.add(row)
            return row.embed_id

    @staticmethod
    def _append_skill_artifact(session: Session, *, skill_id: UUID, owner_scope: str, body: dict) -> ArtifactVersionModel:
        content = dict(body or {})
        content_hash = _compute_hash(content)
        artifact = ArtifactVersionModel(artifact_version_id=uuid4(), artifact_id=skill_id,
            schema_id="toonflow.skill_content", schema_version=1, owner_scope=owner_scope,
            content_json=content, content_hash=content_hash, metadata_json={"resource_type": "skill"},
            created_at=datetime.now(timezone.utc))
        session.add(artifact)
        session.flush()
        return artifact

    # -- Assembly Plans --

    def create_plan(
        self, skill_id: UUID, agent_revision_id: UUID, body: dict
    ) -> SkillAssemblyPlan:
        content_hash = _compute_hash(body)
        with self._factory.begin() as session:
            row = SkillAssemblyPlanModel(
                plan_id=uuid4(),
                skill_id=skill_id,
                agent_revision_id=agent_revision_id,
                body=body,
                content_hash=content_hash,
                created_at=datetime.now(timezone.utc),
            )
            session.add(row)
            session.flush()
            return _plan_row_to_schema(row)

    def get_plan(self, plan_id: UUID) -> SkillAssemblyPlan:
        with self._factory() as session:
            row = session.get(SkillAssemblyPlanModel, plan_id)
            if row is None:
                raise NotFoundError("SkillAssemblyPlan", str(plan_id))
            return _plan_row_to_schema(row)

    def list_plans(self, skill_id: UUID) -> list[SkillAssemblyPlan]:
        stmt = (
            select(SkillAssemblyPlanModel)
            .where(SkillAssemblyPlanModel.skill_id == skill_id)
            .order_by(SkillAssemblyPlanModel.created_at.desc())
        )
        with self._factory() as session:
            return [_plan_row_to_schema(r) for r in session.scalars(stmt)]

    def assemble(self, *, agent_revision_id: UUID, skill_ids: list[UUID | ResourceRef | dict], token_budget: int, owner_scope: str | None = None) -> SkillAssemblyPlan:
        """Resolve frozen skills into an auditable, deterministic assembly plan.

        A requested owner is authoritative: cross-owner references are not a
        convenience feature.  They require a ResourceRef/grant integration
        before reaching this assembler, so raw SkillRevision IDs fail closed.
        Optional skills can be excluded with an explicit reason; required
        skills make the whole compilation fail rather than silently changing
        an Agent's contract.
        """
        if not skill_ids:
            raise ValidationError_("Skill assembly requires at least one SkillRevision")
        normalized: list[tuple[UUID, ResourceRef | None]] = []
        for raw in skill_ids:
            if isinstance(raw, ResourceRef):
                normalized.append((raw.revision_id, raw))
            elif isinstance(raw, dict):
                try:
                    ref = ResourceRef.model_validate(raw)
                except Exception as exc:
                    raise ValidationError_("Skill ResourceRef is invalid") from exc
                normalized.append((ref.revision_id, ref))
            else:
                normalized.append((UUID(str(raw)), None))
        revision_ids = [item[0] for item in normalized]
        if len(set(revision_ids)) != len(revision_ids):
            raise ValidationError_("Skill assembly refs must be unique")
        with self._factory.begin() as session:
            agent_revision = session.get(AgentRevisionModel, agent_revision_id)
            agent_definition = session.get(AgentDefinitionModel, agent_revision.agent_id) if agent_revision else None
            if agent_definition is None:
                raise NotFoundError("AgentRevision", str(agent_revision_id))
            if owner_scope is not None and agent_definition.owner_scope != owner_scope:
                raise ForbiddenError("AgentRevision belongs to a different owner_scope")
            revisions = [session.get(SkillRevisionModel, skill_id) for skill_id in revision_ids]
            rows = [session.get(SkillContentModel, revision.skill_id) if revision else None for revision in revisions]
            if any(row is None for row in rows):
                raise NotFoundError("SkillRevision", "unknown")
            if any(revision is None or revision.status != "active" for revision in revisions):
                raise ValidationError_("Only active frozen SkillRevisions may be assembled")
            for revision in revisions:
                assert revision is not None
                policy = session.get(SkillPolicyStateModel, revision.revision_id)
                if policy is not None and policy.state == "suspended":
                    raise ForbiddenError(f"SkillRevision is policy-suspended: {policy.reason}")
            frozen = [(revision, row) for revision, row in zip(revisions, rows) if revision is not None and row is not None]
            grants_by_revision = {revision_id: ref for revision_id, ref in normalized if ref is not None}
            for revision, row in frozen:
                if row.owner_scope != agent_definition.owner_scope:
                    ref = grants_by_revision.get(revision.revision_id)
                    if ref is None or ref.resource_id != revision.skill_id or ref.resource_type != "skill":
                        raise ForbiddenError(
                            "Cross-owner SkillRevision requires a granted fixed Skill ResourceRef; raw revision IDs are forbidden"
                        )
                    try:
                        kind, raw_owner_id = agent_definition.owner_scope.split(":", 1)
                        requester = OwnerScope(kind=kind, id=UUID(raw_owner_id))
                        from src.infra.db.resource_repository import SqlResourceRepository
                        SqlResourceRepository(self._factory).resolve_ref(
                            ref.resource_id, ref.revision_id, requester, ref.grant_snapshot_id,
                            required_actions={"reference", "execute"},
                        )
                    except (ValueError, NotFoundError) as exc:
                        raise ForbiddenError("Skill ResourceRef grant is unavailable or revoked") from exc
                # Knowledge must be pinned and currently accessible. Inline
                # ArtifactRefs are owner-only; external ResourceRefs repeat
                # the fixed-grant check instead of trusting draft metadata.
                for raw_ref in (revision.body or {}).get("knowledge_refs", []):
                    if not isinstance(raw_ref, dict):
                        raise ValidationError_("Skill knowledge ref is malformed")
                    if raw_ref.get("artifact_version_id"):
                        artifact = session.get(ArtifactVersionModel, UUID(str(raw_ref["artifact_version_id"])))
                        if artifact is None or artifact.owner_scope != agent_definition.owner_scope:
                            raise ForbiddenError("Skill knowledge ArtifactRef is unavailable to Agent owner")
                    elif raw_ref.get("resource_id") and raw_ref.get("revision_id"):
                        ref = ResourceRef.model_validate(raw_ref)
                        kind, raw_owner_id = agent_definition.owner_scope.split(":", 1)
                        from src.infra.db.resource_repository import SqlResourceRepository
                        SqlResourceRepository(self._factory).resolve_ref(ref.resource_id, ref.revision_id,
                            OwnerScope(kind=kind, id=UUID(raw_owner_id)), ref.grant_snapshot_id, required_actions={"reference"})
                    else:
                        raise ValidationError_("Skill knowledge ref must pin ArtifactVersion or ResourceRevision")
            from src.domain.skill.skill_service import compile_skill
            compiled = [(revision, row, compile_skill(dict(revision.body or {}))) for revision, row in frozen]
            # Explicit priority then immutable creation time then UUID is stable.
            tier_order = {"platform": 0, "managed": 1, "step": 2, "explicit": 3}
            compiled.sort(key=lambda item: (tier_order.get(str((item[0].body or {}).get("assembly_tier", "explicit")), 3), int((item[0].body or {}).get("priority", 100)), item[0].created_at, str(item[0].revision_id)))
            seen_tags: dict[str, tuple[UUID, bool]] = {}
            seen_roles: dict[str, UUID] = {}
            seen_schemas: dict[str, UUID] = {}
            conflicts: list[str] = []
            rejected: list[dict[str, str]] = []
            sections: list[dict] = []
            included: list[UUID] = []
            total = 0
            for revision, row, result in compiled:
                body = revision.body or {}
                tags = [str(tag) for tag in body.get("conflict_tags", [])]
                required = bool(body.get("assembly_policy", {}).get("required", True))
                skill_conflicts: list[str] = []
                for tag in tags:
                    normalized = tag.lstrip("!")
                    negative = tag.startswith("!")
                    previous = seen_tags.get(normalized)
                    if previous is not None and previous[1] != negative:
                        skill_conflicts.append(f"{revision.revision_id} conflicts with {previous[0]} on {normalized}")
                    elif previous is not None:
                        skill_conflicts.append(f"duplicate conflict tag {normalized}: {revision.revision_id} and {previous[0]}")
                    else:
                        seen_tags[normalized] = (revision.revision_id, negative)
                for role in body.get("applicable_agent_roles", []):
                    previous = seen_roles.get(str(role))
                    if previous is not None:
                        skill_conflicts.append(f"{revision.revision_id} conflicts with {previous} on role:{role}")
                    else:
                        seen_roles[str(role)] = revision.revision_id
                for key in ("output_schema_ref", "required_context_schema"):
                    value = str(body.get(key, ""))
                    if not value:
                        continue
                    previous = seen_schemas.get(f"{key}:{value}")
                    if previous is not None:
                        skill_conflicts.append(f"{revision.revision_id} conflicts with {previous} on {key}:{value}")
                    else:
                        seen_schemas[f"{key}:{value}"] = revision.revision_id
                if body.get("requires_unavailable_data"):
                    skill_conflicts.append(f"{revision.revision_id} requests unauthorized data")
                if skill_conflicts:
                    if required:
                        conflicts.extend(skill_conflicts)
                    else:
                        rejected.append({"skill_revision_id": str(revision.revision_id), "reason": "; ".join(skill_conflicts)})
                    continue
                cost = int(result["token_accounting"]["total_estimated_tokens"])
                if total + cost > token_budget:
                    if required:
                        conflicts.append(f"required Skill {revision.revision_id} exceeds token budget")
                    else:
                        rejected.append({"skill_revision_id": str(revision.revision_id), "reason": f"optional Skill exceeds remaining token budget ({token_budget - total})"})
                    continue
                total += cost
                included.append(revision.revision_id)
                sections.extend(result["resolved_sections"])
            if conflicts:
                raise ValidationError_("Skill assembly blocked", details={"code": "SKILL_ASSEMBLY_CONFLICT", "conflicts": conflicts})
            resolved_resource_refs = []
            refs_by_revision = {revision_id: ref for revision_id, ref in normalized}
            for revision, row in frozen:
                ref = refs_by_revision.get(revision.revision_id)
                resolved_resource_refs.append((ref or ResourceRef(
                    resource_id=row.skill_id, resource_type="skill", revision_id=revision.revision_id,
                )).model_dump(mode="json"))
            body = {
                "skill_refs": [str(value) for value in included], "resolved_sections": sections,
                "resolved_resource_refs": resolved_resource_refs,
                "token_accounting": {"total_estimated_tokens": total, "max_tokens": token_budget},
                "conflicts": [], "rejected_skills": rejected,
                "security_decisions": ["deterministic_order", "frozen_skill_revisions", "owner_scope_checked", "resource_grant_checked"],
            }
            body["final_context_hash"] = _compute_hash(body)
            plan = SkillAssemblyPlanModel(plan_id=uuid4(), skill_id=next(row.skill_id for revision, row in frozen if revision.revision_id == (included[0] if included else frozen[0][0].revision_id)), agent_revision_id=agent_revision_id,
                body=body, content_hash=_compute_hash(body), created_at=datetime.now(timezone.utc))
            session.add(plan)
            session.flush()
            return _plan_row_to_schema(plan)


class SqlSkillService:
    """Higher-level Skill service with validation."""

    def __init__(self, factory: sessionmaker[Session] | None = None) -> None:
        self._repo = SqlSkillRepository(factory)
        self._factory = factory or get_session_factory()

    def validate(self, body: dict) -> None:
        from src.domain.skill.skill_service import validate_skill
        validate_skill(body)

    def prepare(self, body: dict) -> dict:
        """Validate and prepare body for storage."""
        self.validate(body)
        prepared = dict(body)
        prepared.setdefault("instructions", [])
        prepared.setdefault("examples", [])
        prepared.setdefault("knowledge_refs", [])
        return prepared

    def dry_run(self, body: dict) -> dict:
        """Validate without persisting."""
        from src.domain.skill.skill_service import compile_skill
        return compile_skill(body)
