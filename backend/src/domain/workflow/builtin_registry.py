"""The versioned public node baseline used by official workflow packages."""
from __future__ import annotations

from uuid import uuid4

from src.infra.db.registry_repository import SqlRegistryService
from src.schemas.models import NodeDefinitionRevision, PortTypeRef


PUBLIC_BUSINESS_NODE_TYPES = (
    "brief", "constraint", "structured_generate", "model_router", "variants",
    "select_rank", "review", "transform", "workbench_task", "package_export",
    "human_gate", "agent_invoke", "request_input", "resource_commit",
    "media_recipe_invoke",
    "condition", "join", "fallback", "map", "ordered_map", "fold", "subworkflow_call",
)


def ensure_public_business_node_baseline(registry: SqlRegistryService | None = None) -> None:
    """Activate the stable public-node revisions required by official packages.

    This is idempotent on semantic version and deliberately contains no model
    credential or provider value. A subsequently published workflow always
    freezes these concrete revision IDs into its own RegistrySnapshot.
    """
    registry = registry or SqlRegistryService()
    for type_id in PUBLIC_BUSINESS_NODE_TYPES:
        existing = registry.list_node_definitions(status="active", type_id=type_id)
        if existing:
            continue
        input_ports = [] if type_id == "brief" else [
            PortTypeRef(port_id="in", type_id="artifact", schema_id="workflow_payload", schema_version=1, cardinality="optional"),
        ]
        output_ports = [] if type_id == "package_export" else [
            PortTypeRef(port_id="out", type_id="artifact", schema_id="workflow_payload", schema_version=1, cardinality="optional"),
        ]
        definition = NodeDefinitionRevision(
            node_type_id=type_id,
            revision_id=uuid4(),
            semantic_version="1.0.0",
            input_ports=input_ports,
            output_ports=output_ports,
            config_schema={"type": "object"},
            executor_ref=f"workflow.business.{type_id}",
            policy_metadata={"visibility": "public", "builtin": True},
            ui_metadata={"label": type_id},
        )
        registered = registry.add_node_definition(definition)
        registry.activate_node_definition(type_id, registered.revision_id)
