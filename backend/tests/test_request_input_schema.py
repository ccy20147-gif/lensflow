"""Typed RequestInput answer validation contracts (TF-WF-008)."""
from __future__ import annotations

import pytest

from src.core.exceptions import ValidationError_
from src.domain.agent.request_input import _validate_typed_answer


SCHEMA = {
    "type": "object", "required": ["choice", "notes"], "max_response_bytes": 80,
    "properties": {
        "choice": {"type": "string", "enum": ["approve", "revise"]},
        "notes": {"type": "string"},
        "count": {"type": "integer"},
    },
}


def test_request_input_accepts_schema_valid_typed_answer() -> None:
    _validate_typed_answer({"choice": "approve", "notes": "ok", "count": 2}, SCHEMA, 80)


@pytest.mark.parametrize("answer", [
    {"choice": "approve"},
    {"choice": "other", "notes": "x"},
    {"choice": "approve", "notes": "x", "count": "2"},
])
def test_request_input_rejects_missing_invalid_or_wrong_typed_answer(answer: dict) -> None:
    with pytest.raises(ValidationError_):
        _validate_typed_answer(answer, SCHEMA, 80)


def test_request_input_rejects_oversized_answer() -> None:
    with pytest.raises(ValidationError_):
        _validate_typed_answer({"choice": "approve", "notes": "x" * 100}, SCHEMA, 80)
