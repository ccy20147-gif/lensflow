"""Minimal server-derived V1 owner principal dependency."""
from __future__ import annotations

from uuid import UUID

from fastapi import Header, HTTPException

from src.core.exceptions import ConflictError, NotFoundError
from src.infra.db.identity_repository import get_session_store
from src.schemas.models import OwnerScope


def require_owner(authorization: str | None = Header(None)) -> tuple[UUID, OwnerScope]:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail={"error": {"code": "UNAUTHORIZED"}})
    try:
        account_id = get_session_store().account_for_token(authorization.removeprefix("Bearer "))
    except (ConflictError, NotFoundError) as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_dict()) from exc
    return account_id, OwnerScope(kind="user", id=account_id)
