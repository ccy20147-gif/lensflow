"""TF-ASR-001: Contract tests for Media Recipe persistence (PostgreSQL-backed).

Tests cover:
  - Media Recipe definition CRUD
  - Draft/Revision lifecycle with CAS base_hash
  - Static validation
  - Dry-run compilation
  - Error scenarios
"""
from __future__ import annotations

import os
from uuid import uuid4

import pytest
from sqlalchemy import text

from src.core.exceptions import ConflictError, NotFoundError, ValidationError_
from src.infra.db.recipe_repository import SqlRecipeRepository, SqlMediaRecipeService
from src.infra.db.session import get_session_factory
from src.schemas.models import MediaRecipeRevision


@pytest.fixture
def pg_factory():
    if os.environ.get("TOONFLOW_RUN_PG_TESTS") != "1":
        pytest.skip("set TOONFLOW_RUN_PG_TESTS=1 to run PostgreSQL integration tests")
    factory = get_session_factory()
    try:
        with factory() as session:
            session.execute(text("SELECT 1"))
    except Exception as exc:
        pytest.skip(f"PostgreSQL unavailable: {exc}")
    return factory


@pytest.fixture
def repo(pg_factory):
    return SqlRecipeRepository(pg_factory)


@pytest.fixture
def svc(pg_factory):
    return SqlMediaRecipeService(pg_factory)


@pytest.fixture
def sample_recipe(repo):
    return repo.create_definition(
        name="test-recipe",
        description="Test recipe for contract tests",
        owner_scope="user:test",
        recipe_type="video_pipeline",
    )


class TestRecipeDefinition:
    def test_create_definition(self, repo):
        row = repo.create_definition(
            name="create-test",
            description="Created in test",
            owner_scope="user:create-test",
            recipe_type="image_gen",
        )
        assert row.name == "create-test"
        assert row.recipe_type == "image_gen"

    def test_get_definition(self, repo, sample_recipe):
        row = repo.get_definition(sample_recipe.recipe_id)
        assert row.name == "test-recipe"

    def test_get_definition_not_found(self, repo):
        with pytest.raises(NotFoundError):
            repo.get_definition(uuid4())

    def test_list_definitions(self, repo, sample_recipe):
        rows = repo.list_definitions(owner_scope="user:test")
        assert len(rows) >= 1

    def test_list_definitions_by_type(self, repo, sample_recipe):
        rows = repo.list_definitions(recipe_type="video_pipeline")
        assert len(rows) >= 1

    def test_update_definition(self, repo, sample_recipe):
        updated = repo.update_definition(
            sample_recipe.recipe_id, name="updated-recipe", recipe_type="audio_mix"
        )
        assert updated.name == "updated-recipe"
        assert updated.recipe_type == "audio_mix"

    def test_delete_definition(self, repo, sample_recipe):
        repo.delete_definition(sample_recipe.recipe_id)
        with pytest.raises(NotFoundError):
            repo.get_definition(sample_recipe.recipe_id)


