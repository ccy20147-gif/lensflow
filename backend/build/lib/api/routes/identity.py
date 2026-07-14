"""Identity API Routes — V0 bootstrap, V1 registration/login."""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from src.core.exceptions import CrossOwnerError, ForbiddenError, NotFoundError, UnauthorizedError
from src.domain.identity.identity_service import IdentityService
from src.domain.identity.session_service import SessionService

router = APIRouter(prefix="/api/v1/identity", tags=["identity"])

# ---------------------------------------------------------------------------
# Singleton service instances (swap with DI later)
# ---------------------------------------------------------------------------

_session_service = SessionService()
_identity_service = IdentityService(session_service=_session_service)


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class BootstrapRequest(BaseModel):
    email: str
    display_name: str
    password: str


class LoginRequest(BaseModel):
    email: str
    password: str


class RegisterRequest(BaseModel):
    email: str
    display_name: str
    password: str


class AccountResponse(BaseModel):
    account_id: str
    email: str
    display_name: str
    status: str
    created_at: str
    updated_at: str


class LoginResponse(BaseModel):
    account: AccountResponse
    token: str
    session_id: str


class SessionInfo(BaseModel):
    session_id: str
    status: str
    created_at: str
    expires_at: str | None


class StatusResponse(BaseModel):
    status: str


class ErrorResponse(BaseModel):
    error: dict[str, Any]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _account_to_response(account: Any) -> AccountResponse:
    return AccountResponse(
        account_id=str(account.account_id),
        email=account.email,
        display_name=account.display_name,
        status=account.status.value if hasattr(account.status, "value") else str(account.status),
        created_at=account.created_at.isoformat() if hasattr(account.created_at, "isoformat") else str(account.created_at),
        updated_at=account.updated_at.isoformat() if hasattr(account.updated_at, "isoformat") else str(account.updated_at),
    )


def _resolve_token(authorization: str | None = None) -> str:
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing authorization header")
    parts = authorization.split()
    if parts[0].lower() != "bearer" or len(parts) != 2:
        raise HTTPException(status_code=401, detail="Invalid authorization header")
    return parts[1]


# ---------------------------------------------------------------------------
# V0 Bootstrap (FR-1)
# ---------------------------------------------------------------------------


@router.post("/bootstrap", response_model=AccountResponse, status_code=201)
async def bootstrap(body: BootstrapRequest):
    """Create the single bootstrap owner (idempotent)."""
    account = _identity_service.bootstrap_owner(
        email=body.email,
        display_name=body.display_name,
        password=body.password,
    )
    return _account_to_response(account)


@router.get("/bootstrap/status", response_model=StatusResponse)
async def bootstrap_status():
    """Check if bootstrap is completed."""
    completed = _identity_service.is_bootstrap_completed()
    return StatusResponse(status="completed" if completed else "pending")


# ---------------------------------------------------------------------------
# V1 Registration (FR-3)
# ---------------------------------------------------------------------------


@router.post("/register", response_model=AccountResponse, status_code=201)
async def register(body: RegisterRequest):
    """Register a new user account."""
    account = _identity_service.register_account(
        email=body.email,
        display_name=body.display_name,
        password=body.password,
    )
    return _account_to_response(account)


@router.post("/accounts/{account_id}/verify", response_model=AccountResponse)
async def verify_account(account_id: str):
    """Verify (activate) a pending account."""
    account = _identity_service.verify_account(account_id)
    return _account_to_response(account)


# ---------------------------------------------------------------------------
# Login / Logout (FR-3)
# ---------------------------------------------------------------------------


@router.post("/login", response_model=LoginResponse)
async def login(body: LoginRequest):
    """Authenticate and return a session token."""
    account, raw_token, session = _identity_service.login(
        email=body.email,
        password=body.password,
    )
    return LoginResponse(
        account=_account_to_response(account),
        token=raw_token,
        session_id=session.session_id,
    )


@router.post("/logout", response_model=StatusResponse)
async def logout(authorization: str | None = Header(None)):
    """Revoke current session."""
    try:
        token = _resolve_token(authorization)
        _identity_service.logout(token)
    except HTTPException:
        pass
    return StatusResponse(status="logged_out")


# ---------------------------------------------------------------------------
# Session management (FR-3, FR-4)
# ---------------------------------------------------------------------------


@router.get("/sessions", response_model=list[SessionInfo])
async def list_sessions(authorization: str | None = Header(None)):
    """List sessions for the authenticated user."""
    token = _resolve_token(authorization)
    account, _ = _identity_service.validate_token(token)
    sessions = _session_service.list_sessions(str(account.account_id))
    return [
        SessionInfo(
            session_id=s.session_id,
            status=s.status.value,
            created_at=s.created_at.isoformat(),
            expires_at=s.expires_at.isoformat() if s.expires_at else None,
        )
        for s in sessions
    ]


@router.post("/sessions/{session_id}/revoke", response_model=StatusResponse)
async def revoke_session(session_id: str, authorization: str | None = Header(None)):
    """Revoke a specific session."""
    token = _resolve_token(authorization)
    account, _ = _identity_service.validate_token(token)
    record = _session_service.revoke_session(session_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return StatusResponse(status="revoked")


# ---------------------------------------------------------------------------
# Account management (FR-6)
# ---------------------------------------------------------------------------


@router.get("/accounts/{account_id}", response_model=AccountResponse)
async def get_account(account_id: str, authorization: str | None = Header(None)):
    """Get account details."""
    token = _resolve_token(authorization)
    caller_account, _ = _identity_service.validate_token(token)
    # Only allow access to own account
    if str(caller_account.account_id) != account_id:
        raise HTTPException(status_code=403, detail="Cannot access other account")
    account = _identity_service.get_account(account_id)
    return _account_to_response(account)


@router.post("/accounts/{account_id}/suspend", response_model=AccountResponse)
async def suspend_account(account_id: str, authorization: str | None = Header(None)):
    """Suspend account (revokes all sessions)."""
    token = _resolve_token(authorization)
    caller_account, _ = _identity_service.validate_token(token)
    if str(caller_account.account_id) != account_id:
        raise HTTPException(status_code=403, detail="Cannot modify other account")
    account = _identity_service.suspend_account(account_id)
    return _account_to_response(account)


@router.post("/accounts/{account_id}/deletion-request", response_model=AccountResponse)
async def request_deletion(account_id: str, authorization: str | None = Header(None)):
    """Request account deletion."""
    token = _resolve_token(authorization)
    caller_account, _ = _identity_service.validate_token(token)
    if str(caller_account.account_id) != account_id:
        raise HTTPException(status_code=403, detail="Cannot modify other account")
    account = _identity_service.request_account_deletion(account_id)
    return _account_to_response(account)
