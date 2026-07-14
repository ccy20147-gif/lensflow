"""TF-ASR-001: PostgreSQL-backed Media Recipe repository.

Draft/Revision lifecycle with CAS base_hash enforcement.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from src.core.exceptions import ConflictError, NotFoundError, ValidationError_
from src.domain.recipe.media_recipe_compiler import compile_media_recipe
from src.infra.db.models import MediaRecipeDefinitionModel, MediaRecipeRevisionModel
from src.infra.db.session import get_session_factory
from src.schemas.models import MediaRecipeRevision


def _compute_hash(body: dict) -> str:
    raw = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def _source_body_hash(body: dict) -> str:
    """Compatibility hash for existing draft CAS callers.

    The persisted revision includes a compiler-owned frozen plan.  CAS is a
    user-edit conflict check, so callers based on the pre-compiler body remain
    valid without allowing the frozen plan itself to be edited independently.
    """
    source = dict(body)
    source.pop("compiled_plan", None)
    source.pop("compiled_plan_hash", None)
    return _compute_hash(source)


def _revision_row_to_schema(row: MediaRecipeRevisionModel) -> MediaRecipeRevision:
    body = dict(row.body or {})
    body.update({
        "revision_id": str(row.revision_id),
        "recipe_id": str(row.recipe_id),
        "revision_number": row.revision_number,
        "content_hash": row.content_hash,
        "base_hash": row.base_hash,
        "revision_status": row.status,
        "created_at": row.created_at,
    })
    return MediaRecipeRevision.model_validate(body)


class SqlRecipeRepository:
    """Persistent Media Recipe Definition + Revision storage with CAS."""

    def __init__(self, factory: sessionmaker[Session] | None = None) -> None:
        self._factory = factory or get_session_factory()

    # -- Definitions --

    def create_definition(
        self, *, name: str, description: str, owner_scope: str, recipe_type: str = ""
    ) -> MediaRecipeDefinitionModel:
        if not name:
            raise ValidationError_(message="Media Recipe definition requires a name")
        with self._factory.begin() as session:
            row = MediaRecipeDefinitionModel(
                recipe_id=uuid4(),
                name=name,
                description=description,
                owner_scope=owner_scope,
                recipe_type=recipe_type,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
            session.add(row)
            session.flush()
            return row

    def get_definition(self, recipe_id: UUID) -> MediaRecipeDefinitionModel:
        with self._factory() as session:
            row = session.get(MediaRecipeDefinitionModel, recipe_id)
            if row is None:
                raise NotFoundError("MediaRecipeDefinition", str(recipe_id))
            return row

    def list_definitions(
        self, *, owner_scope: str | None = None, recipe_type: str | None = None
    ) -> list[MediaRecipeDefinitionModel]:
        stmt = select(MediaRecipeDefinitionModel).order_by(
            MediaRecipeDefinitionModel.created_at.desc()
        )
        if owner_scope is not None:
            stmt = stmt.where(MediaRecipeDefinitionModel.owner_scope == owner_scope)
        if recipe_type is not None:
            stmt = stmt.where(MediaRecipeDefinitionModel.recipe_type == recipe_type)
        with self._factory() as session:
            return list(session.scalars(stmt))

    def update_definition(
        self,
        recipe_id: UUID,
        *,
        name: str | None = None,
        description: str | None = None,
        recipe_type: str | None = None,
    ) -> MediaRecipeDefinitionModel:
        with self._factory.begin() as session:
            row = session.get(MediaRecipeDefinitionModel, recipe_id)
            if row is None:
                raise NotFoundError("MediaRecipeDefinition", str(recipe_id))
            if name is not None:
                row.name = name
            if description is not None:
                row.description = description
            if recipe_type is not None:
                row.recipe_type = recipe_type
            row.updated_at = datetime.now(timezone.utc)
            session.flush()
            return row

    def delete_definition(self, recipe_id: UUID) -> None:
        with self._factory.begin() as session:
            row = session.get(MediaRecipeDefinitionModel, recipe_id)
            if row is None:
                raise NotFoundError("MediaRecipeDefinition", str(recipe_id))
            session.query(MediaRecipeRevisionModel).filter(
                MediaRecipeRevisionModel.recipe_id == recipe_id
            ).delete()
            session.delete(row)
            session.flush()

    # -- Revisions --

    def create_revision(
        self, recipe_id: UUID, body: dict, *, base_hash: str | None = None
    ) -> MediaRecipeRevision:
        # Persist the exact compiled plan/capability snapshot with the revision;
        # later executions must never resolve an operator or model as "latest".
        compiled = compile_media_recipe(body)
        body = {**body, "compiled_plan": compiled["compiled_plan"], "compiled_plan_hash": compiled["plan_hash"]}
        content_hash = _compute_hash(body)
        with self._factory.begin() as session:
            latest = session.scalar(
                select(MediaRecipeRevisionModel)
                .where(MediaRecipeRevisionModel.recipe_id == recipe_id)
                .order_by(MediaRecipeRevisionModel.revision_number.desc())
                .limit(1)
            )
            next_number = (latest.revision_number + 1) if latest else 1

            if base_hash is not None:
                if latest is None:
                    raise ConflictError(
                        message="No existing revision to base on for CAS check"
                    )
                if latest.content_hash != base_hash and _source_body_hash(latest.body or {}) != base_hash:
                    raise ConflictError(
                        message="CAS conflict: base_hash does not match latest revision content_hash"
                    )

            row = MediaRecipeRevisionModel(
                revision_id=uuid4(),
                recipe_id=recipe_id,
                revision_number=next_number,
                body=body,
                content_hash=content_hash,
                base_hash=base_hash,
                status="draft",
                created_at=datetime.now(timezone.utc),
            )
            session.add(row)
            session.flush()
            return _revision_row_to_schema(row)

    def get_revision(self, revision_id: UUID) -> MediaRecipeRevision:
        with self._factory() as session:
            row = session.get(MediaRecipeRevisionModel, revision_id)
            if row is None:
                raise NotFoundError("MediaRecipeRevision", str(revision_id))
            return _revision_row_to_schema(row)

    def list_revisions(self, recipe_id: UUID) -> list[MediaRecipeRevision]:
        stmt = (
            select(MediaRecipeRevisionModel)
            .where(MediaRecipeRevisionModel.recipe_id == recipe_id)
            .order_by(MediaRecipeRevisionModel.revision_number.desc())
        )
        with self._factory() as session:
            return [_revision_row_to_schema(r) for r in session.scalars(stmt)]

    def promote_revision(self, revision_id: UUID) -> MediaRecipeRevision:
        with self._factory.begin() as session:
            row = session.get(MediaRecipeRevisionModel, revision_id)
            if row is None:
                raise NotFoundError("MediaRecipeRevision", str(revision_id))
            if row.status == "active":
                return _revision_row_to_schema(row)
            if row.status != "draft":
                raise ConflictError(
                    message=f"Cannot promote revision with status {row.status}"
                )
            row.status = "active"
            session.flush()
            return _revision_row_to_schema(row)

    def retire_revision(self, revision_id: UUID) -> MediaRecipeRevision:
        with self._factory.begin() as session:
            row = session.get(MediaRecipeRevisionModel, revision_id)
            if row is None:
                raise NotFoundError("MediaRecipeRevision", str(revision_id))
            row.status = "retired"
            session.flush()
            return _revision_row_to_schema(row)

    def diff_revisions(self, left_id: UUID, right_id: UUID) -> dict:
        """Return a deterministic public-contract diff between frozen revisions.

        Compiler artifacts are deliberately excluded: they are derived from the
        user-authored recipe contract and would obscure a meaningful review.
        """
        with self._factory() as session:
            left = session.get(MediaRecipeRevisionModel, left_id)
            right = session.get(MediaRecipeRevisionModel, right_id)
            if left is None:
                raise NotFoundError("MediaRecipeRevision", str(left_id))
            if right is None:
                raise NotFoundError("MediaRecipeRevision", str(right_id))
            if left.recipe_id != right.recipe_id:
                raise ConflictError("Cannot diff revisions from different MediaRecipes")

            fields = (
                "recipe_type",
                "operator_graph",
                "public_input_schema_refs",
                "public_output_schema_refs",
                "parameter_schema",
                "capability_requirements",
            )
            left_body = dict(left.body or {})
            right_body = dict(right.body or {})
            changes = {
                field: {"from": left_body.get(field), "to": right_body.get(field)}
                for field in fields
                if left_body.get(field) != right_body.get(field)
            }
            return {
                "left_revision_id": str(left.revision_id),
                "right_revision_id": str(right.revision_id),
                "changed_fields": sorted(changes),
                "changes": changes,
            }


class SqlMediaRecipeService:
    """Higher-level Media Recipe service with validation."""

    def __init__(self, factory: sessionmaker[Session] | None = None) -> None:
        self._repo = SqlRecipeRepository(factory)
        self._factory = factory or get_session_factory()

    def validate(self, body: dict) -> None:
        """Validate graph, types, policy and AtlasCloud capabilities."""
        recipe_type = body.get("recipe_type", "")
        if not recipe_type:
            raise ValidationError_(
                message="MediaRecipe requires a valid recipe_type",
                details={"field": "recipe_type"},
            )
        compile_media_recipe(body)

    def prepare(self, body: dict) -> dict:
        """Validate and prepare body for storage."""
        self.validate(body)
        prepared = dict(body)
        prepared.setdefault("parameter_schema", {})
        prepared.setdefault("capability_requirements", [])
        compiled = compile_media_recipe(prepared)
        prepared["compiled_plan"] = compiled["compiled_plan"]
        prepared["compiled_plan_hash"] = compiled["plan_hash"]
        return prepared

    def dry_run(self, body: dict) -> dict:
        """Compile with frozen dependencies without performing network I/O."""
        self.validate(body)
        return compile_media_recipe(body)
