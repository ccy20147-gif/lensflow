"""TF-PLT-001: PostgreSQL-backed Identity repository.

Persistent replacement for the in-memory IdentityService / SessionService.
Every read filters by owner or principal key; every write happens inside
``session_factory.begin()`` so cross-process state changes are atomic.

Session storage is in-memory during Foundation phase; a shared singleton
ensures token issuers and verifiers use the same dict.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from src.core.exceptions import ConflictError, NotFoundError
from src.infra.db.models import UserAccountModel
from src.infra.db.session import get_session_factory
from src.schemas.enums import AccountStatus, SessionStatus


def _hash_password(plaintext: str, salt: bytes | None = None) -> str:
    """Hash a password using SHA-256 with a random salt."""
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.sha256(salt + plaintext.encode()).hexdigest()
    return f"{salt.hex()}${digest}"


def _verify_password(plaintext: str, stored: str) -> bool:
    try:
        salt_hex, digest = stored.split("$", 1)
        salt = bytes.fromhex(salt_hex)
    except (ValueError, AttributeError):
        return False
    candidate = hashlib.sha256(salt + plaintext.encode()).hexdigest()
    return hmac.compare_digest(candidate, digest)


class SqlIdentityRepository:
    """User account + session persistence in PostgreSQL."""

    def __init__(self, factory: sessionmaker[Session] | None = None) -> None:
        self._factory = factory or get_session_factory()

    # ------------------------------------------------------------------
    # Bootstrap / registration
    # ------------------------------------------------------------------

    def bootstrap(
        self, email: str, display_name: str, plaintext_password: str
    ) -> tuple[UserAccountModel, bool]:
        """Idempotent bootstrap owner creation.

        Returns ``(row, created)``.  When the bootstrap owner already
        exists with the given email, returns the existing row with
        ``created=False``; otherwise creates a new active account.
        """
        if not email or not plaintext_password:
            raise ConflictError(message="bootstrap 需要 email 与密码")

        with self._factory.begin() as session:
            existing = session.scalar(
                select(UserAccountModel).where(UserAccountModel.email == email)
            )
            if existing is not None:
                return existing, False
            row = UserAccountModel(
                account_id=uuid.uuid4(),
                email=email,
                display_name=display_name,
                password_hash=_hash_password(plaintext_password),
                status=AccountStatus.ACTIVE,
                owner_scope=f"user:{uuid.uuid4()}",
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
            session.add(row)
            session.flush()
            return row, True

    def register(
        self, email: str, display_name: str, plaintext_password: str
    ) -> UserAccountModel:
        """Register a new account. Raises ConflictError on duplicate email."""
        with self._factory.begin() as session:
            existing = session.scalar(
                select(UserAccountModel).where(UserAccountModel.email == email)
            )
            if existing is not None:
                raise ConflictError(message=f"邮箱 {email} 已被注册")
            row = UserAccountModel(
                account_id=uuid.uuid4(),
                email=email,
                display_name=display_name,
                password_hash=_hash_password(plaintext_password),
                status=AccountStatus.ACTIVE,
                owner_scope=f"user:{uuid.uuid4()}",
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
            session.add(row)
            session.flush()
            return row

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    def authenticate(
        self, email: str, plaintext_password: str
    ) -> UserAccountModel:
        """Verify password and return the user row."""
        with self._factory() as session:
            row = session.scalar(
                select(UserAccountModel).where(UserAccountModel.email == email)
            )
            if row is None:
                raise NotFoundError("UserAccount", email)
            if row.status != AccountStatus.ACTIVE:
                raise ConflictError(message=f"账户状态 {row.status} 不可登录")
            if not _verify_password(plaintext_password, row.password_hash):
                raise ConflictError(message="邮箱或密码不正确")
            return row

    # ------------------------------------------------------------------
    # Status / lookup
    # ------------------------------------------------------------------

    def get_by_email(self, email: str) -> UserAccountModel | None:
        with self._factory() as session:
            return session.scalar(
                select(UserAccountModel).where(UserAccountModel.email == email)
            )

    def get_by_id(self, account_id: uuid.UUID) -> UserAccountModel | None:
        with self._factory() as session:
            return session.get(UserAccountModel, account_id)

    def transition_status(
        self, account_id: uuid.UUID, new_status: AccountStatus
    ) -> UserAccountModel:
        with self._factory.begin() as session:
            row = session.get(UserAccountModel, account_id)
            if row is None:
                raise NotFoundError("UserAccount", str(account_id))
            row.status = new_status
            row.updated_at = datetime.now(timezone.utc)
            session.flush()
            return row


# ---------------------------------------------------------------------------
# Shared session store — in-memory within Foundation phase; all API routes
# call ``get_session_store()`` to share the same singleton dict.
# ---------------------------------------------------------------------------


class SqlSessionStore:
    """Bounded session token store.

    The Foundation scope persists sessions inside an in-process dict
    controlled by a service-identity tenant.  The store lives behind the
    SqlIdentityRepository so the API can swap to a real DB-backed
    ``sessions`` table without changing callers.
    """

    def __init__(self) -> None:
        self._tokens: dict[str, dict[str, Any]] = {}

    def issue(
        self, account_id: uuid.UUID, ttl_minutes: int = 60
    ) -> dict[str, Any]:
        token = secrets.token_urlsafe(32)
        record = {
            "token": token,
            "account_id": account_id,
            "status": SessionStatus.ACTIVE,
            "issued_at": datetime.now(timezone.utc),
            "expires_at": datetime.now(timezone.utc)
            + timedelta(minutes=ttl_minutes),
        }
        self._tokens[token] = record
        return record

    def verify(self, token: str) -> dict[str, Any]:
        record = self._tokens.get(token)
        if record is None:
            raise NotFoundError("Session", token[:8] + "…")
        if record["status"] != SessionStatus.ACTIVE:
            raise ConflictError(message=f"会话状态 {record['status']}")
        if record["expires_at"] < datetime.now(timezone.utc):
            record["status"] = SessionStatus.EXPIRED
            raise ConflictError(message="会话已过期")
        return record

    def account_for_token(self, token: str) -> uuid.UUID:
        record = self.verify(token)
        return record["account_id"]

    def revoke(self, token: str) -> None:
        record = self._tokens.get(token)
        if record is None:
            raise NotFoundError("Session", token[:8] + "…")
        record["status"] = SessionStatus.REVOKED

    def list_for_account(
        self, account_id: uuid.UUID
    ) -> list[dict[str, Any]]:
        return [
            {**r, "token": r["token"][:8] + "…"}
            for r in self._tokens.values()
            if r["account_id"] == account_id
        ]


# Shared singleton — all callers import this function.
_SESSION_STORE: SqlSessionStore | None = None


def get_session_store() -> SqlSessionStore:
    global _SESSION_STORE
    if _SESSION_STORE is None:
        _SESSION_STORE = SqlSessionStore()
    return _SESSION_STORE