"""Production RuntimeWorker process entry point.

Run with ``python -m src.domain.runtime.worker_entrypoint``.  It is separate
from the FastAPI process and deliberately talks to the same PostgreSQL queue
as the internal runtime API.  The process never accepts browser traffic and
therefore needs no HTTP worker key: deployment identity is the database
credential plus the explicit worker id recorded on every lease.
"""
from __future__ import annotations

import argparse
import logging
import signal
from threading import Event
from collections.abc import Callable

from src.core.exceptions import ConflictError, NotFoundError, PolicyBlockedError, ValidationError_
from src.domain.runtime.worker import RuntimeWorker
from src.domain.provider.atlascloud import AtlasCloudAdapter


logger = logging.getLogger(__name__)
_stop = Event()


def _request_stop(_signal: int, _frame: object) -> None:
    _stop.set()


def _run_maintenance(name: str, action: Callable[[], object]) -> None:
    """Isolate maintenance failures so one integration cannot kill scheduling."""
    try:
        action()
    except Exception:
        logger.exception("runtime worker maintenance %s failed", name)


def run(*, worker_id: str, poll_seconds: float = 0.25, once: bool = False) -> int:
    """Recover then drain durable attempts until stopped.

    A deterministic local/business failure is terminally failed so its
    fallback and run aggregation can proceed.  Provider uncertainty is owned
    by the invocation layer and remains UNKNOWN for reconciliation; this loop
    never blindly resubmits it.
    """
    worker = RuntimeWorker()
    report = worker.recover_pending()
    logger.info("runtime worker recovery: %s", report)
    while not _stop.is_set():
        # These are independent durable loops.  None of them fabricates a
        # provider submission: uncertain work remains UNKNOWN for AtlasCloud
        # reconciliation or a signed callback.
        _run_maintenance("human-task-expiry", worker.expire_due_human_tasks)
        _run_maintenance("map-item-recovery", worker.recover_map_items)
        _run_maintenance("tool-dispatch", worker.consume_tool_dispatches)
        _run_maintenance("provider-dispatch-outbox", worker.consume_provider_dispatch_outbox)
        if AtlasCloudAdapter().configured:
            _run_maintenance("atlascloud-reconciliation", worker.reconcile_unknown)
        claim = worker.claim_next_attempt(worker_id)
        if claim is None:
            if once:
                return 0
            _stop.wait(poll_seconds)
            continue
        try:
            worker.execute_attempt(claim.attempt.attempt_id)
        except (ConflictError, NotFoundError, PolicyBlockedError, ValidationError_) as exc:
            # A concurrent completion/cancellation is already safe.  For a
            # genuine executor validation/policy failure, make the durable
            # failure visible to the scheduler instead of leaving a lease.
            try:
                worker.fail_attempt(claim.attempt.attempt_id)
            except ConflictError:
                pass
            logger.warning("attempt %s failed: %s", claim.attempt.attempt_id, exc)
        except Exception:
            # Unknown programmer/infrastructure failures are intentionally
            # logged and made terminal here; provider send uncertainty is
            # converted to UNKNOWN by its adapter before it reaches this path.
            logger.exception("attempt %s crashed", claim.attempt.attempt_id)
            try:
                worker.fail_attempt(claim.attempt.attempt_id)
            except ConflictError:
                pass
        if once:
            return 0
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="ToonFlow durable runtime worker")
    parser.add_argument("--worker-id", required=True, help="stable deployment/replica identifier")
    parser.add_argument("--poll-seconds", type=float, default=0.25)
    parser.add_argument("--once", action="store_true", help="recover and process at most one attempt")
    args = parser.parse_args()
    if args.poll_seconds <= 0:
        parser.error("--poll-seconds must be positive")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)
    return run(worker_id=args.worker_id, poll_seconds=args.poll_seconds, once=args.once)


if __name__ == "__main__":
    raise SystemExit(main())
