"""TF-ASR-001: Contract tests for Agent persistence (PostgreSQL-backed).

Tests cover:
  - Agent definition CRUD
  - Draft/Revision lifecycle with CAS base_hash
  - Static validation
  - Dry-run compilation
  - Error scenarios (conflict, not found)
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from src.core.exceptions import ConflictError, NotFoundError, ValidationError_
from src.infra.db.agent_repository import SqlAgentRepository, SqlAgentService
from src.infra.db.models import (
    AgentTrialRunModel, AgentTrialStepTraceModel, ResourceDraftModel, ResourceModel, ResourceRevisionModel, SkillContentModel,
    SkillRevisionModel, ToolDefinitionModel, ToolRevisionModel, WorkflowModel,
    WorkflowRevisionModel, WorkflowRunModel, NodeRunModel, NodeRunAttemptModel,
)
from src.infra.db.session import get_session_factory
from src.schemas.models import AgentRevision
from src.schemas.enums import AttemptStatus, NodeRunStatus, RevisionStatus, RunStatus


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
    return SqlAgentRepository(pg_factory)


@pytest.fixture
def svc(pg_factory):
    return SqlAgentService(pg_factory)


@pytest.fixture
def sample_agent(repo):
    return repo.create_definition(
        name="test-agent",
        description="Test agent for contract tests",
        agent_kind="configurable",
        owner_scope="user:test",
    )


class TestAgentDefinition:
    def test_create_definition(self, repo):
        row = repo.create_definition(
            name="create-test",
            description="Created in test",
            agent_kind="managed_preset",
            owner_scope="user:create-test",
        )
        assert row.name == "create-test"
        assert row.agent_kind == "managed_preset"

    def test_get_definition(self, repo, sample_agent):
        row = repo.get_definition(sample_agent.agent_id)
        assert row.name == "test-agent"

    def test_get_definition_not_found(self, repo):
        with pytest.raises(NotFoundError):
            repo.get_definition(uuid4())

    def test_list_definitions(self, repo, sample_agent):
        rows = repo.list_definitions(owner_scope="user:test")
        assert len(rows) >= 1
        assert any(r.agent_id == sample_agent.agent_id for r in rows)

    def test_update_definition(self, repo, sample_agent):
        updated = repo.update_definition(sample_agent.agent_id, name="updated-agent")
        assert updated.name == "updated-agent"

    def test_delete_definition(self, repo, sample_agent):
        repo.delete_definition(sample_agent.agent_id)
        with pytest.raises(NotFoundError):
            repo.get_definition(sample_agent.agent_id)


class TestAgentRevision:
    def test_agent_draft_uses_draft_version_cas(self, repo, sample_agent):
        initial = repo.get_draft(sample_agent.agent_id)
        body = {
            "sop_steps": [{"step_id": "s1", "instruction": "author"}],
            "execution_policy": {"provider_ref": "atlascloud/test"},
        }
        saved = repo.save_draft(sample_agent.agent_id, body=body, base_draft_version=initial.draft_version)
        assert saved.draft_version == initial.draft_version + 1
        assert saved.body == body
        with pytest.raises(ConflictError):
            repo.save_draft(sample_agent.agent_id, body=body, base_draft_version=initial.draft_version)

    def test_submit_current_draft_does_not_accept_client_replacement_body(self, repo, sample_agent):
        initial = repo.get_draft(sample_agent.agent_id)
        saved_body = {
            "sop_steps": [{"step_id": "saved", "instruction": "submit saved draft"}],
            "execution_policy": {"provider_ref": "atlascloud/test"},
        }
        saved = repo.save_draft(sample_agent.agent_id, body=saved_body, base_draft_version=initial.draft_version)
        revision = repo.submit_draft(sample_agent.agent_id, base_draft_version=saved.draft_version)
        assert revision.sop_steps[0].step_id == "saved"
        with pytest.raises(ConflictError):
            repo.submit_draft(sample_agent.agent_id, base_draft_version=saved.draft_version)

    def test_draft_dry_run_persists_only_non_business_trace(self, repo, sample_agent, pg_factory):
        draft = repo.get_draft(sample_agent.agent_id)
        body = {
            "sop_steps": [{"step_id": "dry", "instruction": "validate only"}],
            "execution_policy": {"provider_ref": "atlascloud/test", "max_cost": 2},
            "output_schema_ref": "toonflow.trial.v1",
            "output_schema": {"type": "object", "required": ["answer"], "properties": {"answer": {"type": "string"}}},
        }
        saved = repo.save_draft(sample_agent.agent_id, body=body, base_draft_version=draft.draft_version)
        result = repo.dry_run_draft(sample_agent.agent_id, draft_version=saved.draft_version, budget={"max_cost": 3},
            fixed_input={"sample": "x"}, simulated_output={"answer": "ok"}, usage={"tokens": 3},
            tool_disclosures=[{"tool_revision_id": "fixture", "fields": ["sample"]}])
        with pg_factory() as session:
            trial = session.get(AgentTrialRunModel, UUID(result["trial_id"]))
            assert trial is not None and trial.status == "completed" and trial.fixed_body == body and trial.fixed_input == {"sample": "x"}
            traces = session.query(AgentTrialStepTraceModel).filter(AgentTrialStepTraceModel.trial_id == trial.trial_id).all()
            assert [(row.step_id, row.status, row.usage["tokens"]) for row in traces] == [("dry", "completed", 3)]
            assert traces[0].tool_disclosures == [{"tool_revision_id": "fixture", "fields": ["sample"]}]
        failed = repo.dry_run_draft(sample_agent.agent_id, draft_version=saved.draft_version, budget={}, simulated_output={"wrong": True})
        assert failed["status"] == "failed" and failed["failure_owner"] == "output_schema"

    def test_clone_preserves_lineage_and_never_copies_credential_binding(self, repo, sample_agent):
        revision = repo.create_revision(sample_agent.agent_id, {
            "sop_steps": [{"step_id": "s", "instruction": "clone"}],
            "execution_policy": {"provider_ref": "atlascloud/test"},
        })
        repo.promote_revision(revision.revision_id)
        clone = repo.clone_definition(sample_agent.agent_id, owner_scope="user:test", name="clone")
        draft = repo.get_draft(clone.agent_id)
        assert clone.cloned_from_agent_id == sample_agent.agent_id
        assert draft.body["clone_lineage"]["source_revision_id"] == str(revision.revision_id)
        assert "credential_binding" not in draft.body

    def test_trial_request_input_survives_reload_and_uses_answer_cas(self, repo, sample_agent):
        draft = repo.get_draft(sample_agent.agent_id)
        body = {"sop_steps": [{"step_id": "s", "instruction": "trial"}], "execution_policy": {"provider_ref": "atlascloud/test"}}
        saved = repo.save_draft(sample_agent.agent_id, body=body, base_draft_version=draft.draft_version)
        trial = repo.dry_run_draft(sample_agent.agent_id, draft_version=saved.draft_version, budget={})
        task = repo.create_trial_request_input(UUID(trial["trial_id"]), schema_ref="choice.v1", question="Choose",
            input_schema={"type": "object", "required": ["choice"], "properties": {"choice": {"type": "string", "enum": ["yes", "no"]}}})
        recovered = repo.get_trial_request_input(task.task_id, owner_scope="user:test")
        accepted = repo.resolve_trial_request_input(task.task_id, task_version=recovered.task_version, answer={"choice": "yes"})
        assert accepted.status == "accepted" and accepted.answer == {"choice": "yes"}
        with pytest.raises(ConflictError):
            repo.resolve_trial_request_input(task.task_id, task_version=recovered.task_version, answer={"choice": "yes"})

    def test_create_revision(self, repo, sample_agent):
        body = {
            "sop_steps": [{"step_id": "s1", "instruction": "Do something"}],
            "execution_policy": {"provider_ref": "atlascloud/gpt-4"},
        }
        revision = repo.create_revision(sample_agent.agent_id, body)
        assert isinstance(revision, AgentRevision)
        assert revision.revision_id is not None

    def test_create_revision_with_cas_success(self, repo, sample_agent):
        body1 = {
            "sop_steps": [{"step_id": "s1", "instruction": "Step 1"}],
            "execution_policy": {"provider_ref": "atlascloud/gpt-4"},
        }
        repo.create_revision(sample_agent.agent_id, body1)
        # Use body1's content_hash as base_hash for CAS
        from src.infra.db.agent_repository import _compute_hash
        body2 = {
            "sop_steps": [
                {"step_id": "s1", "instruction": "Step 1"},
                {"step_id": "s2", "instruction": "Step 2"},
            ],
            "execution_policy": {"provider_ref": "atlascloud/gpt-4"},
        }
        rev2 = repo.create_revision(
            sample_agent.agent_id, body2, base_hash=_compute_hash(body1)
        )
        assert rev2 is not None

    def test_create_revision_cas_conflict(self, repo, sample_agent):
        body1 = {
            "sop_steps": [{"step_id": "s1", "instruction": "Step 1"}],
            "execution_policy": {"provider_ref": "atlascloud/gpt-4"},
        }
        repo.create_revision(sample_agent.agent_id, body1)
        # Wrong base_hash should cause conflict
        with pytest.raises(ConflictError):
            repo.create_revision(
                sample_agent.agent_id, body1, base_hash="wronghash"
            )

    def test_list_revisions(self, repo, sample_agent):
        body = {
            "sop_steps": [{"step_id": "s1", "instruction": "Test"}],
            "execution_policy": {"provider_ref": "atlascloud/gpt-4"},
        }
        repo.create_revision(sample_agent.agent_id, body)
        revisions = repo.list_revisions(sample_agent.agent_id)
        assert len(revisions) >= 1

    def test_promote_revision(self, repo, sample_agent):
        body = {
            "sop_steps": [{"step_id": "s1", "instruction": "Test"}],
            "execution_policy": {"provider_ref": "atlascloud/gpt-4"},
        }
        revision = repo.create_revision(sample_agent.agent_id, body)
        promoted = repo.promote_revision(revision.revision_id)
        assert promoted is not None  # promoted successfully

    def test_retire_revision(self, repo, sample_agent):
        body = {
            "sop_steps": [{"step_id": "s1", "instruction": "Test"}],
            "execution_policy": {"provider_ref": "atlascloud/gpt-4"},
        }
        revision = repo.create_revision(sample_agent.agent_id, body)
        repo.promote_revision(revision.revision_id)
        retired = repo.retire_revision(revision.revision_id)
        assert retired is not None

    def test_agent_identity_and_revisions_are_canonical_resources(self, repo, sample_agent, pg_factory):
        first = repo.create_revision(sample_agent.agent_id, {
            "sop_steps": [{"step_id": "s1", "instruction": "one"}],
            "execution_policy": {"provider_ref": "atlascloud/test"},
        })
        repo.promote_revision(first.revision_id)
        second = repo.create_revision(sample_agent.agent_id, {
            "sop_steps": [{"step_id": "s1", "instruction": "two"}],
            "execution_policy": {"provider_ref": "atlascloud/test"},
        }, base_hash=first.content_hash)
        with pg_factory() as session:
            resource = session.get(ResourceModel, sample_agent.agent_id)
            draft = session.get(ResourceDraftModel, sample_agent.agent_id)
            frozen_first = session.get(ResourceRevisionModel, first.revision_id)
            frozen_second = session.get(ResourceRevisionModel, second.revision_id)
            assert resource is not None and resource.resource_type == "agent"
            assert draft is not None and draft.base_revision_id == second.revision_id and draft.draft_version == 2
            assert frozen_first is not None and frozen_first.revision_status.value == "active"
            assert frozen_second is not None and frozen_second.revision_status.value == "draft"
        repo.retire_revision(first.revision_id)
        # Re-activating a pinned former revision is an explicit rollback,
        # never a body overwrite or latest lookup.
        rolled_back = repo.promote_revision(first.revision_id)
        assert rolled_back.revision_id == first.revision_id and rolled_back.revision_status.value == "active"

    def test_agent_revision_usage_index_covers_workflow_and_attempt(self, repo, sample_agent, pg_factory):
        revision = repo.create_revision(sample_agent.agent_id, {
            "sop_steps": [{"step_id": "s", "instruction": "index"}],
            "execution_policy": {"provider_ref": "atlascloud/test"},
        })
        repo.promote_revision(revision.revision_id)
        with pg_factory.begin() as session:
            workflow_id, workflow_revision_id, run_id, node_id, attempt_id = (uuid4() for _ in range(5))
            session.add(WorkflowModel(workflow_id=workflow_id, owner_scope="user:test"))
            session.flush()
            session.add(WorkflowRevisionModel(
                revision_id=workflow_revision_id, workflow_id=workflow_id, revision_number=1,
                graph_hash="g", execution_hash="e", registry_snapshot_id=uuid4(),
                graph={"nodes": [{"id": "agent", "config": {"agent_revision_id": str(revision.revision_id)}}]},
                config={}, layout={}, revision_status=RevisionStatus.ACTIVE, created_at=datetime.now(timezone.utc),
            ))
            session.flush()
            session.add(WorkflowRunModel(run_id=run_id, workflow_revision_id=workflow_revision_id,
                compiled_plan_id=uuid4(), owner_scope="user:test", input_snapshot={}, status=RunStatus.RUNNING,
                created_at=datetime.now(timezone.utc)))
            session.flush()
            session.add(NodeRunModel(node_run_id=node_id, run_id=run_id, node_instance_id="agent",
                node_type_id="agent_invoke", status=NodeRunStatus.RUNNING))
            session.flush()
            session.add(NodeRunAttemptModel(attempt_id=attempt_id, node_run_id=node_id,
                status=AttemptStatus.RUNNING, fixed_input={"agent_revision_id": str(revision.revision_id)}))
        assert repo.usage_index(revision.revision_id) == {
            "workflow_revision_ids": [str(workflow_revision_id)], "attempt_ids": [str(attempt_id)],
        }


class TestAgentValidation:
    def test_validate_valid_agent(self, svc):
        body = {
            "sop_steps": [{"step_id": "s1", "instruction": "Do work"}],
            "execution_policy": {"provider_ref": "atlascloud/gpt-4"},
        }
        # Should not raise
        svc.validate(body)

    def test_validate_missing_steps(self, svc):
        body = {"sop_steps": [], "execution_policy": {"provider_ref": "atlascloud/gpt-4"}}
        with pytest.raises(ValidationError_):
            svc.validate(body)

    def test_validate_missing_provider_ref(self, svc):
        body = {
            "sop_steps": [{"step_id": "s1", "instruction": "Do work"}],
            "execution_policy": {"provider_ref": ""},
        }
        with pytest.raises(ValidationError_):
            svc.validate(body)

    def test_rejects_non_atlascloud_model_policy(self, svc):
        with pytest.raises(ValidationError_) as exc:
            svc.validate({
                "sop_steps": [{"step_id": "s1", "instruction": "Do work"}],
                "execution_policy": {"provider_ref": "openai/gpt-4"},
            })
        assert exc.value.details["field"] == "execution_policy.provider_ref"

    def test_dry_run(self, svc):
        body = {
            "sop_steps": [{"step_id": "s1", "instruction": "Do work"}],
            "execution_policy": {"provider_ref": "atlascloud/gpt-4"},
        }
        result = svc.dry_run(body)
        assert result["valid"] is True
        assert result["step_count"] == 1

    @pytest.mark.parametrize("step", [
        {"step_id": "nested", "instruction": "x", "workflow": "latest"},
        {"step_id": "gate", "instruction": "x", "human_gate": {"required": True}},
        {"step_id": "code", "instruction": "x", "script": "print(1)"},
    ])
    def test_rejects_forbidden_sop_capability(self, svc, step):
        with pytest.raises(ValidationError_) as exc:
            svc.validate({"sop_steps": [step], "execution_policy": {"provider_ref": "atlascloud/llm"}})
        assert exc.value.details["code"] == "AGENT_POLICY_BLOCKED"

    def test_rejects_sop_secret(self, svc):
        with pytest.raises(ValidationError_):
            svc.validate({"sop_steps": [{"step_id": "s1", "instruction": "use sk-abcdefghijklmnop"}], "execution_policy": {"provider_ref": "atlascloud/llm"}})

    def test_revision_diff_is_field_level(self, repo, sample_agent):
        one = repo.create_revision(sample_agent.agent_id, {"sop_steps": [{"step_id": "s1", "instruction": "one"}], "execution_policy": {"provider_ref": "atlascloud/llm"}})
        two = repo.create_revision(sample_agent.agent_id, {"sop_steps": [{"step_id": "s1", "instruction": "two"}], "execution_policy": {"provider_ref": "atlascloud/llm"}}, base_hash=one.content_hash)
        assert "sop_steps" in repo.diff_revisions(one.revision_id, two.revision_id)["changed_fields"]

    def test_multi_agent_graph_requires_pinned_active_revisions(self, repo, sample_agent, pg_factory):
        from src.domain.agent.orchestration import MultiAgentOrchestrator
        revision = repo.create_revision(sample_agent.agent_id, {"sop_steps": [{"step_id": "s1", "instruction": "one"}], "execution_policy": {"provider_ref": "atlascloud/llm"}})
        repo.promote_revision(revision.revision_id)
        result = MultiAgentOrchestrator(pg_factory).validate_graph({"nodes": [{"id": "a", "type": "agent_invoke", "agent_revision_id": str(revision.revision_id)}], "edges": []})
        assert result["scheduler"] == "wf_007"
        with pytest.raises(ValidationError_):
            MultiAgentOrchestrator(pg_factory).validate_graph({"nodes": [{"id": "a", "type": "agent_invoke"}], "edges": []})


class TestAgentDependencyBinding:
    def test_revision_requires_active_same_scope_skill(self, repo, sample_agent, pg_factory):
        with pg_factory.begin() as session:
            inactive = SkillContentModel(
                skill_id=uuid4(), name="inactive", description="", owner_scope="user:test",
                body={"instructions": ["x"]}, content_hash="x", status="draft",
            )
            foreign = SkillContentModel(
                skill_id=uuid4(), name="foreign", description="", owner_scope="user:other",
                body={"instructions": ["x"]}, content_hash="y", status="active",
            )
            valid = SkillContentModel(
                skill_id=uuid4(), name="valid", description="", owner_scope="user:test",
                body={"instructions": ["x"]}, content_hash="z", status="active",
            )
            session.add_all([inactive, foreign, valid])
            session.flush()
            inactive_revision = SkillRevisionModel(revision_id=uuid4(), skill_id=inactive.skill_id, revision_number=1, body=inactive.body, content_hash="ix", status="retired")
            foreign_revision = SkillRevisionModel(revision_id=uuid4(), skill_id=foreign.skill_id, revision_number=1, body=foreign.body, content_hash="fy", status="active")
            valid_revision = SkillRevisionModel(revision_id=uuid4(), skill_id=valid.skill_id, revision_number=1, body=valid.body, content_hash="vz", status="active")
            session.add_all([inactive_revision, foreign_revision, valid_revision])
        base = {
            "sop_steps": [{"step_id": "s1", "instruction": "Do work"}],
            "execution_policy": {"provider_ref": "atlascloud/gpt-4"},
        }
        for skill_id in (inactive_revision.revision_id, foreign_revision.revision_id):
            with pytest.raises(ValidationError_):
                repo.create_revision(sample_agent.agent_id, {**base, "skill_revision_refs": [str(skill_id)]})
        revision = repo.create_revision(sample_agent.agent_id, {**base, "skill_revision_refs": [str(valid_revision.revision_id)]})
        assert revision.skill_revision_refs == [valid_revision.revision_id]

    def test_revision_requires_approved_same_scope_tool(self, repo, sample_agent, pg_factory):
        with pg_factory.begin() as session:
            tool = ToolDefinitionModel(tool_id=uuid4(), name="tool", description="", owner_scope="user:test")
            approved = ToolRevisionModel(
                revision_id=uuid4(), tool_id=tool.tool_id, revision_number=1,
                body={"operations": [{"id": "read", "disclosure_fields": []}]},
                content_hash="t", status="active", approval_status="approved",
            )
            foreign_tool = ToolDefinitionModel(tool_id=uuid4(), name="foreign", description="", owner_scope="user:other")
            foreign_revision = ToolRevisionModel(
                revision_id=uuid4(), tool_id=foreign_tool.tool_id, revision_number=1, body={},
                content_hash="u", status="active", approval_status="approved",
            )
            session.add_all([tool, approved, foreign_tool, foreign_revision])
        base = {
            "sop_steps": [{"step_id": "s1", "instruction": "Do work"}],
            "execution_policy": {"provider_ref": "atlascloud/gpt-4"},
        }
        with pytest.raises(ValidationError_):
            repo.create_revision(sample_agent.agent_id, {**base, "tool_revision_refs": [str(foreign_revision.revision_id)]})
        revision = repo.create_revision(sample_agent.agent_id, {
            **base, "tool_revision_refs": [str(approved.revision_id)],
            "tool_access_plan": [{"tool_revision_id": str(approved.revision_id), "operations": [{"operation_id": "read", "allowed_scopes": [], "disclosure_fields": []}]}],
        })
        assert revision.tool_revision_refs == [approved.revision_id]

    def test_prepare_binds_owner_and_persists_skill_assembly(self, pg_factory):
        from src.api.routes.agent import PrepareAgentInvokeRequest, _agent, prepare_agent_invoke
        from src.infra.db.identity_repository import get_session_store

        owner_id = uuid4()
        owner_scope = f"user:{owner_id}"
        agent = _agent._repo.create_definition(
            name="prepared-agent", description="", agent_kind="configurable", owner_scope=owner_scope,
        )
        with pg_factory.begin() as session:
            skill = SkillContentModel(
                skill_id=uuid4(), name="prepared-skill", description="", owner_scope=owner_scope,
                body={"instructions": ["Use the supplied context"], "priority": 10},
                content_hash="skill", status="active",
            )
            session.add(skill)
            session.flush()
            skill_revision = SkillRevisionModel(revision_id=uuid4(), skill_id=skill.skill_id, revision_number=1, body=skill.body, content_hash="prepared-revision", status="active")
            session.add(skill_revision)
        revision = _agent._repo.create_revision(agent.agent_id, {
            "sop_steps": [{"step_id": "s1", "instruction": "Do work"}],
            "skill_revision_refs": [str(skill_revision.revision_id)],
            "execution_policy": {"provider_ref": "atlascloud/gpt-4"},
        })
        _agent._repo.promote_revision(revision.revision_id)
        token = get_session_store().issue(owner_id)["token"]
        prepared = asyncio.run(prepare_agent_invoke(PrepareAgentInvokeRequest(
            agent_revision_id=revision.revision_id,
        ), authorization=f"Bearer {token}"))
        assert prepared["skill_assembly_plan_id"]
        assert prepared["skill_assembly_fingerprint"]
        with pytest.raises(Exception) as exc:
            asyncio.run(prepare_agent_invoke(PrepareAgentInvokeRequest(
                agent_revision_id=revision.revision_id,
            ), authorization=f"Bearer {get_session_store().issue(uuid4())['token']}"))
        assert getattr(exc.value, "status_code", None) == 403
