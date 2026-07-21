"""Quota governance (§5.7, §4.3 invariants)."""

from __future__ import annotations

from datetime import date

import pytest

from backend.domain.models import Persona, TaskType
from backend.llm import FakeGatewayClient, LLMRouter
from backend.quota import (
    QuotaAccountant,
    QuotaConfig,
    QuotaGovernor,
    QuotaPool,
    TokenBucket,
    bin_pack,
    is_indexing_task,
)

JULY = date(2026, 7, 1)


def _pool(budget: int = 1000, reserve: float = 0.30) -> QuotaPool:
    return QuotaPool(QuotaConfig(budget, reserve, 4), period_start=JULY)


def test_config_splits_budget() -> None:
    cfg = QuotaConfig(1000, 0.30, 4)
    assert cfg.indexing_budget == 700
    assert cfg.serving_reserve == 300
    assert cfg.weekly_tranche == 175


def test_tranches_unlock_weekly_and_roll_forward() -> None:
    pool = _pool()
    assert pool.indexing_available(JULY) == 175  # week 0: one tranche
    assert pool.indexing_available(date(2026, 7, 8)) == 350  # week 1: two, unspent rolled
    assert pool.indexing_available(date(2026, 7, 22)) == 700  # week 3: all four


def test_tranche_never_borrowed_forward() -> None:
    gov = QuotaGovernor(_pool())
    # Week 0 has only 175 unlocked; a 300 request is held, not borrowed from later.
    decision = gov.request_indexing_budget(300, JULY)
    assert not decision.proceed
    assert "rolls forward" in decision.reason
    assert gov.request_indexing_budget(175, JULY).proceed


def test_one_shared_counter_across_models() -> None:
    # A Haiku (indexing) and a Sonnet (serving) call decrement the same pool.
    pool = _pool(budget=100_000)
    router = LLMRouter(FakeGatewayClient(), sinks=[QuotaAccountant(pool)])
    router.route(TaskType.FILE_SUMMARY, "v", {"prompt": "a b c"})  # haiku
    router.route(TaskType.SYNTHESIS, "v", {"prompt": "x y"}, Persona.ARCHITECT)  # sonnet
    assert pool.spent_indexing > 0
    assert pool.spent_serving > 0
    assert pool.spent_total == pool.spent_indexing + pool.spent_serving


def test_is_indexing_task_classification() -> None:
    assert is_indexing_task(TaskType.FILE_SUMMARY)
    assert is_indexing_task(TaskType.FLOW_NARRATIVE)
    assert not is_indexing_task(TaskType.SYNTHESIS)
    assert not is_indexing_task(TaskType.GROUNDEDNESS)


def test_serving_reserve_is_ring_fenced() -> None:
    pool = _pool(budget=1000)  # serving reserve = 300
    gov = QuotaGovernor(pool, serving_bucket=TokenBucket(capacity=1e9, refill_per_second=0))
    assert gov.check_serving(250, now=0.0, today=JULY).proceed
    pool.spent_serving = 300
    assert not gov.check_serving(1, now=1.0, today=JULY).proceed  # reserve exhausted


def test_serving_token_bucket_smooths_bursts() -> None:
    pool = _pool(budget=100_000)
    bucket = TokenBucket(capacity=100, refill_per_second=10)
    gov = QuotaGovernor(pool, serving_bucket=bucket)
    assert gov.check_serving(100, now=0.0, today=JULY).proceed  # drains the bucket
    assert not gov.check_serving(50, now=0.0, today=JULY).proceed  # no refill yet
    assert gov.check_serving(50, now=5.0, today=JULY).proceed  # 5s * 10 = 50 refilled


def test_monthly_reset_clears_counters() -> None:
    pool = _pool()
    pool.spent_indexing = 500
    gov = QuotaGovernor(pool)
    gov.monthly_reset(date(2026, 8, 1))
    assert pool.spent_indexing == 0
    assert pool.period_start == date(2026, 8, 1)


def test_bin_pack_first_fit_decreasing() -> None:
    result = bin_pack(
        [("a", 100), ("b", 60), ("c", 40), ("d", 30)], tranche_capacity=100, tranche_count=4
    )
    # Largest first: a fills week0; b+c+d don't all fit one bin, spread across weeks.
    placed = {svc for week in result.schedule.values() for svc in week}
    assert placed == {"a", "b", "c", "d"}
    assert not result.unscheduled


def test_bin_pack_reports_unschedulable() -> None:
    result = bin_pack([("huge", 500)], tranche_capacity=100, tranche_count=4)
    assert result.unscheduled == ["huge"]


def test_config_rejects_bad_reserve() -> None:
    with pytest.raises(ValueError, match="serving_reserve_fraction"):
        QuotaConfig(1000, 1.5, 4)
