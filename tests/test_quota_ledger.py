"""Integration test for PgQuotaLedger — the durable shared quota pool (§3.3, §5.7).

Env-guarded like tests/test_real_adapters.py so the offline suite stays green:

    docker compose --profile datastores up -d postgres
    VECTURAL_RUN_INTEGRATION=1 uv run pytest tests/test_quota_ledger.py
"""

from __future__ import annotations

import os
import socket
from datetime import date

import pytest

from backend.quota.ledger import QuotaConfig, QuotaPool

INTEGRATION = os.environ.get("VECTURAL_RUN_INTEGRATION") == "1"
pytestmark = pytest.mark.skipif(not INTEGRATION, reason="set VECTURAL_RUN_INTEGRATION=1 to run")

PG_DSN = os.environ.get(
    "VECTURAL_POSTGRES_DSN", "postgresql://vectural:vectural@localhost:5432/vectural"
)


def _reachable(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except OSError:
        return False


@pytest.mark.skipif(not _reachable("localhost", 5432), reason="postgres not reachable")
def test_quota_ledger_roundtrip_and_resume() -> None:
    from backend.persistence.postgres import apply_schema, open_connection
    from backend.persistence.quota_ledger import PgQuotaLedger

    conn = open_connection(PG_DSN)
    apply_schema(conn)
    period = date(2026, 7, 1)
    with conn.cursor() as cur:
        cur.execute("DELETE FROM quota_ledger WHERE period_start = %s", (period,))

    ledger = PgQuotaLedger(conn, tranche_count=4)
    config = QuotaConfig(monthly_budget=1_000_000, serving_reserve_fraction=0.30)

    # First run: create + spend + persist.
    pool = ledger.load_or_create(config, period)
    assert pool.spent_indexing == 0
    pool.spent_indexing += 12_345
    pool.spent_serving += 678
    ledger.upsert(pool)

    # Second "process": reload — spend must survive (resume without re-spend).
    reloaded = ledger.latest()
    assert reloaded is not None
    assert reloaded.spent_indexing == 12_345
    assert reloaded.spent_serving == 678
    assert reloaded.config.monthly_budget == 1_000_000
    assert reloaded.config.tranche_count == 4  # reattached from config, not persisted

    # load_or_create returns the existing pool (does not reset spend).
    again = ledger.load_or_create(config, period)
    assert again.spent_indexing == 12_345


@pytest.mark.skipif(not _reachable("localhost", 5432), reason="postgres not reachable")
def test_quota_pool_is_the_gate_governor_reads() -> None:
    # A reloaded pool drives the governor's tranche decision (§5.7).
    from backend.persistence.postgres import apply_schema, open_connection
    from backend.persistence.quota_ledger import PgQuotaLedger
    from backend.quota.governor import QuotaGovernor

    conn = open_connection(PG_DSN)
    apply_schema(conn)
    period = date(2026, 7, 1)
    with conn.cursor() as cur:
        cur.execute("DELETE FROM quota_ledger WHERE period_start = %s", (period,))

    ledger = PgQuotaLedger(conn)
    config = QuotaConfig(monthly_budget=1_000_000, serving_reserve_fraction=0.30, tranche_count=4)
    pool: QuotaPool = ledger.load_or_create(config, period)
    governor = QuotaGovernor(pool)

    # Week 0 unlocks one weekly tranche = indexing_budget/4 = 700_000/4 = 175_000.
    ok = governor.request_indexing_budget(100_000, period)
    assert ok.proceed
    too_much = governor.request_indexing_budget(1_000_000, period)
    assert not too_much.proceed  # beyond the unlocked tranche → park
