"""Agent definition validation and deterministic compilation.

This module deliberately has no provider client.  An AgentRevision is a
declarative, non-side-effecting contract; the runtime/approved tool broker is
the only component allowed to dispatch work.
"""
from __future__ import annotations

import re
from typing import Any

from src.core.exceptions import ValidationError_
from src.schemas.models import AgentRevision

_FORBIDDEN_KEYS = {
    "agent", "agent_invoke", "workflow", "subworkflow", "recipe",
    "media_recipe", "human_gate", "workbench_task", "resource_commit",
    "resource_revision", "resource_draft", "code", "script", "shell",
    "url", "http", "network", "credential", "credential_binding",
    # Managed task plans belong exclusively to registered Workflow nodes.
    "managed_task_plan", "managed_agent_task_plan", "task_plan",
}
_SECRET = re.compile(r"(?:sk-[A-Za-z0-9]{12,}|api[_-]?key\s*[:=]|bearer\s+[A-Za-z0-9._-]+)", re.I)
_SCHEMA_REF = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]*(?:\.v|@)[1-9][0-9]*$")
_EXECUTION_BOUNDARY = "runtime_and_approved_tool_broker_only"
_FAILURE_STRATEGIES = {"fail", "retry", "request_input"}
_CHECKPOINT_MODES = {"none", "after_step"}


def _reject(message: str, field: str) -> None:
    raise ValidationError_(message=message, details={"field": field, "code": "AGENT_POLICY_BLOCKED"})


def _walk(value: Any, path: str = "") -> None:
    """Reject capability escape hatches at their exact JSON path."""
    if isinstance(value, str):
        if _SECRET.search(value):
            _reject("Agent SOP may not contain secrets", path)
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _walk(item, f"{path}[{index}]")
        return
    if not isinstance(value, dict):
        return
    for key, item in value.items():
        normalized = str(key).lower().replace("-", "_").replace(" ", "_")
        item_path = f"{path}.{key}" if path else str(key)
        if normalized in _FORBIDDEN_KEYS:
            _reject(f"Agent SOP cannot declare {key}", item_path)
        _walk(item, item_path)


def _schema_ref(value: Any, field: str, *, required: bool) -> None:
    if not value:
        if required:
            _reject("Agent requires a typed schema reference", field)
        return
    if not isinstance(value, str) or len(value) > 255 or value.strip() != value or not _SCHEMA_REF.fullmatch(value):
        _reject("Schema reference must include a stable identity and positive version", field)


def _request_input_policy(value: Any) -> None:
    """Validate the frozen policy which gates runtime RequestInput creation."""
    if value is None:
        return
    if not isinstance(value, dict):
        _reject("request_input_policy must be an object", "request_input_policy")
    allowed = {"enabled", "allowed_schema_refs", "max_requests_per_attempt", "max_timeout_minutes", "max_response_bytes"}
    unknown = set(value) - allowed
    if unknown:
        _reject("request_input_policy contains unsupported fields", "request_input_policy")
    if "enabled" in value and not isinstance(value["enabled"], bool):
        _reject("request_input_policy.enabled must be boolean", "request_input_policy.enabled")
    refs = value.get("allowed_schema_refs", [])
    if not isinstance(refs, list) or len(set(map(str, refs))) != len(refs):
        _reject("request_input_policy.allowed_schema_refs must be unique", "request_input_policy.allowed_schema_refs")
    for index, ref in enumerate(refs):
        _schema_ref(ref, f"request_input_policy.allowed_schema_refs[{index}]", required=True)
    for field, maximum in (("max_requests_per_attempt", 16), ("max_timeout_minutes", 10_080), ("max_response_bytes", 1_000_000)):
        if field in value and (not isinstance(value[field], int) or isinstance(value[field], bool) or not 1 <= value[field] <= maximum):
            _reject(f"request_input_policy.{field} is outside allowed bounds", f"request_input_policy.{field}")


def _bindings(value: Any, field: str) -> None:
    """Validate declarative SOP data mappings, never executable expressions."""
    if not isinstance(value, dict):
        _reject(f"{field} must be an object", field)
    for key, reference in value.items():
        if not isinstance(key, str) or not key or not isinstance(reference, str) or not reference:
            _reject(f"{field} must map non-empty names to non-empty references", field)
        if len(key) > 128 or len(reference) > 512:
            _reject(f"{field} entry exceeds size limit", field)


def _step_execution_policies(step: dict[str, Any], path: str) -> None:
    _bindings(step.get("input_bindings", {}), f"{path}.input_bindings")
    _bindings(step.get("output_bindings", {}), f"{path}.output_bindings")
    checkpoint = step.get("checkpoint_policy", {})
    if not isinstance(checkpoint, dict) or set(checkpoint) - {"mode"}:
        _reject("checkpoint_policy only supports mode", f"{path}.checkpoint_policy")
    checkpoint_mode = checkpoint.get("mode", "none")
    if checkpoint_mode not in _CHECKPOINT_MODES:
        _reject("checkpoint_policy.mode is unsupported", f"{path}.checkpoint_policy.mode")
    failure = step.get("failure_policy", {})
    if not isinstance(failure, dict) or set(failure) - {"strategy"}:
        _reject("failure_policy only supports strategy", f"{path}.failure_policy")
    strategy = failure.get("strategy", "fail")
    if strategy not in _FAILURE_STRATEGIES:
        _reject("failure_policy.strategy is unsupported", f"{path}.failure_policy.strategy")


