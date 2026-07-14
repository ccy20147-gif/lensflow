# ToonFlow Backend Foundation — ADR-001: Backend Architecture

## Status
Approved (Foundation)

## Context
ToonFlow requires a new single backend serving web clients, workers, and provider callbacks.
Existing SeedV/Toonflow backends are reference only — not production dependencies.

## Decisions
1. **Framework**: FastAPI 0.115+ with async support, Pydantic v2 for validation
2. **Database**: PostgreSQL 16+ with SQLAlchemy 2.0 async + Alembic migrations
3. **Queue**: Redis-backed task queue for async workers; transactional outbox for durable dispatch
4. **API Versioning**: URL prefix v1 (`/api/v1/`); deprecated versions maintained for one cycle
5. **Module Separation**: Domain modules under `backend/src/domain/`:
   - identity, workflow, artifact, resource, runtime, provider, agent, skill, recipe, project, template, quality
6. **Authentication**: JWT bearer tokens (RS256); service identities for workers/callbacks
7. **Configuration**: Pydantic Settings from environment variables; secrets from env or vault
8. **Transactions**: Business writes + audit + outbox in same DB transaction
9. **Error Handling**: SafeError with correlation ID; no internal stack traces in responses
10. **Observability**: structlog for structured logging; opentelemetry for tracing

## Consequences
- All domain modules share the same authentication, audit, and transaction contracts
- No module may bypass Application Service layer to directly write cross-domain tables
- Worker, callback, and API paths share the same command/query handlers with idempotency keys
