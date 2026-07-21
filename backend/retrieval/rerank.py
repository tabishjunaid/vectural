"""Rerankers (§5.3 step 6).

Production uses BGE-reranker-v2-m3 (a cross-encoder) on the serving pod — "the
highest accuracy-per-infrastructure component in the system" (§3). Offline we
provide a no-op and a token-overlap stand-in so the rerank stage is exercised
end to end and its interface is pinned before the model is wired in.
"""

from __future__ import annotations

from backend.retrieval.base import SearchHit
from backend.text import code_tokens


class NoopReranker:
    """Keeps the fused order, truncating to ``top_n``. Useful to isolate the
    effect of the reranker in evals (rerank on vs. off)."""

    def rerank(self, query_text: str, hits: list[SearchHit], *, top_n: int) -> list[SearchHit]:
        return hits[:top_n]


class TokenOverlapReranker:
    """A cheap cross-encoder stand-in: re-score by query/candidate token overlap
    (Jaccard), stable-sorted so equal overlaps preserve the fused order."""

    def rerank(self, query_text: str, hits: list[SearchHit], *, top_n: int) -> list[SearchHit]:
        query = set(code_tokens(query_text))
        if not query:
            return hits[:top_n]

        def overlap(hit: SearchHit) -> float:
            cand = set(code_tokens(f"{hit.content}\n{hit.symbol or ''}"))
            if not cand:
                return 0.0
            return len(query & cand) / len(query | cand)

        ranked = sorted(enumerate(hits), key=lambda ih: (-overlap(ih[1]), ih[0]))
        return [hit for _, hit in ranked[:top_n]]
