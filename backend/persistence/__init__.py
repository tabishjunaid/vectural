"""PostgreSQL system of record (implementation-plan §4.1, §3.3).

The **only non-rebuildable store**: every other store (Neo4j, OpenSearch) can be
dropped and rebuilt from git + these tables. Design invariant — if a new piece of
state cannot be reconstructed that way, it belongs here.

Each table has a repository protocol with an in-memory implementation (used by
tests and the offline demo) and SQL DDL for the real database. The psycopg-backed
repositories are the infra seam, added behind the ``postgres`` extra.
"""

from backend.persistence.dead_letter import DeadLetterEntry, DeadLetterRepo, InMemoryDeadLetter
from backend.persistence.file_ledger import (
    FileLedgerEntry,
    FileLedgerRepo,
    InMemoryFileLedger,
)
from backend.persistence.schema import DDL_STATEMENTS, schema_sql

__all__ = [
    "DDL_STATEMENTS",
    "DeadLetterEntry",
    "DeadLetterRepo",
    "FileLedgerEntry",
    "FileLedgerRepo",
    "InMemoryDeadLetter",
    "InMemoryFileLedger",
    "schema_sql",
]
