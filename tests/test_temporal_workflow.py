"""The Temporal IndexingWorkflow: park→resume, continue-as-new, no re-spend (§5.7).

Uses Temporal's time-skipping test environment (no external server; it downloads a
lightweight test binary on first use). Skips gracefully if temporalio or the test
server is unavailable, so the offline suite stays green. The workflow's *decision*
logic is also covered by the pure tests in test_orchestration.py; here we prove the
real Temporal host (durable timers, continue-as-new) behaves the same.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import date, datetime

import pytest

temporalio = pytest.importorskip("temporalio")

from temporalio import activity  # noqa: E402

from backend.orchestration.temporal import build_worker  # noqa: E402
from backend.quota.governor import QuotaGovernor  # noqa: E402
from backend.quota.ledger import QuotaConfig, QuotaPool  # noqa: E402

WEEK_SECONDS = 7 * 24 * 3600


@dataclass
class _StubActivities:
    """Controllable stand-in for IndexingActivities — deterministic costs/spend,
    so we can drive the workflow's quota-park and tranche paths precisely."""

    pool: QuotaPool
    cost: int = 100
    indexed: list[str] = field(default_factory=list)
    finalized: int = 0

    def __post_init__(self) -> None:
        self.governor = QuotaGovernor(self.pool)

    @activity.defn(name="request_budget")
    def request_budget(self, service: str, today_iso: str) -> dict[str, object]:
        today = datetime.fromisoformat(today_iso).date()
        d = self.governor.request_indexing_budget(self.cost, today)
        return {"proceed": d.proceed, "reason": d.reason or "", "available": d.available}

    @activity.defn(name="index_service")
    def index_service(self, service: str, today_iso: str) -> dict[str, object]:
        self.indexed.append(service)
        self.pool.spent_indexing += self.cost  # simulate the gateway spend
        return {"service": service}

    @activity.defn(name="finalize")
    def finalize(self, today_iso: str) -> dict[str, object]:
        self.finalized += 1
        return {"finalized": True}


async def _run_workflow(stub: _StubActivities, services: list[str], *,
                        tranche_size: int, park_backoff: int, wid: str) -> dict:
    from temporalio.testing import WorkflowEnvironment

    try:
        env = await WorkflowEnvironment.start_time_skipping()
    except Exception as exc:  # test server unavailable (e.g. offline)
        pytest.skip(f"Temporal test server unavailable: {exc}")

    async with env:
        worker = build_worker(env.client, "vectural-indexing-test", activities=stub)
        async with worker:
            return await env.client.execute_workflow(
                "IndexingWorkflow",
                args=[services, tranche_size, park_backoff],
                id=wid,
                task_queue="vectural-indexing-test",
            )


def _pool(monthly: int) -> QuotaPool:
    return QuotaPool(
        QuotaConfig(monthly_budget=monthly, serving_reserve_fraction=0.30, tranche_count=4),
        period_start=date.today(),
    )


def test_happy_path_indexes_all_then_finalizes() -> None:
    stub = _StubActivities(pool=_pool(100_000_000))
    result = asyncio.run(_run_workflow(
        stub, ["a", "b", "c"], tranche_size=10, park_backoff=1, wid="wf-happy"
    ))
    assert result["completed"] == ["a", "b", "c"]
    assert stub.indexed == ["a", "b", "c"]
    assert stub.finalized == 1  # finalize runs exactly once, at the end


def test_quota_hold_parks_then_resumes_next_tranche_without_respend() -> None:
    # weekly_tranche == cost (monthly 572 → indexing 400 → /4 = 100), so week 0
    # affords exactly one service; the second parks until the next week unlocks.
    stub = _StubActivities(pool=_pool(572), cost=100)
    result = asyncio.run(_run_workflow(
        stub, ["a", "b"], tranche_size=10, park_backoff=WEEK_SECONDS, wid="wf-park"
    ))
    assert result["completed"] == ["a", "b"]
    assert stub.indexed == ["a", "b"]
    assert stub.finalized == 1
    assert stub.pool.spent_indexing == 200  # each service billed once — no re-spend


def test_continue_as_new_at_tranche_boundary_preserves_order() -> None:
    # tranche_size=1 forces continue-as-new after every service; the run must still
    # complete every service exactly once, in order.
    stub = _StubActivities(pool=_pool(100_000_000))
    result = asyncio.run(_run_workflow(
        stub, ["a", "b", "c"], tranche_size=1, park_backoff=1, wid="wf-can"
    ))
    assert result["completed"] == ["c"]  # last generation only sees the final service
    assert stub.indexed == ["a", "b", "c"]  # but every service was indexed once
    assert stub.finalized == 1
