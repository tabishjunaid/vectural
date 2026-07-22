"""OpenSearch-backed search (optional ``opensearch`` extra) — the real §5.3 store.

Implements the :class:`SearchBackend` protocol against a real cluster: creates
the index from the ``code_analyzer`` template (§4.3), bulk-indexes chunks with
their BGE-M3 vectors, and answers hybrid queries by running the BM25 and k-NN
halves and fusing them with **RRF client-side** (portable — no search pipeline
required). Deletes are by service+path for the §5.9 cascade.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from backend.domain.models import Chunk, ChunkKind, Language, Span
from backend.embedding.base import Embedder, Vector
from backend.index.bulk import chunk_to_doc
from backend.index.opensearch_template import index_mappings, index_settings
from backend.retrieval.base import SearchHit
from backend.retrieval.inmemory import _rrf_fuse  # reuse the fusion used offline
from backend.retrieval.opensearch_query import knn_query, lexical_query

if TYPE_CHECKING:
    from opensearchpy import OpenSearch

_log = logging.getLogger(__name__)

_SOURCE_FIELDS = [
    "chunk_id", "service", "path", "lines", "language", "kind", "symbol", "content", "commit_sha",
]


class OpenSearchBackend:
    def __init__(self, client: OpenSearch, index: str, embedder: Embedder) -> None:
        self._client = client
        self._index = index
        self._embedder = embedder

    @classmethod
    def connect(cls, url: str, index: str, embedder: Embedder) -> OpenSearchBackend:
        from opensearchpy import OpenSearch

        client = OpenSearch(hosts=[url], http_compress=True)
        backend = cls(client, index, embedder)
        backend.ensure_index()
        return backend

    def ensure_index(self) -> None:
        if not self._client.indices.exists(index=self._index):
            self._client.indices.create(
                index=self._index,
                body={"settings": index_settings(), "mappings": index_mappings()},
            )

    def index(self, chunks: list[Chunk]) -> None:
        from opensearchpy.helpers import bulk

        docs = [chunk_to_doc(chunk) for chunk in chunks]
        # Batch-embed in one call — a real model (BGE-M3 on CPU) is far faster
        # embedding a batch than one text at a time.
        needs = [(i, _searchable(c)) for i, (c, d) in enumerate(zip(chunks, docs, strict=True))
                 if "embedding" not in d]
        if needs:
            vectors = self._embedder.embed([text for _, text in needs])
            for (i, _), vec in zip(needs, vectors, strict=True):
                docs[i]["embedding"] = vec

        actions = []
        skipped_zero = 0
        for chunk, doc in zip(chunks, docs, strict=True):
            # A zero-norm vector (e.g. a trivial/empty file) cannot be stored under a
            # cosinesimil knn_vector — OpenSearch rejects it and would fail the whole
            # bulk. Such a chunk carries no dense signal, so drop it rather than let one
            # degenerate file abort the estate. (Real BGE-M3 won't emit exact zeros.)
            if not any(doc["embedding"]):
                skipped_zero += 1
                continue
            actions.append({"_index": self._index, "_id": chunk.chunk_id, "_source": doc})
        if skipped_zero:
            _log.warning("skipped %d chunk(s) with zero-norm embeddings (no dense signal)",
                         skipped_zero)
        if actions:
            bulk(self._client, actions, refresh=True)

    def hybrid_search(
        self,
        query_text: str,
        query_vector: Vector | None,
        *,
        k: int,
        services: set[str] | None = None,
    ) -> list[SearchHit]:
        if k <= 0:
            return []
        pool = max(k * 3, 20)
        lexical = self._search({"size": pool, "query": lexical_query(query_text, services)})
        dense = (
            self._search({"size": pool, "query": knn_query(query_vector, pool, services)})
            if query_vector is not None
            else []
        )

        by_id = {hit_id: (source, score) for hit_id, source, score in lexical + dense}
        lex_scores = {hit_id: score for hit_id, _s, score in lexical}
        fused = _rrf_fuse(
            lexical_ranking=[hid for hid, _s, _sc in lexical],
            dense_ranking=[hid for hid, _s, _sc in dense],
        )
        hits = [
            _to_hit(by_id[cid][0], score=rrf, lexical_score=lex_scores.get(cid, 0.0))
            for cid, rrf in fused
            if cid in by_id
        ]
        return hits[:k]

    def delete_by_file(self, service: str, path: str) -> int:
        result = self._client.delete_by_query(
            index=self._index,
            body={"query": {"bool": {"filter": [
                {"term": {"service": service}}, {"term": {"path": path}},
            ]}}},
            refresh=True,
        )
        return int(result.get("deleted", 0))

    def indexed_files(self) -> set[tuple[str, str]]:
        result = self._client.search(
            index=self._index,
            body={
                "size": 0,
                "aggs": {"files": {"composite": {
                    "size": 10000,
                    "sources": [
                        {"service": {"terms": {"field": "service"}}},
                        {"path": {"terms": {"field": "path"}}},
                    ],
                }}},
            },
        )
        buckets = result.get("aggregations", {}).get("files", {}).get("buckets", [])
        return {(b["key"]["service"], b["key"]["path"]) for b in buckets}

    # -- internals ---------------------------------------------------------- #

    def _search(self, body: dict[str, Any]) -> list[tuple[str, dict[str, Any], float]]:
        body["_source"] = _SOURCE_FIELDS
        result = self._client.search(index=self._index, body=body)
        return [
            (hit["_source"]["chunk_id"], hit["_source"], hit.get("_score") or 0.0)
            for hit in result.get("hits", {}).get("hits", [])
        ]


def _searchable(chunk: Chunk) -> str:
    parts = [chunk.content, " ".join(chunk.identifiers)]
    if chunk.symbol:
        parts.append(chunk.symbol)
    return "\n".join(parts)


def _to_hit(source: dict[str, Any], *, score: float, lexical_score: float) -> SearchHit:
    start, _, end = str(source.get("lines", "1-1")).partition("-")
    return SearchHit(
        chunk_id=source["chunk_id"],
        service=source["service"],
        path=source["path"],
        span=Span(start=int(start or 1), end=int(end or start or 1)),
        language=Language(source.get("language", Language.UNKNOWN.value)),
        kind=ChunkKind(source.get("kind", ChunkKind.MODULE.value)),
        symbol=source.get("symbol"),
        content=source.get("content", ""),
        commit_sha=source.get("commit_sha", ""),
        score=score,
        lexical_score=lexical_score,
    )
