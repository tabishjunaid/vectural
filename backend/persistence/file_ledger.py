"""``file_ledger`` — per-file indexing status (§3.3).

Written by the summarisation workflow, read by the Temporal resume logic and the
coverage manifest. Its purpose is the Phase 5 exit criterion: **a killed workflow
resumes without duplicate spend.** That works because a file's ledger row records
the ``content_hash`` and ``prompt_version`` it was last summarised at — re-running
skips any file whose content and prompt are unchanged.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel


class FileStatus(StrEnum):
    PENDING = "pending"
    SUMMARISED = "summarised"
    DEAD_LETTERED = "dead_lettered"


class FileLedgerEntry(BaseModel):
    service: str
    path: str
    content_hash: str
    prompt_version: str
    tier: int  # highest summary tier completed for this file (1 = file summary)
    status: FileStatus
    updated_at: datetime

    def matches(self, content_hash: str, prompt_version: str) -> bool:
        """Whether a re-run can skip this file: same content and same prompt."""
        return (
            self.status is FileStatus.SUMMARISED
            and self.content_hash == content_hash
            and self.prompt_version == prompt_version
        )


class FileLedgerRepo(Protocol):
    def get(self, service: str, path: str) -> FileLedgerEntry | None: ...
    def upsert(self, entry: FileLedgerEntry) -> None: ...
    def delete(self, service: str, path: str) -> bool: ...
    def all(self) -> list[FileLedgerEntry]: ...


class InMemoryFileLedger:
    def __init__(self) -> None:
        self._rows: dict[tuple[str, str], FileLedgerEntry] = {}

    def get(self, service: str, path: str) -> FileLedgerEntry | None:
        return self._rows.get((service, path))

    def upsert(self, entry: FileLedgerEntry) -> None:
        self._rows[(entry.service, entry.path)] = entry

    def delete(self, service: str, path: str) -> bool:
        return self._rows.pop((service, path), None) is not None

    def all(self) -> list[FileLedgerEntry]:
        return list(self._rows.values())
