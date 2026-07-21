"""Structural fast-path queries (§5.5) — deterministic, no LLM, no gateway.

These are the questions the graph answers directly: dependency reachability,
callers, where a symbol is defined, topic producers/consumers. They power both
the "instant answer" UI path (design-doc §4.2 cache/structural branch) and the
Phase 3 exit criterion.

Two surfaces:
- :class:`StructuralQueries` runs them over any :class:`GraphStore` (the
  in-memory store for offline use / tests).
- the ``cypher_*`` builders emit the equivalent **validated** Cypher for the
  Neo4j backend — every emitted query passes :func:`validate_cypher` before it
  is returned, so the fast path cannot itself produce an unsafe query.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from backend.domain.models import EdgeKind, NodeKind
from backend.graph.cypher_validator import DEFAULT_HOP_CAP, ensure_valid
from backend.graph.store import GraphStore, NodeRef


@dataclass(frozen=True)
class Definition:
    service: str
    path: str
    symbol: str
    lines: str


@dataclass
class StructuralQueries:
    store: GraphStore

    def service_dependencies(self, service: str, *, max_hops: int = 3) -> dict[int, list[str]]:
        """Services ``service`` reaches via outgoing CALLS, grouped by hop distance.

        This is the exit-criterion query — "what depends on service X, three hops
        out" is its reverse (:meth:`service_dependents`); this is "what X calls".
        """
        return self._bfs_services(service, EdgeKind.CALLS, outgoing=True, max_hops=max_hops)

    def service_dependents(self, service: str, *, max_hops: int = 3) -> dict[int, list[str]]:
        """Services that reach ``service`` via CALLS — i.e. what depends on it."""
        return self._bfs_services(service, EdgeKind.CALLS, outgoing=False, max_hops=max_hops)

    def direct_callers(self, service: str) -> list[str]:
        return sorted(
            key for _, key in self.store.in_neighbors(NodeKind.SERVICE, service, EdgeKind.CALLS)
        )

    def direct_callees(self, service: str) -> list[str]:
        return sorted(
            key for _, key in self.store.out_neighbors(NodeKind.SERVICE, service, EdgeKind.CALLS)
        )

    def definitions_of(self, symbol: str) -> list[Definition]:
        """Every Function node whose declared symbol matches ``symbol``."""
        out: list[Definition] = []
        for node in self.store.nodes(NodeKind.FUNCTION):
            if node.properties.get("symbol") == symbol:
                out.append(
                    Definition(
                        service=str(node.properties.get("service", "")),
                        path=str(node.properties.get("file", "")),
                        symbol=symbol,
                        lines=str(node.properties.get("lines", "")),
                    )
                )
        return sorted(out, key=lambda d: (d.service, d.path, d.lines))

    def topic_publishers(self, topic: str) -> list[str]:
        return sorted(
            key for _, key in self.store.in_neighbors(NodeKind.TOPIC, topic, EdgeKind.PUBLISHES)
        )

    def topic_consumers(self, topic: str) -> list[str]:
        return sorted(
            key for _, key in self.store.in_neighbors(NodeKind.TOPIC, topic, EdgeKind.CONSUMES)
        )

    # -- internals ---------------------------------------------------------- #

    def _bfs_services(
        self, start: str, rel: EdgeKind, *, outgoing: bool, max_hops: int
    ) -> dict[int, list[str]]:
        if not self.store.has_node(NodeKind.SERVICE, start):
            return {}
        step = self.store.out_neighbors if outgoing else self.store.in_neighbors
        seen: set[str] = {start}
        by_hop: dict[int, list[str]] = {}
        frontier: deque[tuple[str, int]] = deque([(start, 0)])
        while frontier:
            key, hop = frontier.popleft()
            if hop >= max_hops:
                continue
            neighbors: list[NodeRef] = step(NodeKind.SERVICE, key, rel)
            for _, nxt in neighbors:
                if nxt in seen:
                    continue
                seen.add(nxt)
                by_hop.setdefault(hop + 1, []).append(nxt)
                frontier.append((nxt, hop + 1))
        return {hop: sorted(keys) for hop, keys in sorted(by_hop.items())}


# --------------------------------------------------------------------------- #
# Validated Cypher builders for the Neo4j backend
# --------------------------------------------------------------------------- #


def cypher_service_dependents(hops: int = 3, hop_cap: int = DEFAULT_HOP_CAP) -> str:
    """Validated Cypher for "what depends on $service within N hops"."""
    hops = min(hops, hop_cap)
    query = (
        f"MATCH (dependent:Service)-[:CALLS*1..{hops}]->(target:Service {{key: $service}}) "
        "RETURN DISTINCT dependent.key AS service"
    )
    return ensure_valid(query, hop_cap=hop_cap)


def cypher_service_dependencies(hops: int = 3, hop_cap: int = DEFAULT_HOP_CAP) -> str:
    hops = min(hops, hop_cap)
    query = (
        f"MATCH (source:Service {{key: $service}})-[:CALLS*1..{hops}]->(dep:Service) "
        "RETURN DISTINCT dep.key AS service"
    )
    return ensure_valid(query, hop_cap=hop_cap)


def cypher_definitions_of() -> str:
    query = (
        "MATCH (f:File)-[:DEFINES]->(fn:Function {symbol: $symbol}) "
        "RETURN fn.service AS service, fn.file AS path, fn.lines AS lines"
    )
    return ensure_valid(query)
