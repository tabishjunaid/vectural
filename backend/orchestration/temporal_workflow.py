"""The Temporal indexing workflow — durable, resumable, quota-partitioned (§5.7).

Deterministic by construction: it holds the pending-service queue in workflow
history and only *orchestrates* activities (all I/O lives in
:mod:`backend.orchestration.temporal_activities`). Durability comes from Temporal:

  * **checkpoint/resume** — completed services live in history, so a worker crash
    resumes exactly where it stopped; activity idempotency covers retries.
  * **quota park** — when the governor holds, the workflow sleeps on a durable
    timer (``asyncio.sleep``) until the next weekly tranche unlocks, then retries.
  * **continue-as-new** — at each tranche boundary, history is compacted by
    restarting the workflow with the remaining services.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

from temporalio import workflow

_REQUEST_BUDGET = "request_budget"
_INDEX_SERVICE = "index_service"
_FINALIZE = "finalize"


@workflow.defn
class IndexingWorkflow:
    @workflow.run
    async def run(
        self,
        pending: list[str],
        services_per_run: int,
        park_backoff_seconds: int,
    ) -> dict[str, object]:
        pending = list(pending)
        completed: list[str] = []
        processed = 0

        while pending:
            service = pending[0]
            today_iso = workflow.now().isoformat()

            decision = await workflow.execute_activity(
                _REQUEST_BUDGET, args=[service, today_iso],
                start_to_close_timeout=timedelta(seconds=60),
            )
            if not decision["proceed"]:
                # Quota hold — park on a durable timer until a tranche unlocks (§5.8).
                await asyncio.sleep(park_backoff_seconds)
                continue

            await workflow.execute_activity(
                _INDEX_SERVICE, args=[service, today_iso],
                start_to_close_timeout=timedelta(minutes=30),
            )
            pending.pop(0)
            completed.append(service)
            processed += 1

            # Tranche boundary: compact history by continuing as new with the rest.
            if processed >= services_per_run and pending:
                workflow.continue_as_new(
                    args=[pending, services_per_run, park_backoff_seconds]
                )

        # Estate fully indexed → higher tiers, flows, cross-service edges (once).
        await workflow.execute_activity(
            _FINALIZE, args=[workflow.now().isoformat()],
            start_to_close_timeout=timedelta(minutes=30),
        )
        return {"completed": completed}
