"""Retrieval service: scoped hybrid search + rerank (§5.3 steps 5-6).

This is the Phase 2 deliverable: given a query (and an optional service scope),
return the top reranked chunks. No LLM, no synthesis — the answer path (§5.4)
sits above this and arrives in Phase 6. Keeping this layer synthesis-free is what
lets Phase 2 measure retrieval quality in isolation (§Phase 2 exit criterion).
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.embedding.base import Embedder
from backend.retrieval.base import Reranker, SearchBackend, SearchHit
from backend.retrieval.rerank import NoopReranker

# Fetch a wider candidate set than we return, so the reranker has room to reorder.
DEFAULT_CANDIDATE_K = 20
DEFAULT_TOP_N = 5


@dataclass
class RetrievalService:
    backend: SearchBackend
    embedder: Embedder | None = None
    reranker: Reranker | None = None

    def search(
        self,
        query: str,
        *,
        services: set[str] | None = None,
        candidate_k: int = DEFAULT_CANDIDATE_K,
        top_n: int = DEFAULT_TOP_N,
    ) -> list[SearchHit]:
        """Return up to ``top_n`` reranked chunks for ``query``.

        ``services`` is a hard scope (§5.3 step 5): when set, no out-of-scope
        chunk can appear, matching the graph-plan constraint the real pipeline
        applies before evidence search.
        """
        query_vector = self.embedder.embed_one(query) if self.embedder is not None else None
        candidates = self.backend.hybrid_search(
            query, query_vector, k=candidate_k, services=services
        )
        reranker = self.reranker or NoopReranker()
        return reranker.rerank(query, candidates, top_n=top_n)
