"""Optional in-process runner for the deterministic public business nodes.

It is intentionally opt-in for local demos and browser acceptance tests.  It
uses exactly the same durable claim and executor path as an external worker;
it is not an HTTP shortcut and never publishes provider results.
"""
from __future__ import annotations

import asyncio
import logging

from src.core.exceptions import ConflictError, NotFoundError, ValidationError_
from src.domain.runtime.worker import RuntimeWorker
from src.domain.workflow.business_node_service import BUSINESS_NODE_CATALOG

logger = logging.getLogger(__name__)


async def run_embedded_business_worker(stop: asyncio.Event) -> None:
    worker = RuntimeWorker()
    # The embedded runner never competes with provider/recipe/control workers.
    # Filter before leasing so it cannot disturb their durable attempts.
    business_types = {str(item["type_id"]) for item in BUSINESS_NODE_CATALOG}
    while not stop.is_set():
        try:
            claim = worker.claim_next_attempt(
                "embedded-business-worker", node_type_ids=business_types,
            )
            if claim is None:
                await asyncio.sleep(0.12)
                continue
            try:
                worker.execute_business_attempt(claim.attempt.attempt_id)
            except (ConflictError, NotFoundError, ValidationError_) as exc:
                # This is a malformed public-business attempt, not another
                # worker's queue item: the type filter above is authoritative.
                logger.warning("embedded business worker failed attempt %s: %s", claim.attempt.attempt_id, exc)
        except Exception:  # keep optional demo infrastructure from killing API
            logger.exception("embedded business worker iteration failed")
            await asyncio.sleep(0.25)
