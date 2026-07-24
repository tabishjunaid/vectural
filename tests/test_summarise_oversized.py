"""Oversized files must dead-letter, never abort the batch (§5.8).

The regression these cover: a 730 KB generated ``graph.json`` rendered a ~214k-token
tier-1 prompt, the provider rejected it with a permanent 400, the raw SDK error escaped
the driver, and Temporal retried the *whole service* forever. One unsummarisable file
took down all eight services. A file too big for the context window is a per-item
content failure — the batch carries on.
"""

from __future__ import annotations

import math
from datetime import UTC, date, datetime

from backend.llm import FakeGatewayClient, LLMRouter
from backend.persistence import InMemoryDeadLetter, InMemoryFileLedger
from backend.quota import QuotaAccountant, QuotaConfig, QuotaGovernor, QuotaPool
from backend.summarise import FileToSummarise, summarise_files
from backend.summarise.driver import SummariseOutcome
from backend.summarise.tiers import GUARD_CHARS_PER_TOKEN, estimate_prompt_tokens

NOW = datetime(2026, 7, 1, tzinfo=UTC)

SMALL = FileToSummarise("ledger", "ledger/ledger.py", "def apply_charge(a): save(a)")


def _rig(budget: int = 100_000_000):
    pool = QuotaPool(QuotaConfig(budget, 0.30, 4), period_start=date(2026, 7, 1))
    return pool, QuotaGovernor(pool), InMemoryFileLedger(), InMemoryDeadLetter()


def _oversized(limit: int) -> FileToSummarise:
    """A file whose rendered prompt certainly exceeds ``limit`` tokens."""
    return FileToSummarise("synapse", "synapse/graph.json", "x" * int(limit * 4 * 3))


def test_oversized_file_dead_letters_and_batch_continues() -> None:
    """The load-bearing case: the poison file is dead-lettered, the *next* file in
    the same batch still summarises, and nothing is spent on the oversized one."""
    limit = 1_000
    pool, gov, fl, dl = _rig()
    router = LLMRouter(FakeGatewayClient(), sinks=[QuotaAccountant(pool)])

    report = summarise_files(
        [_oversized(limit), SMALL],
        router=router, governor=gov, file_ledger=fl, dead_letter=dl,
        today=NOW, max_input_tokens=limit,
    )

    assert report.counts() == {"dead_lettered": 1, "summarised": 1}
    # The file that followed the poison one really did get summarised.
    assert "ledger:ledger/ledger.py" in report.summaries
    rows = {r.path: r for r in report.rows}
    assert rows["synapse/graph.json"].outcome is SummariseOutcome.DEAD_LETTERED
    assert rows["synapse/graph.json"].tokens == 0  # never sent, so never billed


def test_oversized_file_is_recorded_for_review() -> None:
    limit = 1_000
    _, gov, fl, dl = _rig()
    router = LLMRouter(FakeGatewayClient(), sinks=[])

    summarise_files(
        [_oversized(limit)],
        router=router, governor=gov, file_ledger=fl, dead_letter=dl,
        today=NOW, max_input_tokens=limit,
    )

    entries = dl.all()
    assert len(entries) == 1
    assert entries[0].kind == "oversized_file"


def test_normal_files_are_unaffected_by_the_guard() -> None:
    """The default budget must not disturb ordinary source files."""
    pool, gov, fl, dl = _rig()
    router = LLMRouter(FakeGatewayClient(), sinks=[QuotaAccountant(pool)])
    report = summarise_files(
        [SMALL], router=router, governor=gov, file_ledger=fl, dead_letter=dl, today=NOW
    )
    assert report.counts() == {"summarised": 1}


def test_guard_estimator_is_character_based_not_word_based() -> None:
    """Why the guard cannot reuse ``estimate_tier1_cost``: dense machine-generated
    text has almost no whitespace, so a word-count proxy under-reports it by ~5x —
    exactly the shape of file that blows the context window."""
    minified = '{"a":1,"b":2,"c":3}' * 20_000  # ~380 KB, essentially no spaces

    assert estimate_prompt_tokens(minified) == math.ceil(len(minified) / GUARD_CHARS_PER_TOKEN)
    # The character-based estimate is many times the naive word count, which is the
    # whole reason the guard cannot be built on estimate_tier1_cost.
    assert estimate_prompt_tokens(minified) > 5 * len(minified.split())


def test_empty_prompt_estimates_zero() -> None:
    assert estimate_prompt_tokens("") == 0


def test_file_summary_accepts_shapes_the_model_actually_returns() -> None:
    """Regression: three real tier-1 failures against gpt-4o-mini. The prompt asks
    for JSON keys without pinning the array element type, so the model answered with
    a bare string and with objects — each rejection wasted a paid call and left the
    file unsummarised."""
    from backend.summarise.tiers import FileSummary

    # Observed on vectural/backend/__init__.py — a bare string, not a list.
    s1 = FileSummary.model_validate(
        {"purpose": "package init", "key_operations": "Ingestion of data without a datastore."}
    )
    assert s1.key_operations == ["Ingestion of data without a datastore."]

    # Observed on vectural/backend/answer/cache.py and api/app.py — lists of objects.
    s2 = FileSummary.model_validate(
        {
            "purpose": "cache",
            "key_operations": [{"operation": "get", "description": "Return cached answer."}],
            "external_calls": [{"method": "GET", "endpoint": "/healthz"}],
        }
    )
    assert s2.key_operations == ["operation=get, description=Return cached answer."]
    assert s2.external_calls == ["method=GET, endpoint=/healthz"]

    # The ordinary shape still passes through untouched.
    s3 = FileSummary.model_validate({"purpose": "p", "key_operations": ["a", "b"]})
    assert s3.key_operations == ["a", "b"]
