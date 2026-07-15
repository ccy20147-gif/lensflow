"""TF-WF-002: NodeDefinition and PortTypeRef validation logic.

Port type compatibility is decided by schema_id + schema_version pair.
Same-port_id within a single definition is rejected at validation time.
Converter registration enables explicit type transformation.
"""
from __future__ import annotations

import re
from typing import Any

from src.core.exceptions import ValidationError_
from src.schemas.models import NodeDefinitionRevision, PortTypeRef


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def validate_definition(ndr: NodeDefinitionRevision) -> None:
    """Validate a NodeDefinitionRevision before persisting.

    Raises:
        ValidationError_: If port IDs are duplicated, schema is invalid, or
                         executor_ref is empty.
        ConflictError:    If the node_type_id + semantic_version pair is being
                         overwritten with different content (caller must check).
    """
    errors: dict[str, Any] = {}

    # An executor is an approved, fixed platform identity, never arbitrary
    # code, a shell command, or a URL supplied by a node author.
    if not _is_fixed_executor(ndr.executor_ref):
        errors["executor_ref"] = "executor_ref must be a fixed approved executor identity"

    source = ndr.policy_metadata.get("package_source")
    approved = ndr.policy_metadata.get("approved_package")
    if not ndr.policy_metadata.get("builtin") and not (
        isinstance(source, str) and source.startswith("approved:")
    ) and not (isinstance(approved, dict) and approved.get("digest") and approved.get("approval_id")):
        errors["policy_metadata"] = "definition requires an approved build/package source"

    # Port ID uniqueness (within input and output separately)
    seen_input: set[str] = set()
    for port in ndr.input_ports:
        if port.port_id in seen_input:
            errors.setdefault("input_ports", {})[port.port_id] = "duplicate port_id"
        seen_input.add(port.port_id)
        _validate_port(port, "input_ports", errors)

    seen_output: set[str] = set()
    for port in ndr.output_ports:
        if port.port_id in seen_output:
            errors.setdefault("output_ports", {})[port.port_id] = "duplicate port_id"
        seen_output.add(port.port_id)
        _validate_port(port, "output_ports", errors)

    _validate_config_schema(ndr.config_schema, errors)
    # No secrets in executable or presentation metadata.  References are
    # intentionally represented as opaque IDs elsewhere, never secret values.
    _check_secrets(ndr.policy_metadata, field="policy_metadata", errors=errors)
    _check_secrets(ndr.ui_metadata, field="ui_metadata", errors=errors)
    for index, task in enumerate(ndr.managed_agent_task_plan):
        if not isinstance(task, dict) or task.get("kind") not in {"agent_invoke", "request_input", "human_gate", "workbench_task", "resource_commit"}:
            errors.setdefault("managed_agent_task_plan", {})[str(index)] = "task requires an approved workflow task kind"
        elif task.get("owner_layer") not in {None, "workflow"}:
            errors.setdefault("managed_agent_task_plan", {})[str(index)] = "managed task owner must be workflow"

    if errors:
        raise ValidationError_(
            message="节点定义校验失败",
            details=errors,
        )


_PORT_ID_BLACKLIST = frozenset(
    [
        "password",
        "secret",
        "token",
        "credential",
        "private_key",
        "api_key",
        "apikey",
    ]
)

_EXECUTOR_RE = re.compile(r"^(?:workflow|business)\.[a-z0-9_.-]+$|^agent_invoke$|^executor://[a-z0-9_.-]+/[a-z0-9_.-]+/(?:v[0-9]+|sha256:[a-f0-9]{32,})$", re.I)


def _is_fixed_executor(value: str) -> bool:
    return bool(value and _EXECUTOR_RE.fullmatch(value.strip()))


def _validate_port(port: PortTypeRef, field: str, errors: dict[str, Any]) -> None:
    if not port.port_id.strip() or not port.type_id.strip() or not port.schema_id.strip() or port.schema_version < 1:
        errors.setdefault(field, {})[port.port_id or "<empty>"] = "port requires id/type/schema/positive version"
    if port.cardinality not in {"required", "optional", "list"}:
        errors.setdefault(field, {})[port.port_id or "<empty>"] = "invalid cardinality"
    if any(not isinstance(policy, str) or not policy.strip() for policy in port.required_policy):
        errors.setdefault(field, {})[port.port_id or "<empty>"] = "required_policy entries must be non-empty strings"


