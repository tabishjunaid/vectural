"""Graph store abstraction + an in-memory implementation.

The in-memory store answers the structural fast-path queries (§5.5) with plain
adjacency BFS — no database, so the "what depends on X, three hops out" exit
criterion (§Phase 3) is demonstrable and tested offline. The production
:class:`Neo4jGraphStore` (optional ``neo4j`` extra) satisfies the same protocol
by running the validated Cypher builders in :mod:`backend.graph.queries`.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Protocol

from backend.domain.models import Edge, EdgeKind, GraphDelta, Node, NodeKind

NodeRef = tuple[NodeKind, str]


class GraphStore(Protocol):
    """Low-level graph access the structural queries are written against."""

    def has_node(self, kind: NodeKind, key: str) -> bool: ...

    def nodes(self, kind: NodeKind) -> list[Node]: ...

    def out_neighbors(self, kind: NodeKind, key: str, rel: EdgeKind) -> list[NodeRef]: ...

    def in_neighbors(self, kind: NodeKind, key: str, rel: EdgeKind) -> list[NodeRef]: ...


@dataclass
class InMemoryGraphStore:
    """Adjacency-indexed graph built from ingestion + extraction output."""

    _nodes: dict[NodeRef, Node] = field(default_factory=dict)
    _out: dict[tuple[NodeRef, EdgeKind], list[NodeRef]] = field(
        default_factory=lambda: defaultdict(list)
    )
    _in: dict[tuple[NodeRef, EdgeKind], list[NodeRef]] = field(
        default_factory=lambda: defaultdict(list)
    )
    _by_kind: dict[NodeKind, list[Node]] = field(default_factory=lambda: defaultdict(list))

    @classmethod
    def from_graph(cls, nodes: list[Node], edges: list[Edge]) -> InMemoryGraphStore:
        store = cls()
        for node in nodes:
            store.add_node(node)
        for edge in edges:
            store.add_edge(edge)
        return store

    @classmethod
    def from_delta(cls, delta: GraphDelta) -> InMemoryGraphStore:
        return cls.from_graph(delta.nodes, delta.edges)

    def add_node(self, node: Node) -> None:
        ref = (node.kind, node.key)
        if ref not in self._nodes:
            self._by_kind[node.kind].append(node)
        self._nodes[ref] = node

    def add_edge(self, edge: Edge) -> None:
        src: NodeRef = (edge.src_kind, edge.src_key)
        dst: NodeRef = (edge.dst_kind, edge.dst_key)
        out_list = self._out[(src, edge.kind)]
        if dst not in out_list:
            out_list.append(dst)
        in_list = self._in[(dst, edge.kind)]
        if src not in in_list:
            in_list.append(src)

    # -- GraphStore protocol ------------------------------------------------ #

    def has_node(self, kind: NodeKind, key: str) -> bool:
        return (kind, key) in self._nodes

    def nodes(self, kind: NodeKind) -> list[Node]:
        return list(self._by_kind.get(kind, []))

    def out_neighbors(self, kind: NodeKind, key: str, rel: EdgeKind) -> list[NodeRef]:
        return list(self._out.get(((kind, key), rel), []))

    def in_neighbors(self, kind: NodeKind, key: str, rel: EdgeKind) -> list[NodeRef]:
        return list(self._in.get(((kind, key), rel), []))

    @property
    def node_count(self) -> int:
        return len(self._nodes)
