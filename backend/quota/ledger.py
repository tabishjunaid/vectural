"""The quota pool state + the accountant that the router writes spend into.

``QuotaAccountant`` is a :class:`~backend.llm.router.TokenSink`: the router hands
it every :class:`UsageRecord`, and it decrements the **one** shared pool. It
classifies spend as indexing vs serving by task type only to enforce the serving
reserve — the underlying budget is a single counter (§4.3).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from backend.domain.models import TaskType
from backend.llm.base import UsageRecord

# Tasks that spend on *building* the index vs. *serving* an interactive query.
# Only used to keep the serving reserve ring-fenced; both draw the same pool.
_INDEXING_TASKS: frozenset[TaskType] = frozenset(
    {
        TaskType.FILE_SUMMARY,
        TaskType.MODULE_SUMMARY,
        TaskType.SERVICE_SUMMARY,
        TaskType.FLOW_NARRATIVE,
    }
)


def is_indexing_task(task_type: TaskType) -> bool:
    return task_type in _INDEXING_TASKS


@dataclass(frozen=True)
class QuotaConfig:
    monthly_budget: int
    serving_reserve_fraction: float = 0.30  # 30% ring-fenced for interactive (§5.7)
    tranche_count: int = 4  # weekly tranches within the month

    def __post_init__(self) -> None:
        if not 0.0 <= self.serving_reserve_fraction < 1.0:
            raise ValueError("serving_reserve_fraction must be in [0, 1)")
        if self.monthly_budget <= 0 or self.tranche_count <= 0:
            raise ValueError("monthly_budget and tranche_count must be positive")

    @property
    def indexing_budget(self) -> int:
        return int(self.monthly_budget * (1.0 - self.serving_reserve_fraction))

    @property
    def serving_reserve(self) -> int:
        return self.monthly_budget - self.indexing_budget

    @property
    def weekly_tranche(self) -> int:
        return self.indexing_budget // self.tranche_count


@dataclass
class QuotaPool:
    """Mutable pool state for one monthly period. Persisted as the ``quota_ledger``
    row (§3.3)."""

    config: QuotaConfig
    period_start: date
    spent_indexing: int = 0
    spent_serving: int = 0

    @property
    def spent_total(self) -> int:
        return self.spent_indexing + self.spent_serving

    @property
    def remaining_total(self) -> int:
        return max(0, self.config.monthly_budget - self.spent_total)

    def week_index(self, today: date) -> int:
        """0-indexed week within the period, clamped to the last tranche."""
        days = (today - self.period_start).days
        if days < 0:
            return 0
        return min(days // 7, self.config.tranche_count - 1)

    def cumulative_indexing_allowance(self, today: date) -> int:
        """Indexing tokens unlocked so far: tranches for every elapsed week.

        This is what lets **unspent tranche capacity roll forward** (it is
        cumulative) while a future tranche is **never borrowed against** (only
        weeks up to and including the current one are counted)."""
        return self.config.weekly_tranche * (self.week_index(today) + 1)

    def indexing_available(self, today: date) -> int:
        return max(0, self.cumulative_indexing_allowance(today) - self.spent_indexing)

    def serving_available(self) -> int:
        return max(0, self.config.serving_reserve - self.spent_serving)

    def reset(self, new_period_start: date) -> None:
        """Monthly replenishment: new period, counters cleared (§4.3)."""
        self.period_start = new_period_start
        self.spent_indexing = 0
        self.spent_serving = 0


@dataclass
class QuotaAccountant:
    """A ``TokenSink`` that records router spend into the shared pool."""

    pool: QuotaPool
    recorded: list[UsageRecord] = field(default_factory=list)

    def record(self, usage: UsageRecord) -> None:
        self.recorded.append(usage)
        if is_indexing_task(usage.task_type):
            self.pool.spent_indexing += usage.total_tokens
        else:
            self.pool.spent_serving += usage.total_tokens
