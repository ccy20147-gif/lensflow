# ToonFlow Foundation — ADR-005: Security & Policy

## Status
Approved (Foundation)

## Context
Content safety, consent, rights evidence, and multi-tenancy isolation must work from day one.
V0 bootstrap owner still enforces owner_scope on all entities.

## Decisions
1. **Default Deny**: Unknown identity, cross-owner_scope access, and unregistered provider requests are rejected
2. **Owner Scope**: All Project, Resource, Artifact, Run, Blob, CredentialBinding carry owner_scope
3. **SafeError**: API returns `{"error": {"code": "SEC_CROSS_OWNER", "message": "安全错误", "correlation_id": "…"}}` — no internal details
4. **ArtifactRef**: Same owner_scope only; cross-owner content must be promoted to ResourceRevision with GrantSnapshot
5. **Consent Evidence**: Human face/voice requires explicit consent record with scope, duration, and revocation support
6. **Credential Encryption**: CredentialBinding stored encrypted, never returned in API responses, never in logs
7. **Audit Trail**: All state changes record actor, action, target, old/new values, and correlation_id
8. **Rate Limiting**: Auth endpoints, upload, and critical mutations rate-limited per actor/owner

## Consequences
- Cross-owner workflows need explicit GrantSnapshot or entitlement checks
- V1 multi-account upgrade preserves all V0 owner_scope boundaries without migration
- Audit trail is append-only; corrections create new entries
