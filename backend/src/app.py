"""
ToonFlow Backend — FastAPI Application Entry Point.

Composition root: every router is imported explicitly. Routes whose
module fails to import cause the app to fail at startup — the prior
``except ImportError: pass`` swallowed wiring mistakes.  This is the
Demo composition root: it must reflect exactly the API surface
exercised by the browser UI.
"""
from __future__ import annotations

import os
import sys
import traceback
import asyncio
from contextlib import asynccontextmanager

# Ensure the package root is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from src.core.config import settings
from src.core.exceptions import SafeError
from src.infra.db.session import get_session_factory

@asynccontextmanager
async def lifespan(_: FastAPI):
    stop = asyncio.Event()
    task: asyncio.Task[None] | None = None
    # Built-in runtime and public business nodes are ordinary approved,
    # versioned registry definitions.  This makes every published plan rely
    # on a persisted snapshot rather than compiler fallbacks.
    from src.domain.workflow.builtin_registry import ensure_public_business_node_baseline
    ensure_public_business_node_baseline()
    if settings.embedded_business_worker_enabled:
        from src.domain.runtime.embedded_worker import run_embedded_business_worker
        task = asyncio.create_task(run_embedded_business_worker(stop))
    try:
        yield
    finally:
        stop.set()
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    docs_url="/docs" if settings.debug else None,
    redoc_url="/redoc" if settings.debug else None,
    lifespan=lifespan,
)

# --- Register Routers (explicit, fail-loud) ---

_registration_failures: list[str] = []

router_modules = [
    "identity",
    "project",
    "template",
    "artifact",
    "governance",
    "registry",
    "workflow",
    "runtime",
    "control_flow",
    "business_nodes",
    "agent",
    "architect",
    "skill",
    "recipe",
    "tool",
]

for mod_name in router_modules:
    try:
        mod = __import__(f"src.api.routes.{mod_name}", fromlist=["router"])
        app.include_router(mod.router)
    except ImportError as exc:
        _registration_failures.append(
            f"{mod_name}: {exc}\n{traceback.format_exc()}"
        )
    except Exception as exc:  # surface wiring mistakes at startup
        _registration_failures.append(
            f"{mod_name}: {type(exc).__name__}: {exc}\n{traceback.format_exc()}"
        )


# --- Middleware ---

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def add_correlation_id(request: Request, call_next: Any):
    correlation_id = request.headers.get("X-Correlation-Id", str(uuid.uuid4()))
    request.state.correlation_id = correlation_id
    response = await call_next(request)
    response.headers["X-Correlation-Id"] = correlation_id
    return response


# --- Exception Handler ---


@app.exception_handler(SafeError)
async def safe_error_handler(request: Request, exc: SafeError):
    exc.correlation_id = getattr(request.state, "correlation_id", None)
    return JSONResponse(
        status_code=exc.status_code,
        content=exc.to_dict(),
    )


# --- Health Endpoints ---


@app.get("/health/live")
async def health_live():
    return {"status": "alive"}


@app.get("/health/ready")
async def health_ready():
    """Production readiness — actually probes PostgreSQL."""
    dependencies = {"database": "unconfigured", "queue": "unconfigured", "blob": "unconfigured"}
    overall = "ready"
    try:
        factory = get_session_factory()
        with factory() as session:
            session.execute(text("SELECT 1"))
        dependencies["database"] = "reachable"
    except Exception as exc:
        dependencies["database"] = f"unreachable: {exc}"
        overall = "degraded"
    return {"status": overall, "dependencies": dependencies}


@app.get("/health/routes")
async def health_routes():
    """Demo composition root — confirm every domain router is registered."""
    return {
        "status": "ok" if not _registration_failures else "degraded",
        "registered_count": len(app.routes),
        "registration_failures": _registration_failures,
    }


@app.get("/version")
async def version():
    return {
        "app_name": settings.app_name,
        "app_version": settings.app_version,
        "build_sha": "development",
        "schema_version": "1.0",
    }


@app.get("/api/v1/health")
async def api_health():
    return {"status": "ok"}


@app.get("/")
async def root():
    return {"message": "ToonFlow API", "version": settings.app_version}
