"""TF-WF-002: Contract tests for the Registry Service.

Tests cover:
  - NodeDefinitionRevision CRUD
  - Port type compatibility checking
  - Converter management
  - RegistrySnapshot generation
  - Conflict/error scenarios
"""
from __future__ import annotations

import pytest
from uuid import uuid4

from src.core.exceptions import ConflictError, NotFoundError, ValidationError_
from src.schemas.models import (
    NodeDefinitionRevision,
    PortTypeRef,
    RegistrySnapshot,
)
from src.domain.workflow.registry_service import RegistryService
from src.domain.workflow.node_definition import (
    are_ports_compatible,
    validate_definition,
    validate_converter,
)


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture
def registry() -> RegistryService:
    return RegistryService()


@pytest.fixture
def sample_port() -> PortTypeRef:
    return PortTypeRef(
        port_id="input_image",
        type_id="image",
        schema_id="toonflow.image.v1",
        schema_version=1,
        cardinality="required",
    )


@pytest.fixture
def sample_definition() -> NodeDefinitionRevision:
    return NodeDefinitionRevision(
        node_type_id="toonflow.image_loader",
        revision_id=uuid4(),
        semantic_version="1.0.0",
        input_ports=[
            PortTypeRef(
                port_id="path",
                type_id="string",
                schema_id="toonflow.path.v1",
                schema_version=1,
                cardinality="required",
            ),
        ],
        output_ports=[
            PortTypeRef(
                port_id="image_out",
                type_id="image",
                schema_id="toonflow.image.v1",
                schema_version=1,
                cardinality="required",
            ),
        ],
        config_schema={"type": "object", "properties": {"format": {"type": "string"}}},
        executor_ref="executor://toonflow/image-loader/v1",
        policy_metadata={"cost_estimate": "low", "package_source": "approved:test-registry"},
        ui_metadata={"display_name": "图片加载器", "icon": "image"},
    )


# ------------------------------------------------------------------
# Test: validate_definition
# ------------------------------------------------------------------


class TestValidateDefinition:
    def test_valid_definition(self, sample_definition: NodeDefinitionRevision) -> None:
        # Should not raise
        validate_definition(sample_definition)

    def test_empty_executor_ref(self, sample_definition: NodeDefinitionRevision) -> None:
        sample_definition.executor_ref = ""
        with pytest.raises(ValidationError_) as exc:
            validate_definition(sample_definition)
        assert "executor_ref" in exc.value.details

    def test_duplicate_port_ids(self, sample_definition: NodeDefinitionRevision) -> None:
        sample_definition.input_ports = [
            PortTypeRef(
                port_id="same_id",
                type_id="string",
                schema_id="schema.v1",
                schema_version=1,
                cardinality="required",
            ),
            PortTypeRef(
                port_id="same_id",
                type_id="string",
                schema_id="schema.v1",
                schema_version=1,
                cardinality="optional",
            ),
        ]
        with pytest.raises(ValidationError_) as exc:
            validate_definition(sample_definition)
        assert "input_ports" in exc.value.details

    def test_rejects_nested_secret_and_unapproved_executor(self, sample_definition: NodeDefinitionRevision) -> None:
        sample_definition.policy_metadata["nested"] = {"api_key": "abc"}
        with pytest.raises(ValidationError_) as exc:
            validate_definition(sample_definition)
        assert "policy_metadata" in exc.value.details
        sample_definition.policy_metadata = {"package_source": "approved:test"}
        sample_definition.executor_ref = "python:arbitrary_user_code"
        with pytest.raises(ValidationError_) as exc:
            validate_definition(sample_definition)
        assert "executor_ref" in exc.value.details

    def test_rejects_invalid_config_schema_and_unapproved_source(self, sample_definition: NodeDefinitionRevision) -> None:
        sample_definition.config_schema = {"type": "array"}
        with pytest.raises(ValidationError_) as exc:
            validate_definition(sample_definition)
        assert "config_schema" in exc.value.details
        sample_definition.config_schema = {"type": "object"}
        sample_definition.policy_metadata = {}
        with pytest.raises(ValidationError_) as exc:
            validate_definition(sample_definition)
        assert "policy_metadata" in exc.value.details


# ------------------------------------------------------------------
# Test: are_ports_compatible
# ------------------------------------------------------------------


class TestPortCompatibility:
    def test_same_schema_compatible(self) -> None:
        out = PortTypeRef(port_id="out", type_id="img", schema_id="img.v1", schema_version=2, cardinality="required")
        inp = PortTypeRef(port_id="in", type_id="img", schema_id="img.v1", schema_version=1, cardinality="required")
        assert are_ports_compatible(out, inp) is True

    def test_version_mismatch(self) -> None:
        out = PortTypeRef(port_id="out", type_id="img", schema_id="img.v1", schema_version=1, cardinality="required")
        inp = PortTypeRef(port_id="in", type_id="img", schema_id="img.v1", schema_version=2, cardinality="required")
        assert are_ports_compatible(out, inp) is False

    def test_different_schema_with_converter(self) -> None:
        out = PortTypeRef(port_id="out", type_id="a", schema_id="a.v1", schema_version=1, cardinality="required")
        inp = PortTypeRef(port_id="in", type_id="b", schema_id="b.v1", schema_version=1, cardinality="required")
        converters = {("a.v1", "b.v1", 1)}
        assert are_ports_compatible(out, inp, converters) is True

    def test_different_schema_without_converter(self) -> None:
        out = PortTypeRef(port_id="out", type_id="a", schema_id="a.v1", schema_version=1, cardinality="required")
        inp = PortTypeRef(port_id="in", type_id="b", schema_id="b.v1", schema_version=1, cardinality="required")
        assert are_ports_compatible(out, inp) is False

    def test_list_to_required_incompatible(self) -> None:
        out = PortTypeRef(port_id="out", type_id="x", schema_id="x.v1", schema_version=1, cardinality="list")
        inp = PortTypeRef(port_id="in", type_id="x", schema_id="x.v1", schema_version=1, cardinality="required")
        assert are_ports_compatible(out, inp) is False