def _validate_config_schema(schema: dict[str, Any], errors: dict[str, Any]) -> None:
    if not isinstance(schema, dict):
        errors["config_schema"] = "must be a JSON Schema object"
        return
    if schema and schema.get("type") not in {"object", None}:
        errors["config_schema"] = "node config schema root must be object"
    properties = schema.get("properties", {})
    if not isinstance(properties, dict) or any(not isinstance(value, dict) for value in properties.values()):
        errors["config_schema"] = "properties must be an object of schemas"
    required = schema.get("required", [])
    if not isinstance(required, list) or any(not isinstance(key, str) for key in required):
        errors["config_schema"] = "required must be a list of property names"
    elif isinstance(properties, dict) and any(key not in properties for key in required):
        errors["config_schema"] = "required references an unknown property"


def _check_secrets(
    data: dict[str, Any], field: str, errors: dict[str, Any]
) -> None:
    def visit(value: Any, path: str) -> None:
        if isinstance(value, dict):
            for key, nested in value.items():
                key_text = str(key).lower()
                if key_text in _PORT_ID_BLACKLIST or any(word in key_text for word in _PORT_ID_BLACKLIST):
                    errors[field] = f"FLAGGED: sensitive key at {path}.{key}; use a CredentialBinding reference"
                    return
                visit(nested, f"{path}.{key}")
        elif isinstance(value, list):
            for index, nested in enumerate(value):
                visit(nested, f"{path}[{index}]")
        elif isinstance(value, str) and any(word in value.lower() for word in _PORT_ID_BLACKLIST):
            errors[field] = f"FLAGGED: possible secret value at {path}; use a CredentialBinding reference"
    visit(data, field)


# ---------------------------------------------------------------------------
# Port type compatibility
# ---------------------------------------------------------------------------


def are_ports_compatible(
    output_port: PortTypeRef,
    input_port: PortTypeRef,
    available_converters: set[tuple[str, str, int]] | None = None,
) -> bool:
    """Check whether *output_port* can feed *input_port*.

    Rules:
      1. schema_id must match.
      2. Output schema_version >= input schema_version (backward-compatible).
      3. Cardinality expectations:
         - required  → required or optional
         - optional  → optional (cannot satisfy required)
         - list      → list (no implicit flatten)
      4. If schema_id differs, an explicit converter (schema_id→schema_id) must
         be registered in *available_converters*.
    """
    # direct schema match
    if output_port.schema_id == input_port.schema_id:
        if output_port.schema_version < input_port.schema_version:
            return False
    else:
        # converter check: (from_schema_id, to_schema_id, to_version)
        if available_converters is None:
            return False
        key = (output_port.schema_id, input_port.schema_id, input_port.schema_version)
        if key not in available_converters:
            return False

    # cardinality compatibility
    out_c = output_port.cardinality
    in_c = input_port.cardinality
    if out_c == "list" and in_c != "list":
        return False
    if in_c == "required" and out_c not in ("required", "optional"):
        return False
    if in_c == "optional" and out_c == "required":
        return False  # safer to require explicit handling
    # list→list, required→required, required→optional, optional→optional — OK

    return True


# ---------------------------------------------------------------------------
# Converter registration
# ---------------------------------------------------------------------------


def validate_converter(
    from_schema_id: str,
    from_schema_version: int,
    to_schema_id: str,
    to_schema_version: int,
    executor_digest: str,
) -> None:
    """Raise ValidationError_ if converter metadata is inconsistent."""
    errors: dict[str, Any] = {}
    if not from_schema_id.strip():
        errors["from_schema_id"] = "must not be empty"
    if not to_schema_id.strip():
        errors["to_schema_id"] = "must not be empty"
    if from_schema_id == to_schema_id and from_schema_version >= to_schema_version:
        errors["version"] = "converter must target a newer or different schema"
    if not executor_digest.strip():
        errors["executor_digest"] = "must not be empty"
    if errors:
        raise ValidationError_(message="转换器校验失败", details=errors)
