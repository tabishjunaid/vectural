"""Real Temporal adapter (optional ``temporal`` extra) — the infra seam.

This is the thin layer that runs the deterministic logic in :mod:`workflow` under
a real Temporal worker. It is import-guarded (``temporalio`` is optional) so the
orchestration logic stays testable with no Temporal server.

Mapping:
- the Temporal **workflow** holds :class:`WorkflowState` in history and calls the
  child **activity** per service (deterministic — it must not do I/O itself)
- the **activity** is :class:`SummariseServiceActivities.summarise_service` (all
  the gateway/ledger I/O lives here, with Temporal's retry policy around it)
- a durable **timer** implements the quota park until the monthly replenishment
- **continue-as-new** is invoked at the weekly tranche boundary for history hygiene

The functions below build the worker; the workflow body is intentionally a small
translation of :func:`run_indexing`, kept here rather than in the pure module so
the pure module never imports ``temporalio``.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from backend.orchestration.temporal_activities import IndexingActivities


def build_worker(
    client: Any,
    task_queue: str,
    *,
    activities: IndexingActivities,
    max_workers: int = 4,
) -> Any:
    """Construct a Temporal worker for the :class:`IndexingWorkflow`.

    Registers the workflow plus the three sync activities (run in a thread-pool
    executor since they do blocking I/O). Durability — history, quota-park timers,
    continue-as-new, activity retries — is delegated to Temporal; the workflow body
    stays deterministic (:mod:`backend.orchestration.temporal_workflow`)."""
    from temporalio.worker import Worker

    from backend.orchestration.temporal_workflow import IndexingWorkflow

    return Worker(
        client,
        task_queue=task_queue,
        workflows=[IndexingWorkflow],
        activities=[
            activities.request_budget,
            activities.index_service,
            activities.finalize,
        ],
        activity_executor=ThreadPoolExecutor(max_workers=max_workers),
    )
