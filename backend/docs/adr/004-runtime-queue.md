# ToonFlow Foundation — ADR-004: Runtime & Queue

## Status
Approved (Foundation)

## Context
Long-running DAG execution with async provider callbacks requires durable state, fencing, and outbox patterns.
Memory queues or frontend-driven state management cannot guarantee correct recovery.

## Decisions
1. **WorkflowRun**: Fixed `workflow_revision_id + compiled_plan_id + owner_scope + input snapshot`
2. **NodeRunAttempt**: `AttemptStatus` (pending|leased|running|waiting_external|completed|failed|cancelled|superseded|unknown) with execution_epoch for fencing
3. **Epoch/Fencing**: Each attempt has an incrementing `execution_epoch`; result publishing requires matching epoch; new attempt invalidates old epoch
4. **Transactional Outbox**: Provider dispatch events written in same DB transaction as business state; dispatcher reads outbox after commit
5. **ProviderInvocationAttempt**: Network call only after DB transaction commits; stable idempotency_key per attempt
6. **Unknown Resolution**: When provider response is uncertain, status goes to `unknown` — query/reconcile only, no blind retry
7. **Fallback**: Fresh CapabilitySnapshot → recompile → new authorization → new budget → new Attempt; never reuse old attempt
8. **Lease**: Worker heartbeat with lease expiry; expired leases allow retry by new worker

## Consequences
- Provider dispatch is never lost but may be delayed under DB/queue failure
- At-most-once provider submission guarantee (not at-least-once)
- Worker crashes leave traceable state; no phantom runs after recovery
