"""Multi-agent graph validation; scheduling remains entirely in WF-007."""

from __future__ import annotations
from typing import Any
from uuid import UUID
from sqlalchemy.orm import Session, sessionmaker
from src.core.exceptions import ValidationError_
from src.infra.db.models import AgentRevisionModel
from src.infra.db.session import get_session_factory
from src.domain.agent.agent_service import validate_agent


class MultiAgentOrchestrator:
    """Compile only explicit pinned Agent nodes; never adds an agent loop."""

    def __init__(self, factory: sessionmaker[Session] | None = None) -> None:
        self._factory = factory or get_session_factory()

    def validate_graph(self, graph: dict[str, Any]) -> dict[str, Any]:
        nodes = graph.get("nodes", [])
        edges = graph.get("edges", [])
        if not isinstance(nodes, list) or not isinstance(edges, list):
            raise ValidationError_("Multi-agent graph must have nodes and edges")
        agent_nodes = [
            node
            for node in nodes
            if isinstance(node, dict)
            and node.get("type") in {"agent_invoke", "agent.invoke"}
        ]
        ids = {
            str(node.get("id"))
            for node in nodes
            if isinstance(node, dict) and node.get("id")
        }
        with self._factory() as s:
            for index, node in enumerate(agent_nodes):
                revision = node.get("agent_revision_id") or (
                    node.get("data") or {}
                ).get("agent_revision_id")
                try:
                    revision_id = UUID(str(revision))
                except (ValueError, TypeError):
                    raise ValidationError_(
                        "Agent node requires fixed agent_revision_id",
                        details={"field": f"nodes[{index}].agent_revision_id"},
                    )
                row = s.get(AgentRevisionModel, revision_id)
                if row is None or row.status != "active":
                    raise ValidationError_(
                        "Agent node references inactive or unknown revision",
                        details={"field": f"nodes[{index}].agent_revision_id"},
                    )
                validate_agent(dict(row.body or {}))
            for index, edge in enumerate(edges):
                if (
                    not isinstance(edge, dict)
                    or str(edge.get("source")) not in ids
                    or str(edge.get("target")) not in ids
                ):
                    raise ValidationError_(
                        "Multi-agent edge must join explicit nodes",
                        details={"field": f"edges[{index}]"},
                    )
                if edge.get("implicit_memory") or edge.get("latest"):
                    raise ValidationError_(
                        "Multi-agent edges cannot use implicit memory or latest",
                        details={"field": f"edges[{index}]"},
                    )
        return {
            "valid": True,
            "agent_node_count": len(agent_nodes),
            "scheduler": "wf_007",
            "control_flow": "explicit_graph_only",
        }
