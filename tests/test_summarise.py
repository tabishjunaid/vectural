"""Tier-1 summarisation driver — the Phase 5 spend loop (§5.2, §5.7, §5.8)."""

from __future__ import annotations

from datetime import UTC, date, datetime

from backend.llm import FakeGatewayClient, LLMRouter
from backend.persistence import InMemoryDeadLetter, InMemoryFileLedger
from backend.persistence.file_ledger import FileStatus
from backend.quota import QuotaAccountant, QuotaConfig, QuotaGovernor, QuotaPool
from backend.summarise import (
    FileToSummarise,
    content_hash,
    estimate_tier1_cost,
    summarise_files,
)

NOW = datetime(2026, 7, 1, tzinfo=UTC)


def _rig(budget: int = 100_000):
    pool = QuotaPool(QuotaConfig(budget, 0.30, 4), period_start=date(2026, 7, 1))
    gov = QuotaGovernor(pool)
    return pool, gov, InMemoryFileLedger(), InMemoryDeadLetter()


def _run(files, router, gov, fl, dl):
    return summarise_files(
        files, router=router, governor=gov, file_ledger=fl, dead_letter=dl, today=NOW
    )


FILES = [
    FileToSummarise("payments", "payments/refund.py", "def reverse_refund(id): publish(id)"),
    FileToSummarise("ledger", "ledger/ledger.py", "def apply_charge(a): save(a)"),
]


def test_first_run_summarises_all() -> None:
    pool, gov, fl, dl = _rig()
    router = LLMRouter(FakeGatewayClient(), sinks=[QuotaAccountant(pool)])
    report = _run(FILES, router, gov, fl, dl)
    assert report.counts() == {"summarised": 2}
    assert report.tokens_spent > 0
    assert len(report.summaries) == 2
    assert pool.spent_indexing == report.tokens_spent


def test_resume_skips_unchanged_without_spend() -> None:
    # THE Phase 5 exit criterion: a re-run does not re-spend on unchanged files.
    pool, gov, fl, dl = _rig()
    router = LLMRouter(FakeGatewayClient(), sinks=[QuotaAccountant(pool)])
    _run(FILES, router, gov, fl, dl)
    spent_after_first = pool.spent_indexing

    gw2 = FakeGatewayClient()
    router2 = LLMRouter(gw2, sinks=[QuotaAccountant(pool)])
    report2 = _run(FILES, router2, gov, fl, dl)
    assert report2.counts() == {"skipped": 2}
    assert gw2.calls == 0  # no gateway calls
    assert pool.spent_indexing == spent_after_first  # no additional spend


def test_changed_content_is_resummarised() -> None:
    pool, gov, fl, dl = _rig()
    router = LLMRouter(FakeGatewayClient(), sinks=[QuotaAccountant(pool)])
    _run(FILES[:1], router, gov, fl, dl)

    changed = [FileToSummarise("payments", "payments/refund.py", "def reverse_refund(id): CHANGED")]
    report = _run(changed, router, gov, fl, dl)
    assert report.counts() == {"summarised": 1}


def test_content_failure_dead_letters_and_continues() -> None:
    pool, gov, fl, dl = _rig()
    router = LLMRouter(FakeGatewayClient(malformed=True), sinks=[QuotaAccountant(pool)])
    report = _run(FILES, router, gov, fl, dl)
    assert report.counts() == {"dead_lettered": 2}  # batch not blocked
    assert len(dl.all()) == 2
    entry = fl.get("payments", "payments/refund.py")
    assert entry is not None and entry.status is FileStatus.DEAD_LETTERED


def test_quota_hold_parks_remaining_files() -> None:
    # Weekly tranche too small for all files -> once held, rest are parked.
    pool = QuotaPool(QuotaConfig(400, 0.30, 4), period_start=date(2026, 7, 1))  # tranche = 70
    gov = QuotaGovernor(pool)
    router = LLMRouter(FakeGatewayClient(), sinks=[QuotaAccountant(pool)])
    files = [FileToSummarise("s", f"s/f{i}.py", "word " * 40) for i in range(5)]
    report = summarise_files(
        files, router=router, governor=gov,
        file_ledger=InMemoryFileLedger(), dead_letter=InMemoryDeadLetter(), today=NOW,
    )
    counts = report.counts()
    assert counts.get("held", 0) >= 1
    assert sum(counts.values()) == 5  # every file accounted for


def test_cost_and_hash_helpers() -> None:
    assert content_hash("abc") == content_hash("abc")
    assert content_hash("abc") != content_hash("abd")
    assert estimate_tier1_cost("one two three") > 3  # includes fixed overhead
