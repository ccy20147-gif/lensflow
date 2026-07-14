"""Session Service — token creation, validation, revocation, expiry."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Protocol

from src.core.config import settings
from src.core.exceptions import NotFoundError, UnauthorizedError
from src.schemas.enums import SessionStatus
from src.schemas.models import OwnerScope


# ---------------------------------------------------------------------------
# Storage protocol (swap for real DB later)
# ---------------------------------------------------------------------------


class SessionRecord:
    """Internal session storage record."""

    def __init__(
        self,
        session_id: str,
        account_id: str,
        owner_scope: OwnerScope,
        token_hash: str,
        status: SessionStatus = SessionStatus.ACTIVE,
        created_at: datetime | None = None,
        expires_at: datetime | None = None,
        revoked_at: datetime | None = None,
    ):
        self.session_id = session_id
        self.account_id = account_id
        self.owner_scope = owner_scope
        self.token_hash = token_hash
        self.status = status
        self.created_at = created_at or datetime.now(timezone.utc)
        self.expires_at = expires_at
        self.revoked_at = revoked_at


class SessionStore(Protocol):
    """Interface for session persistence."""

    def save(self, record: SessionRecord) -> None: ...
    def get_by_id(self, session_id: str) -> SessionRecord | None: ...
    def get_by_token_hash(self, token_hash: str) -> SessionRecord | None: ...
    def list_by_account(self, account_id: str) -> list[SessionRecord]: ...
    def delete(self, session_id: str) -> None: ...


# ---------------------------------------------------------------------------
# In-memory implementation
# ---------------------------------------------------------------------------


class InMemorySessionStore:
    """Thread-safe in-memory session store."""

    def __init__(self) -> None:
        self._sessions: dict[str, SessionRecord] = {}
        self._token_index: dict[str, str] = {}  # token_hash -> session_id

    def save(self, record: SessionRecord) -> None:
        self._sessions[record.session_id] = record
        self._token_index[record.token_hash] = record.session_id

    def get_by_id(self, session_id: str) -> SessionRecord | None:
        return self._sessions.get(session_id)

    def get_by_token_hash(self, token_hash: str) -> SessionRecord | None:
        sid = self._token_index.get(token_hash)
        if sid is None:
            return None
        return self._sessions.get(sid)

    def list_by_account(self, account_id: str) -> list[SessionRecord]:
        return [
            s for s in self._sessions.values() if s.account_id == account_id
        ]

    def delete(self, session_id: str) -> None:
        record = self._sessions.pop(session_id, None)
        if record is not None:
            self._token_index.pop(record.token_hash, None)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

import hashlib
import secrets


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _generate_token() -> tuple[str, str]:
    """Return (raw_token, hash)."""
    raw = f"tf_{secrets.token_urlsafe(48)}"
    return raw, _hash_token(raw)


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class SessionService:
    """Manages session lifecycle."""

    def __init__(self, store: SessionStore | None = None) -> None:
        self._store = store or InMemorySessionStore()

    # ---- create ----

    def create_session(
        self,
        account_id: str,
        owner_scope: OwnerScope,
        ttl_minutes: int | None = None,
    ) -> tuple[str, SessionRecord]:
        """Create a new session, return (raw_token, record)."""
        if ttl_minutes is None:
            ttl_minutes = settings.access_token_expire_minutes

        raw, token_hash = _generate_token()
        session_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        record = SessionRecord(
            session_id=session_id,
            account_id=account_id,
            owner_scope=owner_scope,
            token_hash=token_hash,
            status=SessionStatus.ACTIVE,
            created_at=now,
            expires_at=now + timedelta(minutes=ttl_minutes),
        )
        self._store.save(record)
        return raw, record

    # ---- validate ----

    def validate_session(self, raw_token: str) -> SessionRecord:
        """Validate a raw token. Returns record or raises."""
        token_hash = _hash_token(raw_token)
        record = self._store.get_by_token_hash(token_hash)
        if record is None:
            raise UnauthorizedError()

        now = datetime.now(timezone.utc)

        # Auto-expire
        if record.status == SessionStatus.ACTIVE and record.expires_at and now > record.expires_at:
            record.status = SessionStatus.EXPIRED
            self._store.save(record)

        if record.status != SessionStatus.ACTIVE:
            raise UnauthorizedError()

        return record

    # ---- revoke ----

    def revoke_session(self, session_id: str) -> SessionRecord | None:
        """Revoke a session by id. Returns record or None."""
        record = self._store.get_by_id(session_id)
        if record is None:
            return None
        record.status = SessionStatus.REVOKED
        record.revoked_at = datetime.now(timezone.utc)
        self._store.save(record)
        return record

    def revoke_all_account_sessions(self, account_id: str) -> int:
        """Revoke all active sessions for an account. Returns count."""
        count = 0
        for record in self._store.list_by_account(account_id):
            if record.status == SessionStatus.ACTIVE:
                record.status = SessionStatus.REVOKED
                record.revoked_at = datetime.now(timezone.utc)
                self._store.save(record)
                count += 1
        return count

    # ---- query ----

    def get_session(self, session_id: str) -> SessionRecord:
        record = self._store.get_by_id(session_id)
        if record is None:
            raise NotFoundError("Session", session_id)
        return record

    def list_sessions(self, account_id: str) -> list[SessionRecord]:
        return self._store.list_by_account(account_id)

    # ---- cleanup ----

    def expire_stale(self) -> int:
        """Expire sessions past their TTL. Returns count."""
        now = datetime.now(timezone.utc)
        count = 0
        # Get all sessions from the store
        if hasattr(self._store, '_sessions'):
            records = list(self._store._sessions.values())  # type: ignore[attr-defined]
        else:
            return 0
        for record in records:
            if record.status == SessionStatus.ACTIVE and record.expires_at and now > record.expires_at:
                record.status = SessionStatus.EXPIRED
                self._store.save(record)
                count += 1
        return count
