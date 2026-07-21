"""Summary store for the higher tiers (§5.2).

Tier 2/3 summaries attach to Module/Service graph nodes; this store holds them
keyed by ``(tier, key)`` with the ``content_hash`` that produced them. Because a
higher-tier summary is keyed by the hashes of its *children*, it is regenerated
only when a child changes — which is exactly the "file → module → service
regenerate automatically" cascade (§5.9) done without re-spend.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

from pydantic import BaseModel, Field


class SummaryRecord(BaseModel):
    tier: int  # 2 = module, 3 = service
    kind: str  # "module" | "service"
    key: str
    text: str  # the headline (module responsibility / service description)
    data: dict[str, Any] = Field(default_factory=dict)  # full structured summary
    content_hash: str  # hash of child hashes + prompt version
    prompt_version: str
    updated_at: datetime


class SummaryStore(Protocol):
    def get(self, tier: int, key: str) -> SummaryRecord | None: ...
    def upsert(self, record: SummaryRecord) -> None: ...
    def all(self, tier: int) -> list[SummaryRecord]: ...


class InMemorySummaryStore:
    def __init__(self) -> None:
        self._rows: dict[tuple[int, str], SummaryRecord] = {}

    def get(self, tier: int, key: str) -> SummaryRecord | None:
        return self._rows.get((tier, key))

    def upsert(self, record: SummaryRecord) -> None:
        self._rows[(record.tier, record.key)] = record

    def all(self, tier: int) -> list[SummaryRecord]:
        return sorted(
            (r for r in self._rows.values() if r.tier == tier), key=lambda r: r.key
        )
