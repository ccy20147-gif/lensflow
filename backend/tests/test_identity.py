"""Tests for Identity domain (TF-PLT-001)."""
from __future__ import annotations

import uuid

import pytest

from src.core.exceptions import ConflictError, CrossOwnerError, ForbiddenError, NotFoundError, UnauthorizedError
from src.domain.identity.identity_service import IdentityService, InMemoryAccountStore
from src.domain.identity.session_service import InMemorySessionStore, SessionService
from src.schemas.enums import AccountStatus, SessionStatus
from src.schemas.models import OwnerScope


@pytest.fixture
def fresh_store() -> InMemoryAccountStore:
    """Fresh account store for each test."""
    return InMemoryAccountStore()


@pytest.fixture
def fresh_session_store() -> InMemorySessionStore:
    return InMemorySessionStore()


@pytest.fixture
def identity(fresh_store, fresh_session_store) -> IdentityService:
    """Fresh identity service with isolated stores."""
    sess = SessionService(store=fresh_session_store)
    return IdentityService(account_store=fresh_store, session_service=sess)


# ===========================================================================
# AC-1: Bootstrap idempotency
# ===========================================================================


class TestBootstrapIdempotency:
    """AC-1: Concurrent/sequential bootstrap calls produce exactly one owner."""

    def test_bootstrap_creates_first_owner(self, identity: IdentityService):
        """First bootstrap creates a valid owner."""
        account = identity.bootstrap_owner(
            email="owner@toonflow.io",
            display_name="Founder",
            password="StrongP@ss1",
        )
        assert account.status == AccountStatus.ACTIVE
        assert account.email == "owner@toonflow.io"

    def test_bootstrap_is_idempotent(self, identity: IdentityService):
        """Running bootstrap 10 times only creates 1 owner (AC-1)."""
        accounts = []
        for i in range(10):
            acct = identity.bootstrap_owner(
                email=f"run{i}@toonflow.io",
                display_name=f"Run {i}",
                password="StrongP@ss1",
            )
            accounts.append(acct)

        # All 10 calls return the same account
        first_id = accounts[0].account_id
        for acct in accounts:
            assert acct.account_id == first_id, f"Run produced different owner {acct.account_id}"

        # Only one bootstrap owner in the store
        count = sum(1 for r in identity._accounts.list_all() if r.is_bootstrap)
        assert count == 1

    def test_bootstrap_returns_same_account_every_time(self, identity: IdentityService):
        """Bootstrap returns the identical account on repeat calls."""
        a1 = identity.bootstrap_owner(email="same@test.io", display_name="Same", password="P@ssword1")
        a2 = identity.bootstrap_owner(email="different@test.io", display_name="Different", password="P@ssword2")
        assert a1.account_id == a2.account_id
        assert a1.email == a2.email  # First email is kept

    def test_bootstrap_status(self, identity: IdentityService):
        """is_bootstrap_completed reflects reality."""
        assert identity.is_bootstrap_completed() is False
        identity.bootstrap_owner(email="o@t.io", display_name="O", password="P@ss1")
        assert identity.is_bootstrap_completed() is True

    def test_get_bootstrap_owner(self, identity: IdentityService):
        """get_bootstrap_owner returns the owner or None."""
        assert identity.get_bootstrap_owner() is None
        owner = identity.bootstrap_owner(email="o@t.io", display_name="O", password="P@ss1")
        fetched = identity.get_bootstrap_owner()
        assert fetched is not None
        assert fetched.account_id == owner.account_id


# ===========================================================================
# Registration & Verification
# ===========================================================================


class TestRegistration:
    def test_register_new_account(self, identity: IdentityService):
        """Register a fresh account."""
        acct = identity.register_account(
            email="user@example.com",
            display_name="User One",
            password="SecureP@ss1",
        )
        assert acct.status == AccountStatus.PENDING_VERIFICATION
        assert acct.email == "user@example.com"

    def test_register_duplicate_email(self, identity: IdentityService):
        """Duplicate email raises ConflictError."""
        identity.register_account(email="dup@test.io", display_name="First", password="P@ss1")
        with pytest.raises(ConflictError, match=".*邮箱.*"):
            identity.register_account(email="dup@test.io", display_name="Second", password="P@ss2")

    def test_verify_account(self, identity: IdentityService):
        """Verify transitions from pending_verification to active."""
        acct = identity.register_account(email="v@test.io", display_name="V", password="P@ss1")
        assert acct.status == AccountStatus.PENDING_VERIFICATION
        verified = identity.verify_account(str(acct.account_id))
        assert verified.status == AccountStatus.ACTIVE

    def test_verify_already_active_raises(self, identity: IdentityService):
        """Verifying an already active account raises ConflictError."""
        owner = identity.bootstrap_owner(email="o@t.io", display_name="O", password="P@ss1")
        with pytest.raises(ConflictError):
            identity.verify_account(str(owner.account_id))

    def test_verify_nonexistent_raises(self, identity: IdentityService):
        """Verifying a non-existent account raises NotFoundError."""
        with pytest.raises(NotFoundError):
            identity.verify_account(str(uuid.uuid4()))


