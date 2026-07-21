"""System-of-record models + repos + DDL (§3.3)."""

from __future__ import annotations

from datetime import UTC, datetime

from backend.persistence import (
    DDL_STATEMENTS,
    DeadLetterEntry,
    FileLedgerEntry,
    InMemoryDeadLetter,
    InMemoryFileLedger,
    schema_sql,
)
from backend.persistence.file_ledger import FileStatus

NOW = datetime(2026, 7, 1, tzinfo=UTC)


def _entry(content_hash: str = "h1", prompt_version: str = "file-v1") -> FileLedgerEntry:
    return FileLedgerEntry(
        service="payments",
        path="payments/refund.py",
        content_hash=content_hash,
        prompt_version=prompt_version,
        tier=1,
        status=FileStatus.SUMMARISED,
        updated_at=NOW,
    )


def test_file_ledger_upsert_and_get() -> None:
    ledger = InMemoryFileLedger()
    assert ledger.get("payments", "payments/refund.py") is None
    ledger.upsert(_entry())
    got = ledger.get("payments", "payments/refund.py")
    assert got is not None and got.tier == 1


def test_matches_only_when_content_and_prompt_agree() -> None:
    entry = _entry(content_hash="h1", prompt_version="file-v1")
    assert entry.matches("h1", "file-v1")
    assert not entry.matches("h2", "file-v1")  # content changed
    assert not entry.matches("h1", "file-v2")  # prompt bumped


def test_dead_lettered_entry_never_matches() -> None:
    entry = _entry().model_copy(update={"status": FileStatus.DEAD_LETTERED})
    assert not entry.matches("h1", "file-v1")


def test_dead_letter_repo_appends() -> None:
    dl = InMemoryDeadLetter()
    dl.add(DeadLetterEntry(service="s", path="s/x.py", kind="parse_error", detail=None, at=NOW))
    assert len(dl.all()) == 1
    assert dl.all()[0].kind == "parse_error"


def test_schema_covers_system_of_record_tables() -> None:
    sql = schema_sql()
    for table in (
        "repo_state",
        "file_ledger",
        "quota_ledger",
        "answer_cache",
        "eval_runs",
        "dead_letter",
        "coverage_manifest",
    ):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in sql
    assert len(DDL_STATEMENTS) == 7
