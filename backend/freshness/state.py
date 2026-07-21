"""Staleness state (§4.4: "serving continues on the previous version with a
visible staleness indicator").

A service is marked stale from the moment a reindex touches it until its
summaries are regenerated / its flows re-approved. The answer path consults this
to flag an answer as stale rather than withholding it — R3 requires the
staleness be *visible*, not that serving stop.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field


@dataclass
class FreshnessState:
    _stale: set[str] = field(default_factory=set)

    def mark_stale(self, services: Iterable[str]) -> None:
        self._stale |= set(services)

    def clear(self, services: Iterable[str]) -> None:
        self._stale -= set(services)

    def clear_all(self) -> None:
        self._stale.clear()

    def is_stale(self, service: str) -> bool:
        return service in self._stale

    def any_stale(self, services: Iterable[str]) -> bool:
        return bool(self._stale & set(services))

    @property
    def stale_services(self) -> set[str]:
        return set(self._stale)
