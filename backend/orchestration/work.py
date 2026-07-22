"""Partition a built graph into per-service indexing work (§5.7).

The durable workflow processes one service at a time, so the estate's chunks,
nodes, and edges are grouped by service up front. Two things can't belong to a
single service and are handled in the finalize step (after every service's nodes
exist, so their edge MATCHes succeed):

  * **cross-service edges** — e.g. a CALLS edge from service A into service B;
  * **service-less nodes** — global nodes (e.g. shared topics) with no owner.

Chunks group by ``chunk.service``; a "file" aggregates all chunks at one
``(service, path)`` into one tier-1 summary input (matching the boot summariser).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from backend.domain.models import Chunk, Edge, Node, NodeKind
from backend.graph.builder import GraphBuildResult
from backend.summarise.driver import FileToSummarise


@dataclass
class ServiceWork:
    """Everything needed to index one service atomically."""

    service: str
    files: list[FileToSummarise] = field(default_factory=list)  # tier-1 summary inputs
    chunks: list[Chunk] = field(default_factory=list)  # → OpenSearch
    nodes: list[Node] = field(default_factory=list)  # → Neo4j (this service's own)
    edges: list[Edge] = field(default_factory=list)  # intra-service edges only


@dataclass
class EstateWork:
    """The whole estate, partitioned for the durable indexing run."""

    by_service: dict[str, ServiceWork]
    shared_nodes: list[Node] = field(default_factory=list)  # service-less globals
    cross_service_edges: list[Edge] = field(default_factory=list)

    @property
    def services(self) -> list[str]:
        return sorted(self.by_service)


def _node_service(node: Node) -> str | None:
    """The owning service of a node, or None for a global/shared node."""
    if node.kind is NodeKind.SERVICE:
        return node.key
    svc = node.properties.get("service")
    return svc if isinstance(svc, str) and svc else None


def build_work_by_service(graph: GraphBuildResult) -> EstateWork:
    by_service: dict[str, ServiceWork] = {}

    def work(service: str) -> ServiceWork:
        return by_service.setdefault(service, ServiceWork(service=service))

    # Nodes → owning service (or shared), plus a lookup for classifying edges.
    shared_nodes: list[Node] = []
    node_service: dict[tuple[str, str], str | None] = {}
    for node in graph.nodes:
        svc = _node_service(node)
        node_service[(node.kind.value, node.key)] = svc
        if svc is None:
            shared_nodes.append(node)
        else:
            work(svc).nodes.append(node)

    # Edges → intra-service (both endpoints in one service) vs cross-service.
    cross_service_edges: list[Edge] = []
    for edge in graph.edges:
        src_svc = node_service.get((edge.src_kind.value, edge.src_key))
        dst_svc = node_service.get((edge.dst_kind.value, edge.dst_key))
        if src_svc is not None and src_svc == dst_svc:
            work(src_svc).edges.append(edge)
        else:
            cross_service_edges.append(edge)

    # Chunks → OpenSearch, grouped by service; aggregate per (service, path) into
    # one tier-1 summary input.
    content_by_file: dict[tuple[str, str], list[str]] = {}
    for chunk in graph.chunks:
        work(chunk.service).chunks.append(chunk)
        content_by_file.setdefault((chunk.service, chunk.path), []).append(chunk.content)
    for (service, path), parts in sorted(content_by_file.items()):
        work(service).files.append(
            FileToSummarise(service=service, path=path, content="\n".join(parts))
        )

    return EstateWork(
        by_service=by_service,
        shared_nodes=shared_nodes,
        cross_service_edges=cross_service_edges,
    )
