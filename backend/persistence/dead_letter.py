"""``dead_letter`` — content failures for weekly review (§3.3, §5.8).

A content failure (parse error, malformed model output, oversized file) is
dead-lettered here and the batch continues; blocking a whole batch on one bad
file is the anti-pattern (§7.2). Reviewed weekly.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from pydantic import BaseModel


class DeadLetterEntry(BaseModel):
    service: str
    path: str
    kind: str  # e.g. "parse_error", "malformed_json", "oversized"
    detail: str | None
    at: datetime


class DeadLetterRepo(Protocol):
    def add(self, entry: DeadLetterEntry) -> None: ...
    def all(self) -> list[DeadLetterEntry]: ...


class InMemoryDeadLetter:
    def __init__(self) -> None:
        self._rows: list[DeadLetterEntry] = []

    def add(self, entry: DeadLetterEntry) -> None:
        self._rows.append(entry)

    def all(self) -> list[DeadLetterEntry]:
        return list(self._rows)
