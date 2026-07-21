"""The indexing workflow — deterministic orchestration over services (§5.7).

``run_indexing`` processes one tranche's worth of services then yields
``CONTINUE`` (models Temporal's continue-as-new); ``drive`` loops that to
completion. Before each service the quota governor is consulted: a hold returns
``PARKED`` (the caller waits on a durable timer until replenishment — not an
error, §5.8). Every completed service is checkpointed, so a crash mid-run
resumes from the last checkpoint and the file_ledger skip prevents any re-spend.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from backend.orchestration.activities import ServiceActivities
from backend.orchestration.state import WorkflowState
from backend.quota.governor import QuotaGovernor
from backend.summarise.driver import FileToSummarise
from backend.summarise.tiers import estimate_tier1_cost


class Outcome(StrEnum):
    DONE = "done"  # all services indexed
    PARKED = "parked"  # quota hold — waiting on the replenishment timer
    CONTINUE = "continue"  # tranche boundary — continue-as-new


def _noop(state: WorkflowState) -> None:
    return None


def _default_cost(files: list[FileToSummarise]) -> int:
    return sum(estimate_tier1_cost(f.content) for f in files) or 1


@dataclass
class IndexingDeps:
    activities: ServiceActivities
    governor: QuotaGovernor
    checkpoint: Callable[[WorkflowState], None] = _noop
    cost_of: Callable[[list[FileToSummarise]], int] = field(default=_default_cost)
    services_per_run: int = 1_000_000  # continue-as-new cadence (weekly tranche size)


def run_indexing(
    state: WorkflowState,
    services_files: dict[str, list[FileToSummarise]],
    deps: IndexingDeps,
    *,
    today: datetime,
) -> Outcome:
    """Process services until the tranche is full (CONTINUE), the quota holds
    (PARKED), or the queue is empty (DONE)."""
    processed = 0
    while state.next_service is not None:
        service = state.next_service
        files = services_files.get(service, [])

        # Service atomicity (§5.7): approve the whole service's cost before
        # starting it, so we never leave a service half-indexed for lack of budget.
        decision = deps.governor.request_indexing_budget(deps.cost_of(files), today.date())
        if not decision.proceed:
            state.parked = True
            state.park_reason = decision.reason
            deps.checkpoint(state)
            return Outcome.PARKED

        # Child activity — the only place spend happens; idempotent on resume.
        deps.activities.summarise_service(service, files, today)

        state.complete_current()
        deps.checkpoint(state)  # durable checkpoint after each completed service
        processed += 1

        if processed >= deps.services_per_run and state.next_service is not None:
            state.run_count += 1
            deps.checkpoint(state)
            return Outcome.CONTINUE

    return Outcome.DONE


def drive(
    state: WorkflowState,
    services_files: dict[str, list[FileToSummarise]],
    deps: IndexingDeps,
    *,
    today: datetime,
) -> Outcome:
    """Drive the workflow to a terminal outcome, re-entering on continue-as-new."""
    while True:
        outcome = run_indexing(state, services_files, deps, today=today)
        if outcome is Outcome.CONTINUE:
            continue
        return outcome
