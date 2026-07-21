"""Flow narrative store (§Phase 7). Persistence only — transitions live in the
service so the store stays a dumb, swappable repository (Postgres in production)."""

from __future__ import annotations

from typing import Protocol

from backend.flows.models import FlowNarrative


class FlowStore(Protocol):
    def get(self, flow_id: str) -> FlowNarrative | None: ...
    def upsert(self, narrative: FlowNarrative) -> None: ...
    def all(self) -> list[FlowNarrative]: ...


class InMemoryFlowStore:
    def __init__(self) -> None:
        self._rows: dict[str, FlowNarrative] = {}

    def get(self, flow_id: str) -> FlowNarrative | None:
        return self._rows.get(flow_id)

    def upsert(self, narrative: FlowNarrative) -> None:
        self._rows[narrative.id] = narrative

    def all(self) -> list[FlowNarrative]:
        return sorted(self._rows.values(), key=lambda n: n.id)

    def queue(self) -> list[FlowNarrative]:
        """Narratives awaiting an architect: pending or needs_review."""
        return [n for n in self.all() if n.in_review_queue]

    def authoritative_for(self, services: set[str]) -> list[FlowNarrative]:
        """Approved narratives whose services overlap the given set."""
        return [
            n
            for n in self.all()
            if n.is_authoritative and services.intersection(n.services)
        ]