# ------------------------------------------------------------------
# Test: Converter validation
# ------------------------------------------------------------------


class TestValidateConverter:
    def test_valid_converter(self) -> None:
        validate_converter("a.v1", 1, "b.v1", 1, "digest:abc123")

    def test_empty_from_schema(self) -> None:
        with pytest.raises(ValidationError_):
            validate_converter("", 1, "b.v1", 1, "digest:abc")

    def test_same_schema_version_no_upgrade(self) -> None:
        with pytest.raises(ValidationError_):
            validate_converter("a.v1", 2, "a.v1", 1, "digest:abc")

    def test_empty_executor_digest(self) -> None:
        with pytest.raises(ValidationError_):
            validate_converter("a.v1", 1, "b.v1", 1, "")


# ------------------------------------------------------------------
# Test: RegistryService
# ------------------------------------------------------------------


class TestRegistryService:
    def test_register_and_get_definition(self, registry: RegistryService, sample_definition: NodeDefinitionRevision) -> None:
        registry.register_definition(sample_definition)
        found = registry.get_definition(sample_definition.node_type_id)
        assert found is not None
        assert found.revision_id == sample_definition.revision_id

    def test_activate_definition(self, registry: RegistryService, sample_definition: NodeDefinitionRevision) -> None:
        registry.register_definition(sample_definition)
        activated = registry.activate_definition(sample_definition.node_type_id, sample_definition.revision_id)
        assert activated.node_type_id == sample_definition.node_type_id

        # Now it should be active
        active = registry.get_definition(sample_definition.node_type_id)
        assert active is not None

    def test_retire_definition(self, registry: RegistryService, sample_definition: NodeDefinitionRevision) -> None:
        registry.register_definition(sample_definition)
        registry.activate_definition(sample_definition.node_type_id, sample_definition.revision_id)
        retired = registry.retire_definition(sample_definition.node_type_id, sample_definition.revision_id)
        assert retired.node_type_id == sample_definition.node_type_id

        # Active should be None
        assert registry.get_definition(sample_definition.node_type_id) is None

    def test_duplicate_version_conflict(self, registry: RegistryService, sample_definition: NodeDefinitionRevision) -> None:
        registry.register_definition(sample_definition)
        dup = sample_definition.model_copy()
        dup.revision_id = uuid4()
        with pytest.raises(ConflictError):
            registry.register_definition(dup)

    def test_register_converter_and_list(self, registry: RegistryService) -> None:
        registry.register_converter("a.v1", 1, "b.v1", 1, "digest:conv1")
        converters = registry.list_converters()
        assert "a.v1→b.v1@v1" in converters

    def test_converter_port_compatibility(self, registry: RegistryService) -> None:
        registry.register_converter("a.v1", 1, "b.v1", 1, "digest:conv1")
        out = PortTypeRef(port_id="out", type_id="a", schema_id="a.v1", schema_version=1, cardinality="required")
        inp = PortTypeRef(port_id="in", type_id="b", schema_id="b.v1", schema_version=1, cardinality="required")
        assert registry.check_port_compatibility(out, inp) is True

    def test_generate_snapshot(self, registry: RegistryService, sample_definition: NodeDefinitionRevision) -> None:
        registry.register_definition(sample_definition)
        registry.activate_definition(sample_definition.node_type_id, sample_definition.revision_id)
        snap = registry.generate_snapshot()
        assert isinstance(snap, RegistrySnapshot)
        assert snap.snapshot_id is not None
        assert sample_definition.node_type_id in snap.node_definitions
        assert len(snap.schema_hash) == 64  # SHA-256 hex

    def test_get_snapshot(self, registry: RegistryService, sample_definition: NodeDefinitionRevision) -> None:
        registry.register_definition(sample_definition)
        registry.activate_definition(sample_definition.node_type_id, sample_definition.revision_id)
        snap = registry.generate_snapshot()
        fetched = registry.get_snapshot(snap.snapshot_id)
        assert fetched.snapshot_id == snap.snapshot_id

    def test_get_snapshot_not_found(self, registry: RegistryService) -> None:
        with pytest.raises(NotFoundError):
            registry.get_snapshot(uuid4())

    def test_list_definitions_filter_by_status(self, registry: RegistryService, sample_definition: NodeDefinitionRevision) -> None:
        registry.register_definition(sample_definition)
        registry.activate_definition(sample_definition.node_type_id, sample_definition.revision_id)
        drafts = registry.list_definitions(status="draft")
        assert len(drafts) == 0
        active = registry.list_definitions(status="active")
        assert len(active) >= 1

    def test_query_by_version(self, registry: RegistryService, sample_definition: NodeDefinitionRevision) -> None:
        registry.register_definition(sample_definition)
        found = registry.query_definition_by_version(sample_definition.node_type_id, "1.0.0")
        assert found is not None
        assert found.revision_id == sample_definition.revision_id

    def test_query_by_version_not_found(self, registry: RegistryService) -> None:
        assert registry.query_definition_by_version("nonexistent", "1.0.0") is None
