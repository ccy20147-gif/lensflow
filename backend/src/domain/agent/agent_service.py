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
}
_SECRET = re.compile(r"(?:sk-[A-Za-z0-9]{12,}|api[_-]?key\s*[:=]|bearer\s+[A-Za-z0-9._-]+)", re.I)


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
    if not isinstance(value, str) or len(value) > 255 or value.strip() != value:
        _reject("Invalid schema reference", field)


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
