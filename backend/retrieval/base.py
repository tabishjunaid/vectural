"""Retrieval interfaces and the ranked-hit shape returned to callers."""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel

from backend.domain.models import Chunk, ChunkKind, Language, Span
from backend.embedding.base import Vector


class SearchHit(BaseModel):
    """A retrieved chunk with its fused relevance score.

    Carries the display + citation fields (path, lines, service) so a hit is
    directly renderable and directly resolvable to a citation target — the UI's
    citation chip needs exactly ``chunk_id -> path + lines`` (§5.4).
    """

    chunk_id: str
    service: str
    path: str
    span: Span
    language: Language
    kind: ChunkKind
    symbol: str | None
    content: str
    commit_sha: str
    score: float
    lexical_score: float = 0.0
    dense_score: float = 0.0

    @classmethod
    def from_chunk(
        cls,
        chunk: Chunk,
        *,
        score: float,
        lexical_score: float = 0.0,
        dense_score: float = 0.0,
    ) -> SearchHit:
        return cls(
            chunk_id=chunk.chunk_id,
            service=chunk.service,
            path=chunk.path,
            span=chunk.span,
            language=chunk.language,
            kind=chunk.kind,
            symbol=chunk.symbol,
            content=chunk.content,
            commit_sha=chunk.commit_sha,
            score=score,
            lexical_score=lexical_score,
            dense_score=dense_score,
        )


class SearchBackend(Protocol):
    """A store that indexes chunks and answers scoped hybrid queries.

    Implementations: :class:`InMemorySearchBackend` (offline) and, behind the
    ``opensearch`` extra, an OpenSearch-backed one. Both must apply the
    ``services`` scope as a hard filter (§5.3 step 5), never as a soft boost —
    an in-scope query must not leak evidence from an out-of-scope service.
    """

    def index(self, chunks: list[Chunk]) -> None: ...

    def hybrid_search(
        self,
        query_text: str,
        query_vector: Vector | None,
        *,
        k: int,
        services: set[str] | None = None,
    ) -> list[SearchHit]: ...

    def indexed_files(self) -> set[tuple[str, str]]:
        """The ``(service, path)`` pairs already indexed — the idempotency key the
        indexing/reindex paths use to skip re-embedding unchanged files."""
        ...

    def delete_by_file(self, service: str, path: str) -> int: ...

    def delete_service(self, service: str) -> int:
        """Delete every chunk of a service (Ingestion UI "drop index")."""
        ...


class Reranker(Protocol):
    """Re-orders candidate hits for a query, returning at most ``top_n`` (§5.3
    step 6). The production impl is BGE-reranker-v2-m3 on the serving pod."""

    def rerank(self, query_text: str, hits: list[SearchHit], *, top_n: int) -> list[SearchHit]: ...
