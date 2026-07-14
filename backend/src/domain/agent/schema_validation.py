"""Small fail-closed JSON Schema subset used at provider publication.

The workflow registry currently stores schema identity/version on ports but
does not expose an executable JSON Schema store.  Agent revisions therefore
freeze the concrete output schema alongside that identity.  This validator is
deliberately narrow: unsupported JSON-Schema features fail validation rather
than silently accepting provider output.
"""
from __future__ import annotations

from typing import Any

from src.core.exceptions import ValidationError_


def validate_json_schema(value: Any, schema: dict[str, Any], *, path: str = "$") -> None:
    if not isinstance(schema, dict):
        raise ValidationError_("Frozen output_schema must be an object")
    expected = schema.get("type")
    if expected == "object":
        if not isinstance(value, dict):
            raise ValidationError_(f"Provider output at {path} must be an object")
        properties = schema.get("properties", {})
        if not isinstance(properties, dict):
            raise ValidationError_("output_schema.properties must be an object")
        required = schema.get("required", [])
        if not isinstance(required, list) or any(not isinstance(item, str) for item in required):
            raise ValidationError_("output_schema.required must be a string list")
        for key in required:
            if key not in value:
                raise ValidationError_(f"Provider output missing required field {path}.{key}")
        if schema.get("additionalProperties") is False:
            unknown = set(value) - set(properties)
            if unknown:
                raise ValidationError_(f"Provider output has unknown fields at {path}: {sorted(unknown)}")
        for key, child in properties.items():
            if key in value:
                validate_json_schema(value[key], child, path=f"{path}.{key}")
        return
    if expected == "array":
        if not isinstance(value, list):
            raise ValidationError_(f"Provider output at {path} must be an array")
        items = schema.get("items")
        if items is not None:
            for index, item in enumerate(value):
                validate_json_schema(item, items, path=f"{path}[{index}]")
        return
    if expected == "string" and not isinstance(value, str):
        raise ValidationError_(f"Provider output at {path} must be a string")
    if expected == "number" and (not isinstance(value, (int, float)) or isinstance(value, bool)):
        raise ValidationError_(f"Provider output at {path} must be a number")
    if expected == "integer" and (not isinstance(value, int) or isinstance(value, bool)):
        raise ValidationError_(f"Provider output at {path} must be an integer")
    if expected == "boolean" and not isinstance(value, bool):
        raise ValidationError_(f"Provider output at {path} must be a boolean")
    if expected is None:
        raise ValidationError_("Frozen output_schema must declare a supported type")
    if expected not in {"object", "array", "string", "number", "integer", "boolean"}:
        raise ValidationError_(f"Unsupported frozen output_schema type {expected}")

