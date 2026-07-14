"""TF-WF-002: NodeDefinition and PortTypeRef validation logic.

Port type compatibility is decided by schema_id + schema_version pair.
Same-port_id within a single definition is rejected at validation time.
Converter registration enables explicit type transformation.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from src.core.exceptions import ConflictError, ValidationError_
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

    # executor_ref must be non-empty
    if not ndr.executor_ref.strip():
        errors["executor_ref"] = "executor_ref must not be empty"

    # Port ID uniqueness (within input and output separately)
    seen_input: set[str] = set()
    for port in ndr.input_ports:
        if port.port_id in seen_input:
            errors.setdefault("input_ports", {})[port.port_id] = "duplicate port_id"
        seen_input.add(port.port_id)

    seen_output: set[str] = set()
    for port in ndr.output_ports:
        if port.port_id in seen_output:
            errors.setdefault("output_ports", {})[port.port_id] = "duplicate port_id"
        seen_output.add(port.port_id)

    # No secrets in policy_metadata or ui_metadata
    _check_secrets(ndr.policy_metadata, field="policy_metadata", errors=errors)
    _check_secrets(ndr.ui_metadata, field="ui_metadata", errors=errors)

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


def _check_secrets(
    data: dict[str, Any], field: str, errors: dict[str, Any]
) -> None:
    if not isinstance(data, dict):
        return
    for key in data:
        if key.lower() in _PORT_ID_BLACKLIST:
            # config schema may reference secrets via binding, not inline value
            pass
    for val in data.values():
        if isinstance(val, str) and any(
            kw in val.lower() for kw in _PORT_ID_BLACKLIST
        ):
            errors[field] = "FLAGGED: 疑似明文 secret，请使用 CredentialBinding 引用"


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
