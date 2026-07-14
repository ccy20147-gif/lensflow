"""Compile a frozen AgentRevision into a deterministic, inspectable plan."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from .agent_service import validate_agent


def compile_agent(body: dict[str, Any]) -> dict[str, Any]:
    validate_agent(body)
    steps = body.get("sop_steps", body.get("steps", []))
    compiled = [
        {
            "step_id": step["step_id"],
            "instruction": step["instruction"],
            "input_bindings": step.get("input_bindings", {}),
            "output_schema_ref": step.get("output_schema_ref", body.get("output_schema_ref", "")),
            "retry_policy": step.get("retry_policy", {"max_attempts": 1}),
            "checkpoint_enabled": bool(step.get("checkpoint_policy", True)),
        }
        for step in steps
    ]
    canonical = json.dumps({"steps": compiled, "policy": body.get("execution_policy", {})}, sort_keys=True, separators=(",", ":"))
    return {
        "valid": True,
        "step_count": len(compiled),
        "provider_ref": body["execution_policy"]["provider_ref"],
        "compiled_steps": compiled,
        "plan_hash": hashlib.sha256(canonical.encode()).hexdigest(),
        "execution_boundary": "runtime_and_approved_tool_broker_only",
    }
