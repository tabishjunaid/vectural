"""In-memory hybrid search backend (offline stand-in for OpenSearch).

Implements the same contract the cluster does — BM25 lexical + dense k-NN fused
with Reciprocal Rank Fusion (§4.3 "hybrid BM25 + k-NN via RRF") — in pure Python
so retrieval, the API, and the eval harness all run and are tested with no
infrastructure. It is not built for scale; it is built to be *correct and
identical in shape* to the real path.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field

from backend.domain.models import Chunk
from backend.embedding.base import Embedder, Vector, cosine
from backend.retrieval.base import SearchHit
from backend.text import code_tokens

_BM25_K1 = 1.2
_BM25_B = 0.75
_RRF_K = 60  # standard RRF damping constant


@dataclass
class _Doc:
    chunk: Chunk
    tokens: Counter[str]
    length: int
    vector: Vector | None


@dataclass
class InMemorySearchBackend:
    """A tiny hybrid index. Pass an embedder to enable the dense half offline;
    without one, only the BM25 half contributes (still a valid degraded mode)."""

    embedder: Embedder | None = None
    _docs: dict[str, _Doc] = field(default_factory=dict)
    _df: Counter[str] = field(default_factory=Counter)
    _total_length: int = 0

    def index(self, chunks: list[Chunk]) -> None:
        for chunk in chunks:
            searchable = _searchable_text(chunk)
            tokens = Counter(code_tokens(searchable))
            vector = chunk.embedding
            if vector is None and self.embedder is not None:
                vector = self.embedder.embed_one(searchable)

            if chunk.chunk_id in self._docs:  # overwrite-in-place (stable ids)
                self._remove(chunk.chunk_id)
            self._docs[chunk.chunk_id] = _Doc(chunk, tokens, sum(tokens.values()), vector)
            self._total_length += sum(tokens.values())
            for term in tokens:
                self._df[term] += 1

    def hybrid_search(
        self,
        query_text: str,
        query_vector: Vector | None,
        *,
        k: int,
        services: set[str] | None = None,
    ) -> list[SearchHit]:
        candidates = [
            d for d in self._docs.values() if services is None or d.chunk.service in services
        ]
        if not candidates or k <= 0:
            return []

        query_tokens = code_tokens(query_text)
        lexical = {d.chunk.chunk_id: self._bm25(query_tokens, d) for d in candidates}
        dense = {
            d.chunk.chunk_id: (
                cosine(query_vector, d.vector) if query_vector is not None and d.vector else 0.0
            )
            for d in candidates
        }

        fused = _rrf_fuse(
            lexical_ranking=_ranked_ids(lexical),
            dense_ranking=_ranked_ids(dense) if query_vector is not None else [],
        )

        by_id = {d.chunk.chunk_id: d for d in candidates}
        hits = [
            SearchHit.from_chunk(
                by_id[cid].chunk,
                score=rrf,
                lexical_score=lexical[cid],
                dense_score=dense[cid],
            )
            for cid, rrf in fused
        ]
        # Deterministic order: RRF desc, then lexical desc, then chunk_id.
        hits.sort(key=lambda h: (-h.score, -h.lexical_score, h.chunk_id))
        return hits[:k]

    # -- internals ---------------------------------------------------------- #

    def _avgdl(self) -> float:
        return self._total_length / len(self._docs) if self._docs else 0.0

    def _bm25(self, query_tokens: list[str], doc: _Doc) -> float:
        if not query_tokens or doc.length == 0:
            return 0.0
        n = len(self._docs)
        avgdl = self._avgdl() or 1.0
        score = 0.0
        for term in set(query_tokens):
            tf = doc.tokens.get(term, 0)
            if tf == 0:
                continue
            df = self._df.get(term, 0)
            idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
            denom = tf + _BM25_K1 * (1 - _BM25_B + _BM25_B * doc.length / avgdl)
            score += idf * (tf * (_BM25_K1 + 1)) / denom
        return score

    def _remove(self, chunk_id: str) -> None:
        doc = self._docs.pop(chunk_id)
        self._total_length -= doc.length
        for term in doc.tokens:
            self._df[term] -= 1
            if self._df[term] <= 0:
                del self._df[term]


def _searchable_text(chunk: Chunk) -> str:
    parts = [chunk.content, " ".join(chunk.identifiers)]
    if chunk.symbol:
        parts.append(chunk.symbol)
    return "\n".join(parts)


def _ranked_ids(scores: dict[str, float]) -> list[str]:
    """Ids ordered by score desc, dropping zero scores (they carry no signal),
    tie-broken by id for determinism."""
    scored = [(cid, s) for cid, s in scores.items() if s > 0.0]
    scored.sort(key=lambda kv: (-kv[1], kv[0]))
    return [cid for cid, _ in scored]


def _rrf_fuse(
    *, lexical_ranking: list[str], dense_ranking: list[str]
) -> list[tuple[str, float]]:
    rrf: dict[str, float] = {}
    for ranking in (lexical_ranking, dense_ranking):
        for rank, cid in enumerate(ranking, start=1):
            rrf[cid] = rrf.get(cid, 0.0) + 1.0 / (_RRF_K + rank)
    fused = list(rrf.items())
    fused.sort(key=lambda kv: (-kv[1], kv[0]))
    return fused
