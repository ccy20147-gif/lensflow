"""
ToonFlow Backend — Repository Pattern (abstract interfaces + in-memory default)
"""
from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from typing import Any, Generic, TypeVar

from sqlalchemy import select
from sqlalchemy.orm import Session

T = TypeVar("T")


class Repository(ABC, Generic[T]):
    """Abstract repository with CRUD operations."""

    @abstractmethod
    def save(self, entity: T) -> T:
        ...

    @abstractmethod
    def get(self, entity_id: uuid.UUID) -> T | None:
        ...

    @abstractmethod
    def delete(self, entity_id: uuid.UUID) -> None:
        ...

    @abstractmethod
    def list(self, **filters) -> list[T]:
        ...


class UnitOfWork(ABC):
    """Unit of Work for transaction management."""

    @abstractmethod
    def begin(self) -> None:
        ...

    @abstractmethod
    def commit(self) -> None:
        ...

    @abstractmethod
    def rollback(self) -> None:
        ...

    @property
    @abstractmethod
    def is_active(self) -> bool:
        ...


class InMemoryRepository(Repository[T]):
    """In-memory default repository (prototype)."""

    def __init__(self):
        self._store: dict[uuid.UUID, T] = {}

    def save(self, entity: T) -> T:
        entity_id = getattr(entity, "id", None)
        if entity_id is None:
            for name in ("workflow_id", "revision_id", "account_id", "project_id"):
                entity_id = getattr(entity, name, None)
                if entity_id is not None:
                    break
        if entity_id is None:
            raise ValueError("InMemoryRepository entity must expose an id")
        self._store[entity_id] = entity
        return entity

    def get(self, entity_id: uuid.UUID) -> T | None:
        return self._store.get(entity_id)

    def delete(self, entity_id: uuid.UUID) -> None:
        self._store.pop(entity_id, None)

    def list(self, **filters) -> list[T]:
        results = list(self._store.values())
        for key, value in filters.items():
            results = [r for r in results if getattr(r, key, None) == value]
        return results


class InMemoryUnitOfWork(UnitOfWork):
    """In-memory UoW — no real transaction, tracks active state."""

    def __init__(self):
        self._active = False

    def begin(self) -> None:
        self._active = True

    def commit(self) -> None:
        self._active = False

    def rollback(self) -> None:
        self._active = False

    @property
    def is_active(self) -> bool:
        return self._active


class SqlAlchemyRepository(Repository[T], Generic[T]):
    """Small synchronous SQLAlchemy repository for ORM entities.

    Transaction ownership stays with ``SqlAlchemyUnitOfWork``; callers must
    commit the surrounding unit of work explicitly.
    """

    def __init__(self, session: Session, model_type: type[T], id_column: Any):
        self._session = session
        self._model_type = model_type
        self._id_column = id_column

    def save(self, entity: T) -> T:
        return self._session.merge(entity)

    def get(self, entity_id: uuid.UUID) -> T | None:
        return self._session.get(self._model_type, entity_id)

    def delete(self, entity_id: uuid.UUID) -> None:
        entity = self.get(entity_id)
        if entity is not None:
            self._session.delete(entity)

    def list(self, **filters: Any) -> list[T]:
        statement = select(self._model_type).filter_by(**filters)
        return list(self._session.scalars(statement))
