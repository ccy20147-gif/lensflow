"""Validation for non-executable, revision-pinned Skills."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from src.core.exceptions import ValidationError_

_FORBIDDEN = re.compile(r"\b(?:tool(?:invocation)?|agent(?:_invoke)?|workflow|subworkflow|media[_ ]?recipe|credential|https?://|curl|wget|import\s+|exec\s*\(|<script)\b", re.I)
_INJECTION = re.compile(r"(?:ignore (?:all )?(?:previous|system)|reveal .*secret|disable .*safety)", re.I)


def _error(message: str, field: str, code: str = "SKILL_POLICY_BLOCKED") -> None:
    raise ValidationError_(message=message, details={"field": field, "code": code})


def _tokens(value: Any) -> int:
    return len(json.dumps(value, sort_keys=True, ensure_ascii=True).split())


def validate_skill(body: dict[str, Any]) -> None:
    instructions = body.get("instructions", [])
    if not isinstance(instructions, list) or not instructions:
        _error("Skill requires at least one non-executable instruction", "instructions")
    for field in ("tool_revision_refs", "agent_revision_refs", "provider_ref", "credential_binding"):
        if body.get(field):
            _error("Skill cannot declare executable capabilities", field)
    for field in ("instructions", "examples", "evaluation_notes"):
        value = body.get(field, [])
        if not isinstance(value, list):
            _error(f"{field} must be a list", field)
        for index, section in enumerate(value):
            text = json.dumps(section, ensure_ascii=True) if not isinstance(section, str) else section
            if _FORBIDDEN.search(text) or _INJECTION.search(text):
                _error("Skill contains executable, network, or policy-override content", f"{field}[{index}]")
    refs = body.get("knowledge_refs", [])
    if not isinstance(refs, list):
        _error("knowledge_refs must be a list", "knowledge_refs")
    for index, ref in enumerate(refs):
        if not isinstance(ref, dict):
            _error("knowledge ref must be a frozen ArtifactRef or ResourceRef", f"knowledge_refs[{index}]")
        # Artifact references are owner-bound; Resource refs must remain frozen.
        if "artifact_version_id" not in ref and not (ref.get("resource_id") and ref.get("revision_id")):
            _error("knowledge ref must pin a version", f"knowledge_refs[{index}]")
    if body.get("input_schema_ref") and body.get("input_schema_ref") == body.get("output_schema_ref"):
        _error("input_schema_ref and output_schema_ref must differ", "output_schema_ref", "SKILL_SCHEMA_CONFLICT")
    budget = body.get("max_assembly_tokens", body.get("assembly_policy", {}).get("max_tokens", 4096))
    if not isinstance(budget, int) or budget < 1 or budget > 65536:
        _error("max_assembly_tokens must be between 1 and 65536", "max_assembly_tokens")
    if _tokens(instructions) > budget:
        _error("required Skill instructions exceed assembly budget", "instructions", "SKILL_BUDGET_EXCEEDED")
    if not isinstance(body.get("language", "und"), str) or not body.get("language", "und").strip():
        _error("Skill language must be declared", "language")
    if body.get("safety_classification", "standard") not in {"standard", "sensitive", "restricted"}:
        _error("Skill safety_classification is invalid", "safety_classification")
    if body.get("assembly_tier", "explicit") not in {"platform", "managed", "step", "explicit"}:
        _error("Skill assembly_tier is invalid", "assembly_tier")
    required_context = body.get("required_context_schema", "")
    if required_context and (not isinstance(required_context, str) or len(required_context) > 255):
        _error("Skill required_context_schema is invalid", "required_context_schema")


def compile_skill(body: dict[str, Any]) -> dict[str, Any]:
    validate_skill(body)
    sections = []
    for name in ("instructions", "examples", "knowledge_refs"):
        value = body.get(name, [])
        if value:
            sections.append({"section": name, "content": value if name != "knowledge_refs" else "[pinned refs]", "tokens_estimate": _tokens(value)})
    canonical = json.dumps({"sections": sections, "priority": body.get("priority", 100)}, sort_keys=True, separators=(",", ":"))
    return {
        "valid": True,
        "resolved_sections": sections,
        "token_accounting": {"total_estimated_tokens": sum(s["tokens_estimate"] for s in sections), "max_tokens": body.get("max_assembly_tokens", 4096)},
        "conflicts": [],
        "security_decisions": ["non_executable", "frozen_knowledge_refs"],
        "final_context_hash": hashlib.sha256(canonical.encode()).hexdigest(),
    }
