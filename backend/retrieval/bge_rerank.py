"""BGE-reranker-v2-m3 cross-encoder (§5.3 step 6, §3).

The real rerank stage the :class:`~backend.retrieval.rerank.TokenOverlapReranker`
stands in for. A bi-encoder (BGE-M3) scores query and chunk *independently*, so
retrieval is fast but only approximately relevant; a cross-encoder reads the pair
together and can tell that a file about "indexing modules" answers "what are the
main modules", which no amount of token overlap will.

Why this matters here concretely: the token-overlap stand-in re-sorts purely on
Jaccard overlap and **discards the fused retrieval score**, so a good dense
ranking is thrown away at the last step. On the query "main modules of vectural"
that promoted ``LinkedList.java`` (it contains the literal token ``main``) over
``docker-compose.yml``, and the answer path then could not resolve a citation to
real evidence, surfacing as a refusal in the UI.
"""

from __future__ import annotations

import logging

from backend.retrieval.base import SearchHit

_log = logging.getLogger(__name__)

_DEFAULT_MODEL = "BAAI/bge-reranker-v2-m3"
# Query + chunk share one window in a cross-encoder. 512 keeps the pair well
# inside the model's limit and bounds CPU latency, which is the binding cost
# here: every candidate is a forward pass, unlike the bi-encoder's single one.
_MAX_LENGTH = 512
_BATCH = 16


class BgeReranker:
    """Cross-encoder reranker. Loads the model once, at construction."""

    def __init__(self, model: str = _DEFAULT_MODEL, *, max_length: int = _MAX_LENGTH) -> None:
        from sentence_transformers import CrossEncoder

        self._model = CrossEncoder(model, max_length=max_length)
        _log.info("reranker = %s (cross-encoder, max_length=%d)", model, max_length)

    def rerank(self, query_text: str, hits: list[SearchHit], *, top_n: int) -> list[SearchHit]:
        if not hits or not query_text.strip():
            return hits[:top_n]

        pairs = [(query_text, f"{hit.path}\n{hit.symbol or ''}\n{hit.content}") for hit in hits]
        scores = self._model.predict(pairs, batch_size=_BATCH)
        # Stable on ties (enumerate index breaks them), so equal-scoring hits keep
        # the fused order rather than being shuffled by the sort.
        ranked = sorted(enumerate(hits), key=lambda ih: (-float(scores[ih[0]]), ih[0]))
        return [hit for _, hit in ranked[:top_n]]
