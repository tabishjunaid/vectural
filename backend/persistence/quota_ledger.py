"""The shared quota pool over Postgres (§3.3, §5.7).

The ``quota_ledger`` row is the single source of truth for token spend — read and
written by **both** the indexing worker (which spends the indexing tranche) and the
serving API (which spends the reserve). Keeping it in Postgres is what lets a durable,
resumable indexing run pick up exactly where it left off: ``spent_indexing`` survives
worker restarts, continue-as-new, and monthly boundaries.

``tranche_count`` is not persisted (it is a policy knob, not spend state); it comes
from configuration and is reattached to the reconstructed :class:`QuotaConfig`.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Any

from backend.quota.ledger import QuotaConfig, QuotaPool

if TYPE_CHECKING:
    from psycopg import Connection


class PgQuotaLedger:
    """``quota_ledger`` over Postgres — load/persist the shared :class:`QuotaPool`."""

    def __init__(self, conn: Connection, *, tranche_count: int = 4) -> None:
        self._conn = conn
        self._tranche_count = tranche_count

    def get(self, period_start: date) -> QuotaPool | None:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT period_start, monthly_budget, serving_reserve_fraction, "
                "spent_indexing, spent_serving FROM quota_ledger WHERE period_start = %s",
                (period_start,),
            )
            row = cur.fetchone()
        return self._to_pool(row) if row is not None else None

    def latest(self) -> QuotaPool | None:
        """The most recent period's pool (the 'current' pool at startup)."""
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT period_start, monthly_budget, serving_reserve_fraction, "
                "spent_indexing, spent_serving FROM quota_ledger "
                "ORDER BY period_start DESC LIMIT 1"
            )
            row = cur.fetchone()
        return self._to_pool(row) if row is not None else None

    def upsert(self, pool: QuotaPool) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                "INSERT INTO quota_ledger "
                "(period_start, monthly_budget, serving_reserve_fraction, "
                " spent_indexing, spent_serving) VALUES (%s, %s, %s, %s, %s) "
                "ON CONFLICT (period_start) DO UPDATE SET "
                "monthly_budget = EXCLUDED.monthly_budget, "
                "serving_reserve_fraction = EXCLUDED.serving_reserve_fraction, "
                "spent_indexing = EXCLUDED.spent_indexing, "
                "spent_serving = EXCLUDED.spent_serving, updated_at = now()",
                (
                    pool.period_start, pool.config.monthly_budget,
                    pool.config.serving_reserve_fraction,
                    pool.spent_indexing, pool.spent_serving,
                ),
            )

    def load_or_create(self, config: QuotaConfig, period_start: date) -> QuotaPool:
        """Return the current persisted pool, or create + persist a fresh one.

        Used at worker/API startup so both share one durable pool. The persisted
        budget/reserve win over ``config`` if a row already exists (spend state is
        authoritative); only ``tranche_count`` is taken from ``config``."""
        existing = self.latest()
        if existing is not None:
            return existing
        pool = QuotaPool(config=config, period_start=period_start)
        self.upsert(pool)
        return pool

    def _to_pool(self, row: tuple[Any, ...]) -> QuotaPool:
        config = QuotaConfig(
            monthly_budget=int(row[1]),
            serving_reserve_fraction=float(row[2]),
            tranche_count=self._tranche_count,
        )
        return QuotaPool(
            config=config, period_start=row[0],
            spent_indexing=int(row[3]), spent_serving=int(row[4]),
        )