class TestRecipeRevision:
    def test_create_revision(self, repo, sample_recipe):
        body = {
            "recipe_type": "video_pipeline",
            "operator_graph": {
                "load": {"type": "video_loader", "inputs": [], "outputs": ["frames"]},
            },
        }
        revision = repo.create_revision(sample_recipe.recipe_id, body)
        assert isinstance(revision, MediaRecipeRevision)

    def test_create_revision_with_cas_success(self, repo, sample_recipe):
        body1 = {
            "recipe_type": "video_pipeline",
            "operator_graph": {
                "load": {"type": "video_loader", "inputs": [], "outputs": ["frames"]},
            },
        }
        repo.create_revision(sample_recipe.recipe_id, body1)
        # Compute content hash to use as base_hash for CAS
        from src.infra.db.recipe_repository import _compute_hash
        body2 = {
            "recipe_type": "video_pipeline",
            "operator_graph": {
                "load": {"type": "video_loader", "inputs": [], "outputs": ["frames"]},
                "resize": {"type": "resize_filter", "inputs": ["frames"], "outputs": ["resized"]},
            },
        }
        rev2 = repo.create_revision(
            sample_recipe.recipe_id, body2, base_hash=_compute_hash(body1)
        )
        assert rev2 is not None

    def test_create_revision_cas_conflict(self, repo, sample_recipe):
        body1 = {
            "recipe_type": "video_pipeline",
            "operator_graph": {
                "load": {"type": "video_loader", "inputs": [], "outputs": ["frames"]},
            },
        }
        repo.create_revision(sample_recipe.recipe_id, body1)
        with pytest.raises(ConflictError):
            repo.create_revision(
                sample_recipe.recipe_id, body1, base_hash="wronghash"
            )

    def test_list_revisions(self, repo, sample_recipe):
        body = {
            "recipe_type": "video_pipeline",
            "operator_graph": {
                "load": {"type": "video_loader", "inputs": [], "outputs": ["frames"]},
            },
        }
        repo.create_revision(sample_recipe.recipe_id, body)
        revisions = repo.list_revisions(sample_recipe.recipe_id)
        assert len(revisions) >= 1

    def test_promote_revision(self, repo, sample_recipe):
        body = {
            "recipe_type": "video_pipeline",
            "operator_graph": {
                "load": {"type": "video_loader", "inputs": [], "outputs": ["frames"]},
            },
        }
        revision = repo.create_revision(sample_recipe.recipe_id, body)
        promoted = repo.promote_revision(revision.revision_id)
        assert promoted is not None

    def test_retire_revision(self, repo, sample_recipe):
        body = {
            "recipe_type": "video_pipeline",
            "operator_graph": {
                "load": {"type": "video_loader", "inputs": [], "outputs": ["frames"]},
            },
        }
        revision = repo.create_revision(sample_recipe.recipe_id, body)
        repo.promote_revision(revision.revision_id)
        retired = repo.retire_revision(revision.revision_id)
        assert retired is not None

    def test_diff_revisions_reports_recipe_contract_changes(self, repo, sample_recipe):
        first = repo.create_revision(sample_recipe.recipe_id, {
            "recipe_type": "image_pipeline",
            "public_input_schema_refs": ["toonflow.prompt.v1"],
            "public_output_schema_refs": ["toonflow.media_output.v1"],
            "parameter_schema": {"type": "object", "properties": {"seed": {"type": "integer"}}},
            "capability_requirements": ["atlascloud.image_generation"],
            "operator_graph": {"source": {"type": "input", "outputs": ["prompt"]}},
        })
        second = repo.create_revision(sample_recipe.recipe_id, {
            "recipe_type": "image_pipeline",
            "public_input_schema_refs": ["toonflow.prompt.v1"],
            "public_output_schema_refs": ["toonflow.media_output.v1"],
            "parameter_schema": {"type": "object", "properties": {"seed": {"type": "integer", "default": 4}}},
            "capability_requirements": ["atlascloud.image_generation", "atlascloud.control.pose"],
            "operator_graph": {"source": {"type": "input", "outputs": ["prompt"]}, "image": {"type": "atlas_image", "inputs": ["source.prompt"], "outputs": ["media"]}},
        }, base_hash=first.content_hash)
        result = repo.diff_revisions(first.revision_id, second.revision_id)
        assert result["changed_fields"] == ["capability_requirements", "operator_graph", "parameter_schema"]
        assert "compiled_plan" not in result["changes"]


class TestRecipeValidation:
    def test_validate_valid_recipe(self, svc):
        body = {
            "recipe_type": "video_pipeline",
            "operator_graph": {
                "load": {"type": "video_loader", "inputs": [], "outputs": ["frames"]},
            },
        }
        svc.validate(body)

    def test_validate_missing_operator_graph(self, svc):
        body = {"recipe_type": "video_pipeline", "operator_graph": {}}
        with pytest.raises(ValidationError_):
            svc.validate(body)

    def test_validate_missing_recipe_type(self, svc):
        body = {
            "recipe_type": "",
            "operator_graph": {
                "load": {"type": "video_loader", "inputs": [], "outputs": ["frames"]},
            },
        }
        with pytest.raises(ValidationError_):
            svc.validate(body)

    def test_dry_run(self, svc):
        body = {
            "recipe_type": "video_pipeline",
            "operator_graph": {
                "load": {"type": "video_loader", "inputs": [], "outputs": ["frames"]},
            },
        }
        result = svc.dry_run(body)
        assert result["valid"] is True
        assert result["step_count"] == 1
