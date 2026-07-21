"""Durable indexing workflow: resume without duplicate spend (§5.7, Phase 5)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime

import pytest

from backend.llm import FakeGatewayClient, LLMRouter
from backend.orchestration import (
    IndexingDeps,
    InMemoryWorkflowStore,
    Outcome,
    SummariseServiceActivities,
    WorkflowState,
    drive,
)
from backend.persistence import InMemoryDeadLetter, InMemoryFileLedger
from backend.persistence.file_ledger import FileStatus
from backend.quota import QuotaAccountant, QuotaConfig, QuotaGovernor, QuotaPool
from backend.summarise import FileToSummarise

ORDER = ["svc-a", "svc-b", "svc-c"]
TODAY = datetime(2026, 7, 1, tzinfo=UTC)


def _files() -> dict[str, list[FileToSummarise]]:
    return {
        s: [FileToSummarise(s, f"{s}/f{i}.py", f"def {s[-1]}{i}(): return {i}") for i in range(2)]
        for s in ORDER
    }


def _pool(monthly: int = 1_000_000) -> QuotaPool:
    return QuotaPool(QuotaConfig(monthly, 0.30, 4), period_start=date(2026, 7, 1))


def _deps(
    pool: QuotaPool,
    gateway: FakeGatewayClient,
    ledger: InMemoryFileLedger,
    dead: InMemoryDeadLetter,
    *,
    checkpoint: Callable[[WorkflowState], None] | None = None,
    services_per_run: int = 1_000_000,
) -> IndexingDeps:
    router = LLMRouter(gateway, sinks=[QuotaAccountant(pool)])
    activities = SummariseServiceActivities(router, QuotaGovernor(pool), ledger, dead)
    kwargs = {} if checkpoint is None else {"checkpoint": checkpoint}
    return IndexingDeps(
        activities=activities, governor=QuotaGovernor(pool),
        services_per_run=services_per_run, **kwargs,
    )


def test_clean_run_completes_all_services() -> None:
    pool, ledger, dead = _pool(), InMemoryFileLedger(), InMemoryDeadLetter()
    state = WorkflowState.initial(ORDER)
    outcome = drive(state, _files(), _deps(pool, FakeGatewayClient(), ledger, dead), today=TODAY)
    assert outcome is Outcome.DONE
    assert state.completed_services == ORDER
    # Service atomicity: every file of every service is summarised.
    assert all(e.status is FileStatus.SUMMARISED for e in ledger.all())
    assert len(ledger.all()) == 6


def test_crash_resumes_without_duplicate_spend() -> None:
    files = _files()
    # A clean baseline spend to compare against.
    clean_pool = _pool()
    drive(
        WorkflowState.initial(ORDER), files,
        _deps(clean_pool, FakeGatewayClient(), InMemoryFileLedger(), InMemoryDeadLetter()),
        today=TODAY,
    )
    baseline = clean_pool.spent_indexing

    # A run that is killed mid-flight; pool + ledger survive the crash.
    pool, ledger, dead = _pool(), InMemoryFileLedger(), InMemoryDeadLetter()
    store = InMemoryWorkflowStore()
    state = WorkflowState.initial(ORDER)
    store.save("wf", state)
    crashing = _deps(
        pool, FakeGatewayClient(crash_after=3), ledger, dead,
        checkpoint=lambda s: store.save("wf", s),
    )
    with pytest.raises(RuntimeError, match="worker killed"):
        drive(state, files, crashing, today=TODAY)

    resumed = store.load("wf")
    assert resumed is not None
    assert resumed.completed_services == ["svc-a"]  # checkpointed after svc-a

    # Resume with a healthy gateway over the SAME pool + ledger.
    healthy = _deps(
        pool, FakeGatewayClient(), ledger, dead, checkpoint=lambda s: store.save("wf", s)
    )
    outcome = drive(resumed, files, healthy, today=TODAY)

    assert outcome is Outcome.DONE
    assert resumed.completed_services == ORDER
    # THE exit criterion: total spend equals a single clean run — nothing re-spent.
    assert pool.spent_indexing == baseline


def test_continue_as_new_at_tranche_boundary() -> None:
    pool, ledger, dead = _pool(), InMemoryFileLedger(), InMemoryDeadLetter()
    state = WorkflowState.initial(ORDER)
    outcome = drive(
        state, _files(),
        _deps(pool, FakeGatewayClient(), ledger, dead, services_per_run=1),
        today=TODAY,
    )
    assert outcome is Outcome.DONE
    assert state.completed_services == ORDER
    assert state.run_count == 2  # 3 services, continue-as-new after each of the first two


def test_quota_hold_parks_then_resumes_next_week() -> None:
    files = _files()
    pool = _pool(2000)  # weekly tranche = 350: fits one service, not two in week 0
    ledger, dead = InMemoryFileLedger(), InMemoryDeadLetter()
    state = WorkflowState.initial(ORDER)

    week0 = drive(state, files, _deps(pool, FakeGatewayClient(), ledger, dead), today=TODAY)
    assert week0 is Outcome.PARKED  # not an error (§5.8)
    assert state.parked
    assert "tranche" in (state.park_reason or "")
    assert state.completed_services == ["svc-a"]  # partial progress

    # Later week unlocks more tranche → the parked work resumes to completion.
    week3 = drive(
        state, files, _deps(pool, FakeGatewayClient(), ledger, dead),
        today=datetime(2026, 7, 22, tzinfo=UTC),
    )
    assert week3 is Outcome.DONE
    assert state.completed_services == ORDER


def test_resume_makes_no_gateway_calls_for_done_services() -> None:
    # Re-driving an already-complete workflow spends nothing and calls nothing.
    pool, ledger, dead = _pool(), InMemoryFileLedger(), InMemoryDeadLetter()
    files = _files()
    state = WorkflowState.initial(ORDER)
    drive(state, files, _deps(pool, FakeGatewayClient(), ledger, dead), today=TODAY)

    gateway = FakeGatewayClient()
    outcome = drive(state, files, _deps(pool, gateway, ledger, dead), today=TODAY)
    assert outcome is Outcome.DONE
    assert gateway.calls == 0  # nothing pending
