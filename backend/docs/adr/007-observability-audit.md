# ToonFlow Foundation — ADR-007: Observability & Audit

## Status
Approved (Foundation)

## Context
Debugging failed runs, security incidents, and cross-layer calls requires structured logs, traces, and audit records.

## Decisions
1. **Structured Logging**: structlog with JSON output; correlation_id on every request/task
2. **Tracing**: OpenTelemetry with manual span injection for domain boundaries
3. **SafeError**: All errors returned as structured JSON with:
   - `code`: stable error code (e.g., `SEC_CROSS_OWNER`, `WF_COMPILE_FAILED`)
   - `message`: user-safe message in request language
   - `correlation_id`: stable ID for operator debugging
   - `details`: omitted in production
4. **Audit Log**: `audit_events` table — append-only, records actor, action, target, old/new state, correlation_id
5. **Metrics**: Request rate/error/duration by endpoint; queue depth; outbox lag; provider latency
6. **Health Endpoints**:
   - `/health/live`: process alive
   - `/health/ready`: DB + queue + blob available
   - `/version`: build SHA, schema version, config fingerprint

## Consequences
- Logs never contain secrets, tokens, or raw provider responses
- Audit trail supports regulatory and security investigations
- Correlation IDs link frontend → API → worker → provider callback chains