# ===========================================================================
# Login / Logout
# ===========================================================================


class TestLogin:
    def test_login_success(self, identity: IdentityService):
        """Successful login returns account, token, and session."""
        identity.bootstrap_owner(email="login@test.io", display_name="Login", password="P@ssword1")
        account, token, session = identity.login(email="login@test.io", password="P@ssword1")
        assert account is not None
        assert token.startswith("tf_")
        assert session.status == SessionStatus.ACTIVE

    def test_login_wrong_password(self, identity: IdentityService):
        """Wrong password raises UnauthorizedError."""
        identity.bootstrap_owner(email="fail@test.io", display_name="Fail", password="Correct1")
        with pytest.raises(UnauthorizedError):
            identity.login(email="fail@test.io", password="WrongPassword")

    def test_login_nonexistent_email(self, identity: IdentityService):
        """Non-existent email raises UnauthorizedError."""
        with pytest.raises(UnauthorizedError):
            identity.login(email="noone@test.io", password="AnyP@ss1")

    def test_logout_revokes_session(self, identity: IdentityService):
        """Logout revokes the token's session."""
        identity.bootstrap_owner(email="lo@test.io", display_name="LO", password="P@ss1")
        _, token, _ = identity.login(email="lo@test.io", password="P@ss1")
        identity.logout(token)
        with pytest.raises(UnauthorizedError):
            identity.validate_token(token)


# ===========================================================================
# Cross-account isolation (AC-3)
# ===========================================================================


class TestCrossAccountIsolation:
    """AC-3: Two accounts cannot see each other's data."""

    def test_cross_owner_access_raises(self, identity: IdentityService):
        """Accessing another owner's scope raises CrossOwnerError."""
        owner1 = OwnerScope(kind="user", id=uuid.uuid4())
        owner2 = OwnerScope(kind="user", id=uuid.uuid4())

        with pytest.raises(CrossOwnerError):
            identity.check_owner_access(token_owner=owner1, resource_owner=owner2)

    def test_same_owner_passes(self, identity: IdentityService):
        """Accessing own scope passes."""
        owner = OwnerScope(kind="user", id=uuid.uuid4())
        # Should not raise
        identity.check_owner_access(token_owner=owner, resource_owner=owner)


# ===========================================================================
# Session revocation (AC-4)
# ===========================================================================


class TestSessionRevocation:
    """AC-4: Revoked tokens are rejected on next protected request."""

    def test_revoke_session(self, identity: IdentityService):
        """Revoking a session makes the token invalid."""
        identity.bootstrap_owner(email="rev@test.io", display_name="Rev", password="P@ss1")
        _, token, session = identity.login(email="rev@test.io", password="P@ss1")
        # Session is active
        identity.validate_token(token)
        # Revoke
        identity._sessions.revoke_session(session.session_id)
        # Now fails
        with pytest.raises(UnauthorizedError):
            identity.validate_token(token)

    def test_revoke_all_sessions(self, identity: IdentityService):
        """Revoke all sessions for an account."""
        owner = identity.bootstrap_owner(email="all@test.io", display_name="All", password="P@ss1")
        aid = str(owner.account_id)
        # Create multiple sessions
        _, t1, _ = identity.login(email="all@test.io", password="P@ss1")
        _, t2, _ = identity.login(email="all@test.io", password="P@ss1")
        count = identity._sessions.revoke_all_account_sessions(aid)
        assert count >= 2
        with pytest.raises(UnauthorizedError):
            identity.validate_token(t1)
        with pytest.raises(UnauthorizedError):
            identity.validate_token(t2)

    def test_session_expiry(self, identity: IdentityService):
        """Expired sessions are rejected."""
        identity.bootstrap_owner(email="exp@test.io", display_name="Exp", password="P@ss1")
        raw, record = identity._sessions.create_session(
            account_id="test",
            owner_scope=OwnerScope(kind="user", id=uuid.uuid4()),
            ttl_minutes=-1,  # already expired
        )
        with pytest.raises(UnauthorizedError):
            identity._sessions.validate_session(raw)


# ===========================================================================
# Account status transitions (FR-6)
# ===========================================================================


class TestAccountStatus:
    def test_suspend_account(self, identity: IdentityService):
        """Suspended account cannot login."""
        owner = identity.bootstrap_owner(email="sus@test.io", display_name="Sus", password="P@ss1")
        identity.suspend_account(str(owner.account_id))
        with pytest.raises(ForbiddenError):
            identity.login(email="sus@test.io", password="P@ss1")

    def test_deletion_flow(self, identity: IdentityService):
        """Deletion request transitions account through proper states."""
        owner = identity.bootstrap_owner(email="del@test.io", display_name="Del", password="P@ss1")
        aid = str(owner.account_id)
        # Request deletion
        pending = identity.request_account_deletion(aid)
        assert pending.status == AccountStatus.DELETION_PENDING
        # Login blocked
        with pytest.raises(ForbiddenError):
            identity.login(email="del@test.io", password="P@ss1")
