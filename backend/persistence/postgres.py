"""PostgreSQL-backed repositories (psycopg3) — the real system of record (§3.3).

Behind the optional ``postgres`` extra. These satisfy the same protocols the
in-memory repos do (:class:`FileLedgerRepo`, :class:`DeadLetterRepo`), so the
summarisation loop and freshness pipeline work unchanged over a real database.
The connection is autocommit — each repo call is its own durable write, which is
what the resume/dedup guarantee (§Phase 5) relies on.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import psycopg

from backend.persistence.dead_letter import DeadLetterEntry
from backend.persistence.file_ledger import FileLedgerEntry, FileStatus
from backend.persistence.schema import DDL_STATEMENTS

if TYPE_CHECKING:
    from psycopg import Connection


def open_connection(dsn: str) -> Connection:
    """Open an autocommit connection. Caller owns closing it."""
    return psycopg.connect(dsn, autocommit=True)


def apply_schema(conn: Connection) -> None:
    """Create the system-of-record tables if absent (idempotent)."""
    with conn.cursor() as cur:
        for statement in DDL_STATEMENTS:
            cur.execute(statement)


class PgFileLedger:
    """``file_ledger`` over Postgres (FileLedgerRepo)."""

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def get(self, service: str, path: str) -> FileLedgerEntry | None:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT service, path, content_hash, prompt_version, tier, status, updated_at "
                "FROM file_ledger WHERE service = %s AND path = %s",
                (service, path),
            )
            row = cur.fetchone()
        return _to_file_entry(row) if row is not None else None

    def upsert(self, entry: FileLedgerEntry) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                "INSERT INTO file_ledger "
                "(service, path, content_hash, prompt_version, tier, status, updated_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (service, path) DO UPDATE SET "
                "content_hash = EXCLUDED.content_hash, prompt_version = EXCLUDED.prompt_version, "
                "tier = EXCLUDED.tier, status = EXCLUDED.status, updated_at = EXCLUDED.updated_at",
                (
                    entry.service, entry.path, entry.content_hash, entry.prompt_version,
                    entry.tier, entry.status.value, entry.updated_at,
                ),
            )

    def delete(self, service: str, path: str) -> bool:
        with self._conn.cursor() as cur:
            cur.execute(
                "DELETE FROM file_ledger WHERE service = %s AND path = %s", (service, path)
            )
            return cur.rowcount > 0

    def all(self) -> list[FileLedgerEntry]:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT service, path, content_hash, prompt_version, tier, status, updated_at "
                "FROM file_ledger ORDER BY service, path"
            )
            return [_to_file_entry(row) for row in cur.fetchall()]


class PgDeadLetter:
    """``dead_letter`` over Postgres (DeadLetterRepo)."""

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def add(self, entry: DeadLetterEntry) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                "INSERT INTO dead_letter (service, path, kind, detail, at) "
                "VALUES (%s, %s, %s, %s, %s)",
                (entry.service, entry.path, entry.kind, entry.detail, entry.at),
            )

    def all(self) -> list[DeadLetterEntry]:
        with self._conn.cursor() as cur:
            cur.execute("SELECT service, path, kind, detail, at FROM dead_letter ORDER BY id")
            return [
                DeadLetterEntry(service=r[0], path=r[1], kind=r[2], detail=r[3], at=r[4])
                for r in cur.fetchall()
            ]


def _to_file_entry(row: tuple[Any, ...]) -> FileLedgerEntry:
    return FileLedgerEntry(
        service=str(row[0]),
        path=str(row[1]),
        content_hash=str(row[2]),
        prompt_version=str(row[3]),
        tier=int(row[4]),
        status=FileStatus(str(row[5])),
        updated_at=row[6],
    )
