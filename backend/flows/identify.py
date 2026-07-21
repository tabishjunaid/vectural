"""Identify recurring cross-service flows from the graph (§Phase 7).

Deterministic, no LLM. Two kinds of candidate flow:

- **call chains** — a linear path through the ``CALLS`` graph starting at an entry
  service (one nothing else calls), e.g. ``gateway → payments → ledger``
- **event flows** — a publisher and a consumer linked by a topic, e.g. ``payments
  → notifications`` via ``payments.events``

Each candidate carries a **structural signature** (its services + trigger); that
signature is what the freshness pipeline compares to decide a narrative is stale
(§4.4), and what keys tier-4 generation so an unchanged flow is never re-spent.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from backend.domain.models import EdgeKind, NodeKind
from backend.graph.queries import StructuralQueries
from backend.graph.store import GraphStore


@dataclass(frozen=True)
class FlowCandidate:
    id: str
    title: str
    services: tuple[str, ...]  # ordered path
    trigger: str
    signature: str

    @property
    def is_cross_service(self) -> bool:
        return len(set(self.services)) >= 2


def identify_flows(
    store: GraphStore, structural: StructuralQueries, *, max_len: int = 5
) -> list[FlowCandidate]:
    """Return de-duplicated cross-service flow candidates, sorted for stability."""
    candidates: dict[str, FlowCandidate] = {}

    for path in _call_chains(store, structural, max_len=max_len):
        cand = _candidate(path, trigger="call graph")
        if cand.is_cross_service:
            candidates[cand.id] = cand

    for publisher, topic, consumer in _event_links(store, structural):
        cand = _candidate((publisher, consumer), trigger=f"topic: {topic}")
        if cand.is_cross_service:
            candidates[cand.id] = cand

    return sorted(candidates.values(), key=lambda c: c.id)


def _call_chains(
    store: GraphStore, structural: StructuralQueries, *, max_len: int
) -> list[tuple[str, ...]]:
    services = [n.key for n in store.nodes(NodeKind.SERVICE)]
    entries = [s for s in services if not structural.direct_callers(s)]
    chains: list[tuple[str, ...]] = []
    for entry in entries:
        chains.extend(_walk_calls(entry, structural, max_len))
    return chains


def _walk_calls(
    start: str, structural: StructuralQueries, max_len: int
) -> list[tuple[str, ...]]:
    """Enumerate maximal simple CALLS paths from ``start`` (cycle-safe)."""
    results: list[tuple[str, ...]] = []

    def dfs(path: tuple[str, ...]) -> None:
        if len(path) >= max_len:
            results.append(path)
            return
        callees = [c for c in structural.direct_callees(path[-1]) if c not in path]
        if not callees:
            if len(path) >= 2:
                results.append(path)
            return
        for callee in callees:
            dfs((*path, callee))

    dfs((start,))
    return results


def _event_links(
    store: GraphStore, structural: StructuralQueries
) -> list[tuple[str, str, str]]:
    links: list[tuple[str, str, str]] = []
    for topic in store.nodes(NodeKind.TOPIC):
        publishers = structural.topic_publishers(topic.key)
        consumers = structural.topic_consumers(topic.key)
        for pub in publishers:
            for con in consumers:
                if pub != con:
                    links.append((pub, topic.key, con))
    return links


def _candidate(services: tuple[str, ...], *, trigger: str) -> FlowCandidate:
    signature = _signature(services, trigger)
    slug = "-".join(services) + "-" + signature[:6]
    title = " → ".join(services)
    return FlowCandidate(
        id=slug, title=title, services=services, trigger=trigger, signature=signature
    )


def _signature(services: tuple[str, ...], trigger: str) -> str:
    raw = "|".join(services) + "::" + trigger + "::" + EdgeKind.CALLS.value
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
