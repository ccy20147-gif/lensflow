"""Identity Service — bootstrap, registration, login, account lifecycle."""
from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timezone
from typing import Protocol

from src.core.config import settings
from src.core.exceptions import ConflictError, CrossOwnerError, ForbiddenError, NotFoundError, UnauthorizedError
from src.schemas.enums import AccountStatus
from src.schemas.models import OwnerScope, UserAccount

from .session_service import SessionService, SessionRecord

# ---------------------------------------------------------------------------
# Password hashing (PBKDF2-HMAC-SHA256 — no bcrypt version conflicts)
# ---------------------------------------------------------------------------

_PBKDF2_ITERATIONS = 600_000
_SALT_BYTES = 32


def _hash_password(plain: str) -> str:
    salt = secrets.token_hex(_SALT_BYTES)
    dk = hashlib.pbkdf2_hmac("sha256", plain.encode("utf-8"), salt.encode("utf-8"), _PBKDF2_ITERATIONS)
    return f"{salt}:{dk.hex()}"


def _verify_password(plain: str, stored: str) -> bool:
    try:
        salt, expected_hex = stored.split(":", 1)
        dk = hashlib.pbkdf2_hmac("sha256", plain.encode("utf-8"), salt.encode("utf-8"), _PBKDF2_ITERATIONS)
        return secrets.compare_digest(dk.hex(), expected_hex)
    except (ValueError, AttributeError):
        return False


# ---------------------------------------------------------------------------
# Account storage
# ---------------------------------------------------------------------------


class AccountRecord:
    """Internal account storage record."""

    def __init__(
        self,
        account_id: str,
        email: str,
        display_name: str,
        password_hash: str,
        status: AccountStatus = AccountStatus.PENDING_VERIFICATION,
        is_bootstrap: bool = False,
        created_at: datetime | None = None,
        updated_at: datetime | None = None,
    ):
        self.account_id = account_id
        self.email = email
        self.display_name = display_name
        self.password_hash = password_hash
        self.status = status
        self.is_bootstrap = is_bootstrap
        self.created_at = created_at or datetime.now(timezone.utc)
        self.updated_at = updated_at or datetime.now(timezone.utc)


class AccountStore(Protocol):
    """Interface for account persistence."""

    def save(self, record: AccountRecord) -> None: ...
    def get_by_id(self, account_id: str) -> AccountRecord | None: ...
    def get_by_email(self, email: str) -> AccountRecord | None: ...
    def list_all(self) -> list[AccountRecord]: ...
    def count(self) -> int: ...


