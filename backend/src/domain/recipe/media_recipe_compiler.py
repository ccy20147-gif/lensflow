"""Finite, revision-freezing compiler for Media Recipe operator DAGs."""
from __future__ import annotations

import hashlib
import json

from src.core.exceptions import PolicyBlockedError, ValidationError_

ALLOWED_OPERATORS = frozenset({
    "atlas_llm", "atlas_image", "atlas_video", "input", "format_convert",
    "resize", "crop", "score", "branch", "merge", "video_loader", "image_loader",
    "audio_loader", "resize_filter", "color_convert", "frame_extract",
})
FORBIDDEN_OPERATORS = frozenset({"agent", "workflow", "recipe", "human_gate", "request_input", "code", "shell", "http"})


def _hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _nodes(body: dict) -> dict[str, dict]:
    graph = body.get("operator_graph", body.get("steps", {}))
    if isinstance(graph, list):
        graph = {str(item.get("id", index)): item for index, item in enumerate(graph) if isinstance(item, dict)}
    if not isinstance(graph, dict) or not graph:
        raise ValidationError_("MediaRecipe requires a non-empty operator_graph")
    return {str(key): value for key, value in graph.items() if isinstance(value, dict)}


def compile_media_recipe(body: dict) -> dict:
    nodes = _nodes(body)
    if len(nodes) > 64:
        raise ValidationError_("MediaRecipe operator limit exceeded")
    dependencies: dict[str, set[str]] = {node_id: set() for node_id in nodes}
    frozen_steps: list[dict] = []
    controls: list[dict] = []
    for node_id, node in nodes.items():
        kind = str(node.get("type", ""))
        if kind in FORBIDDEN_OPERATORS or kind not in ALLOWED_OPERATORS:
            raise PolicyBlockedError(f"Recipe operator '{kind}' is not allowed")
        inputs = node.get("inputs", [])
        if not isinstance(inputs, list):
            raise ValidationError_(f"Operator '{node_id}' inputs must be a list")
        for source in inputs:
            source_id = source.get("node") if isinstance(source, dict) else source.split(".", 1)[0] if isinstance(source, str) else None
            if source_id and source_id in nodes:
                dependencies[node_id].add(str(source_id))
        required = node.get("required_controls", [])
        supported = set(node.get("supported_controls", required))
        policy = node.get("unsupported_policy", "block")
        for control in required:
            outcome = "applied" if control in supported else ("degraded" if policy == "degrade" else "blocked")
            controls.append({"operator_id": node_id, "control": control, "outcome": outcome})
            if outcome == "blocked":
                raise PolicyBlockedError(f"AtlasCloud capability does not support control '{control}'")
        frozen_steps.append({"id": node_id, "operator": kind, "inputs": inputs,
                             "outputs": node.get("outputs", []), "parameters": node.get("parameters", {}),
                             "operator_revision": str(node.get("operator_revision", "v1")),
                             "model_id": str(node.get("model_id", "")), "capability_snapshot": {"provider": "atlascloud", "controls": sorted(supported)}})
    # Preserve the full predecessor set for the durable runtime.  Kahn mutates
    # ``dependencies`` while sorting; a topological index is not a dependency
    # contract for diamond DAGs.
    frozen_dependencies = {node_id: sorted(values) for node_id, values in dependencies.items()}
    # Kahn topological sort makes cycles impossible to submit.
    order: list[str] = []
    ready = sorted(key for key, deps in dependencies.items() if not deps)
    while ready:
        current = ready.pop(0)
        order.append(current)
        for target, deps in dependencies.items():
            if current in deps:
                deps.remove(current)
                if not deps:
                    ready.append(target)
                    ready.sort()
    if len(order) != len(nodes):
        raise ValidationError_("MediaRecipe operator_graph must be acyclic")
    ordered = [{**next(step for step in frozen_steps if step["id"] == node_id), "depends_on": frozen_dependencies[node_id]} for node_id in order]
    plan = {"provider": "atlascloud", "recipe_type": body.get("recipe_type", ""), "steps": ordered,
            "public_inputs": body.get("public_input_schema_refs", []), "public_outputs": body.get("public_output_schema_refs", []),
            "parameter_schema": body.get("parameter_schema", {}), "control_outcomes": controls}
    return {"valid": True, "step_count": len(ordered), "recipe_type": body.get("recipe_type", ""),
            "steps": ordered, "warnings": [c for c in controls if c["outcome"] != "applied"],
            "compiled_plan": plan, "plan_hash": _hash(plan)}
