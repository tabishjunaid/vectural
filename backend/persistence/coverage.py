"""Coverage manifest writer — per-service indexing progress (§5.4).

The durable indexing job records each service's reached tier + timestamp as it
completes, so ``coverage_manifest`` is a live progress surface (which services are
indexed, at what tier). The serving ``CoverageService`` derives the same view from
the graph today; reading these rows instead is a follow-up.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol


class CoverageWriter(Protocol):
    def mark_indexed(self, service: str, *, tier: int, status: str, at: datetime) -> None: ...


class InMemoryCoverageWriter:
    """Records the latest (tier, status, at) per service — for tests / the demo path."""

    def __init__(self) -> None:
        self.rows: dict[str, tuple[int, str, datetime]] = {}

    def mark_indexed(self, service: str, *, tier: int, status: str, at: datetime) -> None:
        self.rows[service] = (tier, status, at)
