"""Identity API Routes — V0 bootstrap, V1 registration/login.

Production wiring uses PostgreSQL-backed SqlIdentityRepository +
SqlSessionStore; the in-memory services remain as focused unit-test
doubles and are not wired into the API surface.
"""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from src.core.exceptions import ConflictError, NotFoundError
from src.infra.db.identity_repository import SqlIdentityRepository, get_session_store
from src.schemas.enums import AccountStatus

router = APIRouter(prefix="/api/v1/identity", tags=["identity"])

# Production wiring — durable PostgreSQL-backed services.
_identity = SqlIdentityRepository()
_sessions = get_session_store()


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


class BootstrapStatus(BaseModel):
    completed: bool
    bootstrap_email: str = ""


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/bootstrap-status", response_model=BootstrapStatus)
async def bootstrap_status() -> BootstrapStatus:
    """Return whether a bootstrap owner exists.

    A bootstrap owner is the first registered user in the deployment.
    The check uses ``email == "bootstrap"`` as the sentinel key, but any
    deployment can override this in production.
    """
    row = _identity.get_by_email("bootstrap@toonflow.local")
    if row is None:
        return BootstrapStatus(completed=False)
    return BootstrapStatus(completed=True, bootstrap_email=row.email)


@router.post("/bootstrap", status_code=201)
async def bootstrap(body: BootstrapRequest) -> dict[str, Any]:
    """Create the bootstrap owner (idempotent)."""
    row, created = _identity.bootstrap(
        email=body.email,
        display_name=body.display_name,
        plaintext_password=body.password,
    )
    return {
        "account_id": str(row.account_id),
        "email": row.email,
        "display_name": row.display_name,
        "created": created,
    }


@router.post("/register", status_code=201)
async def register(body: RegisterRequest) -> dict[str, Any]:
    """Register a new user account."""
    try:
        row = _identity.register(body.email, body.display_name, body.password)
    except ConflictError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_dict())
    return {"account_id": str(row.account_id), "email": row.email, "display_name": row.display_name}


@router.post("/login")
async def login(body: LoginRequest) -> dict[str, Any]:
    """Authenticate and issue a session token."""
    try:
        row = _identity.authenticate(body.email, body.password)
    except NotFoundError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_dict())
    except ConflictError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_dict())
    session = _sessions.issue(row.account_id)
    return {
        "account_id": str(row.account_id),
        "token": session["token"],
        "expires_at": session["expires_at"].isoformat(),
    }


@router.get("/verify")
async def verify(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    """Verify a bearer token and return the associated account_id."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail={"error": {"code": "UNAUTHORIZED"}})
    token = authorization.removeprefix("Bearer ")
    try:
        session = _sessions.verify(token)
    except (NotFoundError, ConflictError) as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_dict())
    return {"account_id": str(session["account_id"]), "status": session["status"].value}


@router.post("/logout")
async def logout(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    """Revoke the bearer token."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail={"error": {"code": "UNAUTHORIZED"}})
    token = authorization.removeprefix("Bearer ")
    try:
        _sessions.revoke(token)
    except NotFoundError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_dict())
    return {"status": "revoked"}


@router.get("/sessions")
async def list_sessions(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    """List active sessions for the bearer-token account."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail={"error": {"code": "UNAUTHORIZED"}})
    token = authorization.removeprefix("Bearer ")
    try:
        session = _sessions.verify(token)
    except (NotFoundError, ConflictError) as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_dict())
    return {"sessions": _sessions.list_for_account(session["account_id"])}


@router.post("/accounts/{account_id}/suspend")
async def suspend_account(account_id: str) -> dict[str, Any]:
    """Suspend an account."""
    try:
        row = _identity.transition_status(_id(account_id), AccountStatus.SUSPENDED)
    except NotFoundError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_dict())
    return {"account_id": str(row.account_id), "status": row.status.value}


@router.post("/accounts/{account_id}/reinstate")
async def reinstate_account(account_id: str) -> dict[str, Any]:
    """Reinstate a suspended account."""
    try:
        row = _identity.transition_status(_id(account_id), AccountStatus.ACTIVE)
    except NotFoundError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_dict())
    return {"account_id": str(row.account_id), "status": row.status.value}


def _id(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid id") from exc