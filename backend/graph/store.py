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

    def delete_node(self, kind: NodeKind, key: str) -> bool: ...

    def delete_file(self, path: str) -> int: ...


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

    def upsert_nodes(self, nodes: list[Node]) -> None:
        """Batch upsert — the in-memory twin of Neo4jGraphStore.upsert_nodes so the
        indexing activity writes to either store through the same GraphWriter seam."""
        for node in nodes:
            self.add_node(node)

    def upsert_edges(self, edges: list[Edge]) -> None:
        for edge in edges:
            self.add_edge(edge)

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

    @property
    def edge_count(self) -> int:
        return sum(len(dsts) for dsts in self._out.values())

    def delete_node(self, kind: NodeKind, key: str) -> bool:
        """Delete a node and **all referencing edges** in both directions (§5.9).

        Deleting a node must never leave a dangling edge — the cascade is the
        whole point (design-doc §4.4 "nodes + all referencing edges")."""
        ref: NodeRef = (kind, key)
        if ref not in self._nodes:
            return False
        node = self._nodes.pop(ref)
        self._by_kind[kind] = [n for n in self._by_kind.get(kind, []) if n.key != key]

        # Outgoing edges: drop ref from every destination's in-adjacency.
        for (src, rel), dsts in list(self._out.items()):
            if src == ref:
                for dst in dsts:
                    self._prune(self._in, (dst, rel), ref)
                del self._out[(src, rel)]
        # Incoming edges: drop ref from every source's out-adjacency.
        for (dst, rel), srcs in list(self._in.items()):
            if dst == ref:
                for src in srcs:
                    self._prune(self._out, (src, rel), ref)
                del self._in[(dst, rel)]
        del node
        return True

    def delete_file(self, path: str) -> int:
        """Cascade-delete a File node: its Function nodes, then the File itself.

        Returns the number of graph nodes removed. The owning Module/Service
        nodes are left in place — they may still contain other files."""
        removed = 0
        for fn in [n for n in self.nodes(NodeKind.FUNCTION) if n.key.startswith(f"{path}#")]:
            if self.delete_node(NodeKind.FUNCTION, fn.key):
                removed += 1
        if self.delete_node(NodeKind.FILE, path):
            removed += 1
        return removed

    def file_keys(self) -> set[str]:
        """Keys of all File nodes — for reconciliation against the git tree."""
        return {n.key for n in self.nodes(NodeKind.FILE)}

    @staticmethod
    def _prune(
        index: dict[tuple[NodeRef, EdgeKind], list[NodeRef]],
        key: tuple[NodeRef, EdgeKind],
        ref: NodeRef,
    ) -> None:
        if key in index:
            index[key] = [r for r in index[key] if r != ref]
            if not index[key]:
                del index[key]