def validate_agent(body: dict[str, Any]) -> None:
    """Validate the frozen, capability-safe AgentRevision body."""
    steps = body.get("sop_steps", body.get("steps", []))
    if not isinstance(steps, list) or not steps:
        _reject("Agent requires at least one SOP step", "sop_steps")
    if len(steps) > 64:
        _reject("Agent exceeds the 64 step execution bound", "sop_steps")
    policy = body.get("execution_policy", {})
    if not isinstance(policy, dict) or not policy.get("provider_ref"):
        _reject("Agent requires execution_policy.provider_ref", "execution_policy.provider_ref")
    provider_ref = policy.get("provider_ref")
    if (
        not isinstance(provider_ref, str)
        or not provider_ref.startswith("atlascloud/")
        or provider_ref == "atlascloud/"
    ):
        _reject(
            "Agent execution_policy.provider_ref must select an AtlasCloud model",
            "execution_policy.provider_ref",
        )
    for limit in ("max_attempts", "max_tokens", "max_cost", "timeout_seconds"):
        if limit in policy and (not isinstance(policy[limit], (int, float)) or policy[limit] < 0):
            _reject(f"{limit} must be a non-negative number", f"execution_policy.{limit}")
    _schema_ref(body.get("input_schema_ref"), "input_schema_ref", required=False)
    _schema_ref(body.get("output_schema_ref"), "output_schema_ref", required=False)
    output_schema = body.get("output_schema")
    if output_schema is not None:
        if not body.get("output_schema_ref"):
            _reject("output_schema requires output_schema_ref", "output_schema_ref")
        # Validate the schema contract itself through a representative empty
        # value only for malformed shape errors; a real output is checked at
        # publication time by AgentInvocationService.
        if not isinstance(output_schema, dict) or "type" not in output_schema:
            _reject("output_schema must be a typed JSON Schema object", "output_schema")
    boundary = body.get("execution_boundary", _EXECUTION_BOUNDARY)
    if boundary != _EXECUTION_BOUNDARY:
        _reject("Agent execution_boundary must be runtime_and_approved_tool_broker_only", "execution_boundary")
    _request_input_policy(body.get("request_input_policy", {}))
    seen: set[str] = set()
    for index, step in enumerate(steps):
        path = f"sop_steps[{index}]"
        if not isinstance(step, dict):
            _reject("SOP step must be an object", path)
        step_id = step.get("step_id")
        if not isinstance(step_id, str) or not step_id.strip() or step_id in seen:
            _reject("SOP step_id must be present and unique", f"{path}.step_id")
        seen.add(step_id)
        if not isinstance(step.get("instruction"), str) or not step["instruction"].strip():
            _reject("SOP step requires an instruction", f"{path}.instruction")
        retries = step.get("retry_policy", {})
        if retries and (not isinstance(retries, dict) or int(retries.get("max_attempts", 1)) < 1):
            _reject("retry_policy.max_attempts must be at least one", f"{path}.retry_policy")
        _step_execution_policies(step, path)
        _schema_ref(step.get("output_schema_ref"), f"{path}.output_schema_ref", required=False)
        _walk(step, path)
    for field in ("skill_revision_refs", "tool_revision_refs"):
        refs = body.get(field, [])
        if not isinstance(refs, list) or len(set(map(str, refs))) != len(refs):
            _reject(f"{field} must contain unique frozen revision IDs", field)
    tool_refs = {str(value) for value in body.get("tool_revision_refs", [])}
    access_plan = body.get("tool_access_plan", [])
    if not isinstance(access_plan, list):
        _reject("tool_access_plan must be a list", "tool_access_plan")
    plan_refs: set[str] = set()
    for entry_index, entry in enumerate(access_plan):
        path = f"tool_access_plan[{entry_index}]"
        if not isinstance(entry, dict):
            _reject("Tool access plan entry must be an object", path)
        raw_revision = str(entry.get("tool_revision_id", ""))
        if not raw_revision or raw_revision not in tool_refs or raw_revision in plan_refs:
            _reject("Tool access plan must reference each declared ToolRevision once", f"{path}.tool_revision_id")
        plan_refs.add(raw_revision)
        operations = entry.get("operations")
        if not isinstance(operations, list) or not operations:
            _reject("Tool access plan requires at least one operation", f"{path}.operations")
        operation_ids: set[str] = set()
        for operation_index, operation in enumerate(operations):
            operation_path = f"{path}.operations[{operation_index}]"
            if not isinstance(operation, dict):
                _reject("Tool access operation must be an object", operation_path)
            operation_id = operation.get("operation_id")
            if not isinstance(operation_id, str) or not operation_id or operation_id in operation_ids:
                _reject("Tool access operation_id must be present and unique", f"{operation_path}.operation_id")
            operation_ids.add(operation_id)
            for key in ("allowed_scopes", "disclosure_fields"):
                values = operation.get(key, [])
                if not isinstance(values, list) or any(not isinstance(value, str) or not value for value in values):
                    _reject(f"{key} must contain non-empty strings", f"{operation_path}.{key}")
                if len(set(values)) != len(values):
                    _reject(f"{key} must not contain duplicates", f"{operation_path}.{key}")
    if tool_refs != plan_refs:
        _reject("Every ToolRevision requires one complete frozen access plan", "tool_access_plan")
    _walk({k: v for k, v in body.items() if k not in {"sop_steps", "steps"}})


def prepare_body(agent_revision: AgentRevision) -> dict[str, Any]:
    return agent_revision.model_dump(mode="json", exclude={"revision_id"})
