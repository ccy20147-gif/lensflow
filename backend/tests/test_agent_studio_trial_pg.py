"""AGT-002 Studio trial contracts over the real durable runtime boundary."""
from __future__ import annotations

import os
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, text

from cryptography.fernet import Fernet

from src.core.config import settings
from src.domain.agent.request_input import AgentRequestInputService
from src.domain.agent.tool_broker import ToolBroker
from src.infra.db.agent_repository import SqlAgentRepository
from src.infra.db.models import (
    AgentTrialRunModel,
    NodeRunAttemptModel,
    ToolDefinitionModel,
    ToolInvocationModel,
    ToolRevisionModel,
)
from src.infra.db.session import get_session_factory
from src.schemas.enums import AttemptStatus
from src.schemas.models import OwnerScope


pytestmark = pytest.mark.skipif(
    os.environ.get("TOONFLOW_RUN_PG_TESTS") != "1",
    reason="set TOONFLOW_RUN_PG_TESTS=1 to run PostgreSQL integration tests",
)


@pytest.fixture
def factory():
    value = get_session_factory()
    with value() as session:
        session.execute(text("SELECT 1"))
    return value


def _bound_trial(factory, monkeypatch):
    owner = OwnerScope(kind="user", id=uuid4())
    # The repository's trial code intentionally instantiates the broker at
    # the server boundary, so use a process-local test-only encryption key.
    monkeypatch.setattr(settings, "credential_encryption_key", Fernet.generate_key().decode())
    with factory.begin() as session:
        tool = ToolDefinitionModel(tool_id=uuid4(), name="studio-trial-tool", owner_scope=owner.scoped_id, provider_type="atlascloud")
        tool_revision = ToolRevisionModel(
            revision_id=uuid4(), tool_id=tool.tool_id, revision_number=1, status="active", approval_status="approved",
            body={
                "risk_level": "low", "data_classifications": ["internal"],
                "sanitizer_policy": {"policy_version": "platform.v1"},
                "operations": [{"id": "generate", "input_schema": {"type": "object"}, "output_schema": {"type": "object"},
                                "output_schema_ref": "tool_result.v1", "disclosure_fields": [],
                                "endpoint": "https://api.atlascloud.ai/studio-trial",
                                "execution_limits": {"max_calls_per_step": 1, "max_calls_per_run": 1, "max_concurrency": 1,
                                                     "max_cost": 1, "max_retries": 0, "cost_estimate": 0.01}}],
                "egress_policy": {"allowed_domains": ["api.atlascloud.ai"], "allowed_mime_types": ["application/json"],
                                  "timeout_seconds": 20, "max_request_bytes": 100_000, "max_response_bytes": 100_000},
            },
        )
        session.add_all([tool, tool_revision])
    binding = ToolBroker(factory).bind(owner_scope=owner.scoped_id, tool_revision_id=tool_revision.revision_id, scopes=[], secret="trial-secret")
    agents = SqlAgentRepository(factory)
    definition = agents.create_definition(name="studio-trial", description="", agent_kind="configurable", owner_scope=owner.scoped_id)
    draft = agents.get_draft(definition.agent_id)
    saved = agents.save_draft(definition.agent_id, base_draft_version=draft.draft_version, body={
        "tool_revision_refs": [str(tool_revision.revision_id)],
        "tool_access_plan": [{"tool_revision_id": str(tool_revision.revision_id), "operations": [{"operation_id": "generate", "allowed_scopes": [], "disclosure_fields": []}]}],
        "sop_steps": [{"step_id": "call_tool", "instruction": "Call only the approved tool"}],
        "execution_policy": {"provider_ref": "atlascloud/qwen-test"},
    })
    return owner, agents, definition.agent_id, saved.draft_version, binding.binding_id