class InMemoryAccountStore:
    """Thread-safe in-memory account store."""

    def __init__(self) -> None:
        self._accounts: dict[str, AccountRecord] = {}
        self._email_index: dict[str, str] = {}  # email -> account_id

    def save(self, record: AccountRecord) -> None:
        self._accounts[record.account_id] = record
        self._email_index[record.email] = record.account_id

    def get_by_id(self, account_id: str) -> AccountRecord | None:
        return self._accounts.get(account_id)

    def get_by_email(self, email: str) -> AccountRecord | None:
        aid = self._email_index.get(email)
        if aid is None:
            return None
        return self._accounts.get(aid)

    def list_all(self) -> list[AccountRecord]:
        return list(self._accounts.values())

    def count(self) -> int:
        return len(self._accounts)


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class IdentityService:
    """Manages accounts, authentication, and owner_scope plumbing."""

    def __init__(
        self,
        account_store: AccountStore | None = None,
        session_service: SessionService | None = None,
    ) -> None:
        self._accounts = account_store or InMemoryAccountStore()
        self._sessions = session_service or SessionService()

    # ---- helpers ----

    def _to_user_account(self, rec: AccountRecord) -> UserAccount:
        return UserAccount(
            account_id=uuid.UUID(rec.account_id),
            email=rec.email,
            display_name=rec.display_name,
            status=rec.status,
            created_at=rec.created_at,
            updated_at=rec.updated_at,
        )

    def _to_owner_scope(self, account_id: str) -> OwnerScope:
        return OwnerScope(kind="user", id=uuid.UUID(account_id))

    # ---- V0 Bootstrap (FR-1) ----

    def bootstrap_owner(
        self,
        email: str,
        display_name: str,
        password: str,
    ) -> UserAccount:
        """Create the single bootstrap owner. Idempotent — 2nd+ call returns existing."""
        account_id = str(uuid.uuid4())
        password_hash = _hash_password(password)

        # Check if bootstrap already exists
        existing = None
        for rec in self._accounts.list_all():
            if rec.is_bootstrap:
                existing = rec
                break

        if existing is not None:
            return self._to_user_account(existing)

        record = AccountRecord(
            account_id=account_id,
            email=email,
            display_name=display_name,
            password_hash=password_hash,
            status=AccountStatus.ACTIVE,
            is_bootstrap=True,
        )
        self._accounts.save(record)
        settings.bootstrap_completed = True
        settings.bootstrap_owner_email = email

        # Auto-create initial session
        return self._to_user_account(record)

    def is_bootstrap_completed(self) -> bool:
        return any(r.is_bootstrap for r in self._accounts.list_all())

    def get_bootstrap_owner(self) -> UserAccount | None:
        for rec in self._accounts.list_all():
            if rec.is_bootstrap:
                return self._to_user_account(rec)
        return None

    # ---- V1 Registration (FR-3) ----

    def register_account(
        self,
        email: str,
        display_name: str,
        password: str,
    ) -> UserAccount:
        """Register a new user account."""
        # Check for duplicate email
        existing = self._accounts.get_by_email(email)
        if existing is not None:
            raise ConflictError("邮箱已被注册")

        account_id = str(uuid.uuid4())
        password_hash = _hash_password(password)

        record = AccountRecord(
            account_id=account_id,
            email=email,
            display_name=display_name,
            password_hash=password_hash,
            status=AccountStatus.PENDING_VERIFICATION,
            is_bootstrap=False,
        )
        self._accounts.save(record)
        return self._to_user_account(record)

    def verify_account(self, account_id: str) -> UserAccount:
        """Mark account as verified (activate)."""
        record = self._accounts.get_by_id(account_id)
        if record is None:
            raise NotFoundError("Account", account_id)
        if record.status != AccountStatus.PENDING_VERIFICATION:
            raise ConflictError("账户状态不允许验证")

        record.status = AccountStatus.ACTIVE
        record.updated_at = datetime.now(timezone.utc)
        self._accounts.save(record)
        return self._to_user_account(record)

    # ---- V1 Login (FR-3, FR-4) ----

    def login(
        self,
        email: str,
        password: str,
    ) -> tuple[UserAccount, str, SessionRecord]:
        """Authenticate and create session. Returns (account, raw_token, session)."""
        record = self._accounts.get_by_email(email)
        if record is None:
            raise UnauthorizedError()

        # Check status
        if record.status not in (AccountStatus.ACTIVE,):
            raise ForbiddenError("账户已被暂停或删除")

        if not _verify_password(password, record.password_hash):
            raise UnauthorizedError()

        owner_scope = self._to_owner_scope(record.account_id)
        raw_token, session_record = self._sessions.create_session(
            account_id=record.account_id,
            owner_scope=owner_scope,
        )

        return self._to_user_account(record), raw_token, session_record

    # ---- Logout (FR-3) ----

    def logout(self, raw_token: str) -> None:
        """Revoke the session associated with the token."""
        try:
            record = self._sessions.validate_session(raw_token)
            self._sessions.revoke_session(record.session_id)
        except UnauthorizedError:
            pass  # Silent — already invalid

    # ---- Account status management (FR-6, FR-7) ----

    def get_account(self, account_id: str) -> UserAccount:
        record = self._accounts.get_by_id(account_id)
        if record is None:
            raise NotFoundError("Account", account_id)
        return self._to_user_account(record)

    def suspend_account(self, account_id: str) -> UserAccount:
        """Suspend account — stops new runs, invalidates sessions (FR-6)."""
        record = self._accounts.get_by_id(account_id)
        if record is None:
            raise NotFoundError("Account", account_id)
        if record.status in (AccountStatus.DELETED_TOMBSTONE,):
            raise ConflictError("已删除的账户无法暂停")

        record.status = AccountStatus.SUSPENDED
        record.updated_at = datetime.now(timezone.utc)
        self._accounts.save(record)

        # Revoke all sessions
        self._sessions.revoke_all_account_sessions(account_id)

        return self._to_user_account(record)

    def request_account_deletion(self, account_id: str) -> UserAccount:
        """Start deletion flow (FR-7)."""
        record = self._accounts.get_by_id(account_id)
        if record is None:
            raise NotFoundError("Account", account_id)
        if record.status in (AccountStatus.DELETED_TOMBSTONE, AccountStatus.DELETION_PENDING):
            raise ConflictError("删除请求已存在")

        record.status = AccountStatus.DELETION_PENDING
        record.updated_at = datetime.now(timezone.utc)
        self._accounts.save(record)

        # Revoke all sessions
        self._sessions.revoke_all_account_sessions(account_id)

        return self._to_user_account(record)

    # ---- Session helpers ----

    def validate_token(self, raw_token: str) -> tuple[UserAccount, SessionRecord]:
        """Validate a session token. Returns (account, session)."""
        session_rec = self._sessions.validate_session(raw_token)
        account_rec = self._accounts.get_by_id(session_rec.account_id)
        if account_rec is None:
            raise UnauthorizedError()
        if account_rec.status != AccountStatus.ACTIVE:
            # Even if session is active, a suspended account can't use it
            self._sessions.revoke_session(session_rec.session_id)
            raise ForbiddenError("账户已被暂停")
        return self._to_user_account(account_rec), session_rec

    def get_owner_scope_from_token(self, raw_token: str) -> OwnerScope:
        """Extract owner_scope from a validated token."""
        account, _ = self.validate_token(raw_token)
        return self._to_owner_scope(str(account.account_id))

    # ---- Authorization guard ----

    def check_owner_access(
        self,
        token_owner: OwnerScope,
        resource_owner: OwnerScope,
    ) -> None:
        """Raise CrossOwnerError if owners don't match (FR-5)."""
        if token_owner.scoped_id != resource_owner.scoped_id:
            raise CrossOwnerError()
