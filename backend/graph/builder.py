"""Assemble the full graph from an estate (§Phase 3).

Layers the extracted relationships on top of the Phase-1 deterministic skeleton:

- ``Service -CALLS-> Service`` resolved from call sites (a call to a symbol
  defined in another service is a cross-service dependency edge)
- ``Service -PUBLISHES/CONSUMES-> Topic`` from messaging call sites
- ``Service -EXPOSES-> Endpoint`` from OpenAPI specs

Resolution is deliberately conservative: only calls whose callee symbol is
*defined somewhere in the estate* become edges — library/framework calls are
dropped rather than invented as edges. The result is directly loadable into
Neo4j and directly answerable by the in-memory structural queries.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from backend.domain.manifest import Manifest
from backend.domain.models import (
    Chunk,
    Edge,
    EdgeKind,
    GraphDelta,
    Language,
    Node,
    NodeKind,
)
from backend.graph.analyze import FileFacts, analyze_file
from backend.graph.openapi import endpoints, looks_like_openapi, parse_openapi
from backend.graph.store import InMemoryGraphStore
from backend.ingestion.pipeline import FileResult, IngestionResult, ingest_file
from backend.ingestion.walker import walk_estate


@dataclass
class GraphBuildResult:
    """Full graph (skeleton + extracted edges) plus the chunks for OpenSearch."""

    nodes: list[Node]
    edges: list[Edge]
    chunks: list[Chunk]
    file_count: int = 0
    parse_error_count: int = 0

    def store(self) -> InMemoryGraphStore:
        return InMemoryGraphStore.from_graph(self.nodes, self.edges)

    def counts_by_node_kind(self) -> dict[str, int]:
        out: dict[str, int] = defaultdict(int)
        for node in self.nodes:
            out[node.kind.value] += 1
        return dict(out)

    def counts_by_edge_kind(self) -> dict[str, int]:
        out: dict[str, int] = defaultdict(int)
        for edge in self.edges:
            out[edge.kind.value] += 1
        return dict(out)


def build_graph(
    root: Path,
    manifest: Manifest,
    *,
    commit_sha: str,
    indexed_at: datetime | None = None,
) -> GraphBuildResult:
    indexed_at = indexed_at or datetime.now(UTC)
    ing = IngestionResult()

    # Register every manifested service up front (coverage completeness).
    for svc in manifest.services:
        ing.add_file(
            FileResult(
                service=svc.name,
                path=svc.path,
                language=svc.language or Language.UNKNOWN,
                delta=GraphDelta(nodes=[_service_node(svc.name, commit_sha, indexed_at)]),
            )
        )

    facts: list[FileFacts] = []
    openapi_docs: list[tuple[str, str, dict[str, object]]] = []

    for walked in walk_estate(root, manifest):
        source = walked.abs_path.read_bytes()
        result = ingest_file(
            source=source,
            service=walked.service,
            path=walked.path,
            commit_sha=commit_sha,
            indexed_at=indexed_at,
        )
        ing.add_file(result)
        facts.append(
            analyze_file(
                service=walked.service, path=walked.path, source=source, language=result.language
            )
        )
        if looks_like_openapi(walked.path, source):
            doc = parse_openapi(source)
            if doc is not None:
                openapi_docs.append((walked.service, walked.path, doc))

    extracted = _resolve(facts, openapi_docs, commit_sha=commit_sha, indexed_at=indexed_at)
    ing.add_file(
        FileResult(service="", path="", language=Language.UNKNOWN, delta=extracted)
    )

    return GraphBuildResult(
        nodes=ing.nodes,
        edges=ing.edges,
        chunks=ing.chunks,
        file_count=ing.file_count,
        parse_error_count=ing.parse_error_count,
    )


# --------------------------------------------------------------------------- #
# Resolution
# --------------------------------------------------------------------------- #


def _resolve(
    facts: list[FileFacts],
    openapi_docs: list[tuple[str, str, dict[str, object]]],
    *,
    commit_sha: str,
    indexed_at: datetime,
) -> GraphDelta:
    delta = GraphDelta()

    symbol_service: dict[str, set[str]] = defaultdict(set)
    for f in facts:
        for symbol in f.defined_symbols:
            symbol_service[symbol].add(f.service)

    _resolve_calls(delta, facts, symbol_service)
    _resolve_topics(delta, facts, commit_sha, indexed_at)
    _resolve_endpoints(delta, openapi_docs, commit_sha, indexed_at)
    return delta


def _resolve_calls(
    delta: GraphDelta, facts: list[FileFacts], symbol_service: dict[str, set[str]]
) -> None:
    edges: set[tuple[str, str]] = set()
    for f in facts:
        for _caller, callee in f.calls:
            for target_service in symbol_service.get(callee, ()):
                if target_service != f.service:
                    edges.add((f.service, target_service))
    for src, dst in edges:
        delta.edges.append(
            Edge(
                kind=EdgeKind.CALLS,
                src_kind=NodeKind.SERVICE,
                src_key=src,
                dst_kind=NodeKind.SERVICE,
                dst_key=dst,
            )
        )


def _resolve_topics(
    delta: GraphDelta, facts: list[FileFacts], commit_sha: str, indexed_at: datetime
) -> None:
    for f in facts:
        for topic in f.publishes:
            _add_topic_edge(delta, f.service, topic, EdgeKind.PUBLISHES, commit_sha, indexed_at)
        for topic in f.consumes:
            _add_topic_edge(delta, f.service, topic, EdgeKind.CONSUMES, commit_sha, indexed_at)


def _add_topic_edge(
    delta: GraphDelta,
    service: str,
    topic: str,
    kind: EdgeKind,
    commit_sha: str,
    indexed_at: datetime,
) -> None:
    delta.nodes.append(
        Node(
            kind=NodeKind.TOPIC,
            key=topic,
            properties={"name": topic},
            commit_sha=commit_sha,
            indexed_at=indexed_at,
        )
    )
    delta.edges.append(
        Edge(
            kind=kind,
            src_kind=NodeKind.SERVICE,
            src_key=service,
            dst_kind=NodeKind.TOPIC,
            dst_key=topic,
        )
    )


def _resolve_endpoints(
    delta: GraphDelta,
    openapi_docs: list[tuple[str, str, dict[str, object]]],
    commit_sha: str,
    indexed_at: datetime,
) -> None:
    for service, spec_path, doc in openapi_docs:
        for method, route in endpoints(doc):
            key = f"{method} {route}"
            delta.nodes.append(
                Node(
                    kind=NodeKind.ENDPOINT,
                    key=key,
                    properties={
                        "service": service,
                        "method": method,
                        "route": route,
                        "spec": spec_path,
                    },
                    commit_sha=commit_sha,
                    indexed_at=indexed_at,
                )
            )
            delta.edges.append(
                Edge(
                    kind=EdgeKind.EXPOSES,
                    src_kind=NodeKind.SERVICE,
                    src_key=service,
                    dst_kind=NodeKind.ENDPOINT,
                    dst_key=key,
                )
            )


def _service_node(service: str, commit_sha: str, indexed_at: datetime) -> Node:
    return Node(
        kind=NodeKind.SERVICE,
        key=service,
        properties={"name": service},
        commit_sha=commit_sha,
        indexed_at=indexed_at,
    )
