"""TF-ASR-001: Media Recipe domain service.

Validates Media Recipe definitions with step-based operator graphs.
"""
from __future__ import annotations

from src.core.exceptions import ValidationError_


def validate_recipe(body: dict) -> None:
    """Static validation: requires at least one MediaStep, valid type field."""
    operator_graph = body.get("operator_graph", {})
    steps = body.get("steps", {})

    if not operator_graph and not steps:
        raise ValidationError_(
            message="MediaRecipe requires at least one MediaStep in operator_graph",
            details={"field": "operator_graph"},
        )

    recipe_type = body.get("recipe_type", "")
    if not recipe_type:
        raise ValidationError_(
            message="MediaRecipe requires a valid recipe_type field",
            details={"field": "recipe_type"},
        )

    graph = operator_graph or steps
    if isinstance(graph, dict):
        for key, node in graph.items():
            if isinstance(node, dict) and not node.get("type", ""):
                raise ValidationError_(
                    message=f"MediaStep '{key}' is missing a type field",
                    details={"field": f"operator_graph.{key}.type"},
                )
    elif isinstance(graph, list):
        for i, node in enumerate(graph):
            if isinstance(node, dict) and not node.get("type", ""):
                raise ValidationError_(
                    message=f"MediaStep at index {i} is missing a type field",
                    details={"field": f"operator_graph[{i}].type"},
                )