def test_isolated_trial_authorizes_and_dispatches_a_frozen_bound_tool(factory, monkeypatch):
    owner, agents, agent_id, draft_version, binding_id = _bound_trial(factory, monkeypatch)
    # Mock transport must still traverse ToolBroker's binding, frozen access
    # plan, disclosure and durable dispatch path.  DNS is pinned public here
    # so the unit is independent of resolver availability.
    monkeypatch.setattr("src.domain.agent.tool_broker.socket.getaddrinfo", lambda *_args, **_kwargs: [(None, None, None, None, ("8.8.8.8", 443))])
    result = agents.run_isolated_runtime_trial(agent_id, draft_version=draft_version, budget={}, fixed_input={"sample": "studio"})
    assert result["status"] == "completed"
    assert result["runtime_run_id"] and result["runtime_trial_agent_revision_id"]
    dispatch = [entry for entry in result["runtime_timeline"] if entry["phase"] == "tool_dispatch"]
    assert len(dispatch) == 1
    assert dispatch[0]["status"] == "completed"
    assert dispatch[0]["tool_disclosures"][0]["fields"] == []
    assert str(binding_id) not in str(result)
    with factory() as session:
        invocation = session.scalar(select(ToolInvocationModel).where(ToolInvocationModel.owner_scope == owner.scoped_id))
        trial = session.get(AgentTrialRunModel, UUID(result["trial_id"]))
        assert invocation is not None and invocation.status == "completed"
        assert trial is not None and trial.runtime_attempt_id is not None and trial.runtime_run_id is not None
        assert session.scalar(select(func.count()).select_from(ToolInvocationModel).where(ToolInvocationModel.node_run_attempt_id == trial.runtime_attempt_id)) == 1


def test_trial_request_input_refresh_resumes_the_same_attempt_without_replaying_tool(factory, monkeypatch):
    owner, agents, agent_id, draft_version, _binding_id = _bound_trial(factory, monkeypatch)
    monkeypatch.setattr("src.domain.agent.tool_broker.socket.getaddrinfo", lambda *_args, **_kwargs: [(None, None, None, None, ("8.8.8.8", 443))])
    result = agents.run_isolated_runtime_trial(agent_id, draft_version=draft_version, budget={}, fixed_input={})
    with factory() as session:
        trial = session.get(AgentTrialRunModel, UUID(result["trial_id"]))
        assert trial is not None and trial.runtime_attempt_id and trial.runtime_node_run_id and trial.runtime_run_id and trial.runtime_agent_revision_id
        attempt_id, node_id, run_id, revision_id = trial.runtime_attempt_id, trial.runtime_node_run_id, trial.runtime_run_id, trial.runtime_agent_revision_id
        before = session.scalar(select(func.count()).select_from(ToolInvocationModel).where(ToolInvocationModel.node_run_attempt_id == attempt_id))
    service = AgentRequestInputService(factory)
    task = service.create(agent_revision_id=revision_id, run_id=run_id, node_run_id=node_id, attempt_id=attempt_id,
                          schema_ref="choice.v1", question="Choose", timeout_minutes=10, idempotency_token="trial-question",
                          input_schema={"type": "object", "required": ["choice"], "properties": {"choice": {"type": "string", "enum": ["yes", "no"]}}},
                          requester_scope=owner.scoped_id)
    # Reconstruct the service as a browser refresh would.  CAS/decision data
    # is durable; accepting the typed response changes only this fixed attempt.
    recovered = AgentRequestInputService(factory).resolve(task_id=task.task_id, task_version=task.task_version,
        idempotency_token="trial-answer", answer={"choice": "yes"}, requester_scope=owner.scoped_id)
    assert recovered.task_id == task.task_id
    with factory() as session:
        attempt = session.get(NodeRunAttemptModel, attempt_id)
        after = session.scalar(select(func.count()).select_from(ToolInvocationModel).where(ToolInvocationModel.node_run_attempt_id == attempt_id))
        assert attempt is not None and attempt.status == AttemptStatus.RUNNING
        assert attempt.fixed_input["request_input"]["answer"] == {"choice": "yes"}
        assert before == after == 1
