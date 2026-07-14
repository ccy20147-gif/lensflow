"""
ToonFlow Backend — FastAPI Application Entry Point
"""
from __future__ import annotations

import os
import sys

# Ensure the package root is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from src.core.config import settings
from src.core.exceptions import SafeError

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    docs_url="/docs" if settings.debug else None,
    redoc_url="/redoc" if settings.debug else None,
)

# --- Register Routers ---

from src.api.routes.identity import router as identity_router
from src.api.routes.project import router as project_router
from src.api.routes.template import router as template_router

app.include_router(identity_router)
app.include_router(project_router)
app.include_router(template_router)

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
    return {"status": "ready", "dependencies": {"database": "unknown", "queue": "unknown", "blob": "unknown"}}


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
