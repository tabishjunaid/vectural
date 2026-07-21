"""The quota governor (§5.7, §4.3 sequence).

Gates indexing against the weekly tranche + serving reserve, and smooths serving
within a day with a token bucket. It does not itself spend — it *decides*; the
router records the actual spend into the same pool (via the accountant), so the
governor's next decision sees current numbers (§5.1: accounting is not batched).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from backend.quota.ledger import QuotaPool


@dataclass
class BudgetDecision:
    proceed: bool
    reason: str
    available: int


class TokenBucket:
    """Classic token bucket for intra-day serving smoothing, so interactive
    latency stays stable rather than spiking when a burst arrives (§5.7)."""

    def __init__(
        self, capacity: float, refill_per_second: float, *, start_full: bool = True
    ) -> None:
        self.capacity = capacity
        self.rate = refill_per_second
        self.tokens = capacity if start_full else 0.0
        self._last: float | None = None

    @classmethod
    def per_day(cls, daily_amount: float) -> TokenBucket:
        return cls(capacity=daily_amount, refill_per_second=daily_amount / 86_400.0)

    def try_consume(self, amount: float, now: float) -> bool:
        self._refill(now)
        if self.tokens >= amount:
            self.tokens -= amount
            return True
        return False

    def refill_full(self) -> None:
        self.tokens = self.capacity
        self._last = None

    def _refill(self, now: float) -> None:
        if self._last is None:
            self._last = now
            return
        elapsed = max(0.0, now - self._last)
        self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
        self._last = now


class QuotaGovernor:
    def __init__(self, pool: QuotaPool, *, serving_bucket: TokenBucket | None = None) -> None:
        self.pool = pool
        # Default: smooth the monthly serving reserve across ~30 days.
        self._bucket = serving_bucket or TokenBucket.per_day(pool.config.serving_reserve / 30.0)

    def request_indexing_budget(self, cost: int, today: date) -> BudgetDecision:
        """Approve indexing spend only if it fits the tokens unlocked *so far*.

        A hold is not an error (§5.8) — the unspent tranche capacity rolls
        forward, and the caller parks on a durable timer. A future tranche is
        never borrowed against."""
        available = self.pool.indexing_available(today)
        if cost <= available:
            return BudgetDecision(True, "within current tranche", available)
        return BudgetDecision(
            False,
            f"tranche exhausted: need {cost}, have {available} (rolls forward, not borrowed)",
            available,
        )

    def check_serving(self, cost: int, *, now: float, today: date) -> BudgetDecision:
        """Serving is ring-fenced: it draws its reserve and is rate-smoothed, and
        is never contended by indexing."""
        reserve_available = self.pool.serving_available()
        if cost > reserve_available:
            return BudgetDecision(False, "serving reserve exhausted", reserve_available)
        if not self._bucket.try_consume(cost, now):
            return BudgetDecision(False, "serving rate limited (token bucket)", reserve_available)
        return BudgetDecision(True, "within serving reserve", reserve_available)

    def monthly_reset(self, new_period_start: date) -> None:
        self.pool.reset(new_period_start)
        self._bucket.refill_full()


@dataclass
class BinPackResult:
    schedule: dict[int, list[str]]  # week index -> service names
    unscheduled: list[str]  # cost exceeds total remaining capacity this month


def bin_pack(
    services: list[tuple[str, int]], tranche_capacity: int, tranche_count: int
) -> BinPackResult:
    """First-fit-decreasing pack of services (by measured cost) into weekly
    tranches (§5.7). Services that fit no remaining tranche are returned as
    ``unscheduled`` — they roll into a later month rather than silently dropping."""
    bins = [tranche_capacity] * tranche_count
    schedule: dict[int, list[str]] = {i: [] for i in range(tranche_count)}
    unscheduled: list[str] = []

    for name, cost in sorted(services, key=lambda s: (-s[1], s[0])):
        placed = False
        for week in range(tranche_count):
            if bins[week] >= cost:
                bins[week] -= cost
                schedule[week].append(name)
                placed = True
                break
        if not placed:
            unscheduled.append(name)
    return BinPackResult(schedule=schedule, unscheduled=unscheduled)
