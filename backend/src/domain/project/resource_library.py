"""Resource Library — query/filter resources by type, name, time, source."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Protocol

from src.core.exceptions import CrossOwnerError
from src.schemas.models import OwnerScope, Resource


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


class ResourceRecord:
    """Internal resource storage record."""

    def __init__(
        self,
        resource_id: str,
        resource_type: str,
        owner_scope: OwnerScope,
        name: str = "",
        source: str = "",
        created_at: datetime | None = None,
        updated_at: datetime | None = None,
    ):
        self.resource_id = resource_id
        self.resource_type = resource_type
        self.owner_scope = owner_scope
        self.name = name
        self.source = source
        self.created_at = created_at or datetime.now(timezone.utc)
        self.updated_at = updated_at or datetime.now(timezone.utc)


class ResourceStore(Protocol):
    """Interface for resource persistence."""

    def save(self, record: ResourceRecord) -> None: ...
    def get_by_id(self, resource_id: str) -> ResourceRecord | None: ...
    def list_by_owner(self, owner_scope: OwnerScope) -> list[ResourceRecord]: ...
    def delete(self, resource_id: str) -> None: ...


class InMemoryResourceStore:
    """Thread-safe in-memory resource store."""

    def __init__(self) -> None:
        self._resources: dict[str, ResourceRecord] = {}

    def save(self, record: ResourceRecord) -> None:
        self._resources[record.resource_id] = record

    def get_by_id(self, resource_id: str) -> ResourceRecord | None:
        return self._resources.get(resource_id)

    def list_by_owner(self, owner_scope: OwnerScope) -> list[ResourceRecord]:
        key = owner_scope.scoped_id
        return [r for r in self._resources.values() if r.owner_scope.scoped_id == key]

    def delete(self, resource_id: str) -> None:
        self._resources.pop(resource_id, None)


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class ResourceLibrary:
    """Query and filter resources within an owner scope."""

    def __init__(self, store: ResourceStore | None = None) -> None:
        self._store = store or InMemoryResourceStore()

    def _to_resource(self, rec: ResourceRecord) -> Resource:
        return Resource(
            resource_id=uuid.UUID(rec.resource_id),
            resource_type=rec.resource_type,
            owner_scope=rec.owner_scope,
            created_at=rec.created_at,
        )

    def list_resources(
        self,
        owner_scope: OwnerScope,
        resource_type: str | None = None,
        name_query: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[Resource], int]:
        """List resources for an owner scope with optional filters (FR-4)."""
        records = self._store.list_by_owner(owner_scope)

        # Filter by type
        if resource_type is not None:
            records = [r for r in records if r.resource_type == resource_type]

        # Filter by name (case-insensitive substring)
        if name_query is not None:
            q = name_query.lower()
            records = [r for r in records if q in r.name.lower()]

        total = len(records)

        # Sort by updated_at desc
        records.sort(key=lambda r: r.updated_at, reverse=True)

        # Paginate
        sliced = records[offset:offset + limit]

        return [self._to_resource(r) for r in sliced], total

    def get_resource(
        self,
        resource_id: str,
        caller_owner: OwnerScope,
    ) -> Resource:
        """Get a single resource with cross-owner validation."""
        rec = self._store.get_by_id(resource_id)
        if rec is None:
            from src.core.exceptions import NotFoundError
            raise NotFoundError("Resource", resource_id)
        if rec.owner_scope.scoped_id != caller_owner.scoped_id:
            raise CrossOwnerError()
        return self._to_resource(rec)

    def create_resource(
        self,
        owner_scope: OwnerScope,
        resource_type: str,
        name: str = "",
        source: str = "",
    ) -> Resource:
        """Register a new resource."""
        resource_id = str(uuid.uuid4())
        record = ResourceRecord(
            resource_id=resource_id,
            resource_type=resource_type,
            owner_scope=owner_scope,
            name=name,
            source=source,
        )
        self._store.save(record)
        return self._to_resource(record)
