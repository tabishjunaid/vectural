"""Partitioning a built graph into per-service indexing work (§5.7)."""

from __future__ import annotations

from backend.domain.models import NodeKind
from backend.graph.builder import GraphBuildResult
from backend.orchestration.work import build_work_by_service


def test_work_partitions_every_chunk_node_and_edge(graph_build: GraphBuildResult) -> None:
    work = build_work_by_service(graph_build)

    # Every manifested service is represented.
    service_nodes = {n.key for n in graph_build.nodes if n.kind is NodeKind.SERVICE}
    assert service_nodes <= set(work.by_service)

    # Chunks partition exactly (no loss, no duplication).
    assert sum(len(w.chunks) for w in work.by_service.values()) == len(graph_build.chunks)

    # Nodes are fully accounted for: per-service owners + service-less shared.
    partitioned_nodes = sum(len(w.nodes) for w in work.by_service.values()) + len(work.shared_nodes)
    assert partitioned_nodes == len(graph_build.nodes)

    # Edges are fully accounted for: intra-service + cross-service.
    partitioned_edges = (
        sum(len(w.edges) for w in work.by_service.values()) + len(work.cross_service_edges)
    )
    assert partitioned_edges == len(graph_build.edges)


def test_files_aggregate_chunks_per_path(graph_build: GraphBuildResult) -> None:
    work = build_work_by_service(graph_build)
    for w in work.by_service.values():
        # One tier-1 file per distinct (service, path); never per chunk.
        paths = [f.path for f in w.files]
        assert len(paths) == len(set(paths))
        chunk_paths = {c.path for c in w.chunks}
        assert set(paths) == chunk_paths


def test_intra_service_edges_stay_within_one_service(graph_build: GraphBuildResult) -> None:
    work = build_work_by_service(graph_build)
    node_owner = {
        (n.kind.value, n.key): svc
        for svc, w in work.by_service.items()
        for n in w.nodes
    }
    for svc, w in work.by_service.items():
        for edge in w.edges:
            # Both endpoints belong to this same service (that's what makes it intra).
            assert node_owner.get((edge.src_kind.value, edge.src_key)) == svc
            assert node_owner.get((edge.dst_kind.value, edge.dst_key)) == svc
