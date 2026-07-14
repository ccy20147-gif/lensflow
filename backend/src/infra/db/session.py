"""Synchronous SQLAlchemy engine, sessions, and transaction boundary.

The API and domain layer currently use synchronous services.  Keeping this
adapter synchronous avoids pretending an async transaction exists while still
providing a real PostgreSQL-backed Unit of Work.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from src.core.config import settings
from src.infra.db.repository import UnitOfWork


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    return create_engine(settings.database_url_sync, pool_pre_ping=True)


@lru_cache(maxsize=1)
def get_session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), autoflush=False, expire_on_commit=False)


class SqlAlchemyUnitOfWork(UnitOfWork):
    """Own one database transaction and expose its SQLAlchemy session."""

    def __init__(self, factory: sessionmaker[Session] | None = None) -> None:
        self._factory = factory or get_session_factory()
        self.session: Session | None = None
        self._active = False

    def begin(self) -> None:
        if self._active:
            raise RuntimeError("Unit of work is already active")
        self.session = self._factory()
        self.session.begin()
        self._active = True

    def commit(self) -> None:
        if self.session is None:
            raise RuntimeError("Unit of work has not started")
        self.session.commit()
        self._active = False
        self.session.close()
        self.session = None

    def rollback(self) -> None:
        if self.session is not None:
            self.session.rollback()
            self.session.close()
        self.session = None
        self._active = False

    @property
    def is_active(self) -> bool:
        return self._active

    def __enter__(self) -> "SqlAlchemyUnitOfWork":
        self.begin()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if exc_type is None:
            self.commit()
        else:
            self.rollback()


@contextmanager
def session_scope(factory: sessionmaker[Session] | None = None) -> Iterator[Session]:
    """Convenience transaction scope for infrastructure-facing code."""
    with SqlAlchemyUnitOfWork(factory) as uow:
        assert uow.session is not None
        yield uow.session
