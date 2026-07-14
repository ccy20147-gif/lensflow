# ToonFlow Foundation — ADR-003: Schema & Identity

## Status
Approved (Foundation)

## Context
All PRDs reference shared types: ArtifactVersion, Resource, ResourceRevision, OwnerScope, etc.
Each domain must not create parallel type definitions or custom state enums.

## Decisions
1. **Canonical Schema Package**: `backend/src/schemas/` contains Pydantic models + JSON Schema export
2. **Schema Identity**: Each type has `schema_id` (stable identity) + `schema_version` (incrementing int)
3. **OwnerScope**: Every business entity carries `owner_scope` — either user or project scope
4. **State Machines** (public enums):
   - `RequirementStatus`: discovered|defined|reviewed|approved|in_delivery|implemented|verified|released|deferred|superseded|rejected
   - `RevisionStatus`: draft|active|retired
   - `RunStatus`: queued|running|waiting_user|cancelling|cancelled|completed|failed
   - `NodeRunStatus`: pending|ready|running|waiting_user|completed|failed|cancelled|skipped
   - `AttemptStatus`: pending|leased|running|waiting_external|completed|failed|cancelled|superseded|unknown
   - `HumanTaskStatus`: pending|waiting|submitted|accepted|rejected|escalated|expired|cancelled
   - `AccountStatus`: pending_verification|active|suspended|deletion_pending|deleted_tombstone
   - `ProjectStatus`: active|archived|deletion_pending|deleted_tombstone
   - `WorkbenchActionType`: provider.precompile|board.generate|grid.generate|grid.cell.regenerate|director_scene.export_controls|continuity.check|shot.generate|shot.rerun
5. **Hash Rules**: graph_hash includes nodes+edges+config; layout_hash includes positions only; execution_hash = graph_hash + all pinned dependency revisions

## Consequences
- All domains import from canonical schemas — no local state enums
- Round-trip tests verify JSON Schema ↔ Pydantic ↔ TypeScript mappings
- Database migration order: identity → content → workflow → runtime → provider → security → quality
