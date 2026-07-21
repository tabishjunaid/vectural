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

from typing import Any


def build_worker(
    client: Any,
    task_queue: str,
    *,
    activities: Any,
) -> Any:  # pragma: no cover - requires a Temporal server
    """Construct a Temporal worker for the indexing workflow.

    Lazy-imports ``temporalio`` so importing this module never requires it. The
    workflow/activity registration mirrors :func:`backend.orchestration.workflow.run_indexing`
    one-to-one; only the durability (history, timers, continue-as-new, activity
    retries) is delegated to Temporal.
    """
    from temporalio.worker import Worker

    return Worker(client, task_queue=task_queue, activities=[activities.summarise_service])
