"""Ingestion orchestration (§5.1): file → chunks + graph deltas → estate result.

This is the seam where deterministic outputs are assembled but **not** yet
persisted. It returns in-memory results (chunks for the OpenSearch bulk load,
graph deltas for Neo4j) so the store clients — added in later phases — own
persistence and this module stays testable with no infrastructure.

Graph deltas emitted here are the deterministic skeleton only:
``Service -CONTAINS-> Module -CONTAINS-> File -DEFINES-> Function``. Summary
content, embeddings, and cross-service CALLS/PUBLISHES edges are added by later
phases (§5.2, §Phase 3) — never here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from backend.domain.manifest import Manifest
from backend.domain.models import (
    Chunk,
    ChunkKind,
    Edge,
    EdgeKind,
    GraphDelta,
    Language,
    Node,
    NodeKind,
)
from backend.ingestion.chunker import chunk_source
from backend.ingestion.classify import classify_path
from backend.ingestion.parser import UnsupportedLanguageError, has_errors, parse
from backend.ingestion.walker import WalkedFile, walk_estate

_FUNCTIONISH = frozenset({ChunkKind.FUNCTION, ChunkKind.METHOD})


@dataclass
class FileResult:
    """The deterministic output of ingesting a single file."""

    service: str
    path: str
    language: Language
    chunks: list[Chunk] = field(default_factory=list)
    delta: GraphDelta = field(default_factory=GraphDelta)
    parse_error: bool = False


@dataclass
class IngestionResult:
    """Aggregated, de-duplicated result of ingesting a set of files."""

    chunks: list[Chunk] = field(default_factory=list)
    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    file_count: int = 0
    parse_error_count: int = 0

    # Internal dedupe indexes (nodes and edges recur across files).
    _node_keys: set[tuple[str, str]] = field(default_factory=set, repr=False)
    _edge_keys: set[tuple[str, str, str, str, str]] = field(default_factory=set, repr=False)

    def add_file(self, result: FileResult) -> None:
        self.file_count += 1
        if result.parse_error:
            self.parse_error_count += 1
        self.chunks.extend(result.chunks)
        for node in result.delta.nodes:
            key = (node.kind.value, node.key)
            if key not in self._node_keys:
                self._node_keys.add(key)
                self.nodes.append(node)
        for edge in result.delta.edges:
            ekey = (
                edge.kind.value,
                edge.src_kind.value,
                edge.src_key,
                edge.dst_kind.value,
                edge.dst_key,
            )
            if ekey not in self._edge_keys:
                self._edge_keys.add(ekey)
                self.edges.append(edge)

    def counts_by_kind(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for node in self.nodes:
            out[node.kind.value] = out.get(node.kind.value, 0) + 1
        return out


def ingest_file(
    *,
    source: bytes,
    service: str,
    path: str,
    commit_sha: str,
    language: Language | None = None,
    indexed_at: datetime | None = None,
) -> FileResult:
    """Ingest one file's bytes into chunks and a graph delta.

    ``language`` is auto-classified from ``path`` when not supplied. A parse
    failure never raises: the file still yields whole-file/module chunks and a
    File node, with ``parse_error`` set for dead-letter accounting (§5.8).
    """
    language = language or classify_path(path)
    indexed_at = indexed_at or datetime.now(UTC)

    parse_error = _detect_parse_error(source, language)
    chunks = chunk_source(
        source, language=language, service=service, path=path, commit_sha=commit_sha
    )
    delta = _graph_delta(
        service=service,
        path=path,
        language=language,
        chunks=chunks,
        commit_sha=commit_sha,
        indexed_at=indexed_at,
    )
    return FileResult(
        service=service,
        path=path,
        language=language,
        chunks=chunks,
        delta=delta,
        parse_error=parse_error,
    )


def ingest_tree(
    root: Path,
    manifest: Manifest,
    *,
    commit_sha: str,
    indexed_at: datetime | None = None,
) -> IngestionResult:
    """Walk and ingest an entire estate rooted at ``root`` per ``manifest``.

    Registers Service nodes for every manifested service up front so a service
    with no indexable files still appears in the graph (coverage must be able to
    say "indexed, zero files" rather than omit it).
    """
    indexed_at = indexed_at or datetime.now(UTC)
    result = IngestionResult()
    for svc in manifest.services:
        result.add_file(
            FileResult(
                service=svc.name,
                path=svc.path,
                language=svc.language or Language.UNKNOWN,
                delta=GraphDelta(nodes=[_service_node(svc.name, commit_sha, indexed_at)]),
            )
        )
    for walked in walk_estate(root, manifest):
        result.add_file(_ingest_walked(walked, commit_sha, indexed_at))
    return result


def _ingest_walked(walked: WalkedFile, commit_sha: str, indexed_at: datetime) -> FileResult:
    source = walked.abs_path.read_bytes()
    return ingest_file(
        source=source,
        service=walked.service,
        path=walked.path,
        commit_sha=commit_sha,
        indexed_at=indexed_at,
    )


def _detect_parse_error(source: bytes, language: Language) -> bool:
    try:
        tree = parse(source, language)
    except UnsupportedLanguageError:
        return False  # unsupported ≠ malformed; it is intentionally lexical-only
    return has_errors(tree)


# --------------------------------------------------------------------------- #
# Graph delta construction
# --------------------------------------------------------------------------- #


def _graph_delta(
    *,
    service: str,
    path: str,
    language: Language,
    chunks: list[Chunk],
    commit_sha: str,
    indexed_at: datetime,
) -> GraphDelta:
    delta = GraphDelta()
    module_key = _module_key(service, path)

    delta.nodes.append(_service_node(service, commit_sha, indexed_at))
    delta.nodes.append(
        Node(
            kind=NodeKind.MODULE,
            key=module_key,
            properties={"service": service},
            commit_sha=commit_sha,
            indexed_at=indexed_at,
        )
    )
    delta.nodes.append(
        Node(
            kind=NodeKind.FILE,
            key=path,
            properties={"service": service, "module": module_key, "language": language.value},
            commit_sha=commit_sha,
            indexed_at=indexed_at,
        )
    )
    delta.edges.append(
        Edge(
            kind=EdgeKind.CONTAINS,
            src_kind=NodeKind.SERVICE,
            src_key=service,
            dst_kind=NodeKind.MODULE,
            dst_key=module_key,
        )
    )
    delta.edges.append(
        Edge(
            kind=EdgeKind.CONTAINS,
            src_kind=NodeKind.MODULE,
            src_key=module_key,
            dst_kind=NodeKind.FILE,
            dst_key=path,
        )
    )

    for chunk in chunks:
        if chunk.kind not in _FUNCTIONISH or not chunk.symbol:
            continue
        fn_key = f"{path}#{chunk.symbol}@{chunk.span.start}"
        delta.nodes.append(
            Node(
                kind=NodeKind.FUNCTION,
                key=fn_key,
                properties={
                    "service": service,
                    "file": path,
                    "symbol": chunk.symbol,
                    "lines": str(chunk.span),
                    "chunk_id": chunk.chunk_id,
                },
                commit_sha=commit_sha,
                indexed_at=indexed_at,
            )
        )
        delta.edges.append(
            Edge(
                kind=EdgeKind.DEFINES,
                src_kind=NodeKind.FILE,
                src_key=path,
                dst_kind=NodeKind.FUNCTION,
                dst_key=fn_key,
            )
        )
    return delta


def _service_node(service: str, commit_sha: str, indexed_at: datetime) -> Node:
    return Node(
        kind=NodeKind.SERVICE,
        key=service,
        properties={"name": service},
        commit_sha=commit_sha,
        indexed_at=indexed_at,
    )


def _module_key(service: str, path: str) -> str:
    """Folder-level module identity: the file's parent directory.

    A file directly under the service root belongs to the service's root module
    (keyed by the service path itself), so every file has exactly one module.
    """
    parent = PurePosixPath(path).parent
    return str(parent) if str(parent) != "." else path
