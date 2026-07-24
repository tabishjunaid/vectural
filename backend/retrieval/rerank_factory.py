"""Select the rerank stage from configuration (§5.3 step 6).

- ``bge``            — the real cross-encoder (BGE-reranker-v2-m3), via the
  ``embeddings`` extra. What production uses (§3).
- ``token-overlap``  — offline Jaccard stand-in. Exercises the interface with no
  model, but **overrides** the fused ranking rather than refining it, so it is a
  poor default once real dense retrieval is in play.
- ``noop``           — keep the fused order untouched; the honest choice when no
  reranker model is available, and the right control arm in evals.

Unlike the gateway, an unknown name here degrades rather than raises: a wrong
reranker returns worse ordering, never a wrong or fabricated answer.
"""

from __future__ import annotations

import logging

from backend.config import Settings
from backend.retrieval.base import Reranker
from backend.retrieval.rerank import NoopReranker, TokenOverlapReranker

_log = logging.getLogger(__name__)


def build_reranker(settings: Settings | None = None) -> Reranker:
    name = (settings.reranker if settings is not None else "token-overlap").strip().lower()

    if name == "bge":
        from backend.retrieval.bge_rerank import BgeReranker

        try:
            return BgeReranker()
        except Exception as exc:  # model missing / offline cache empty
            # Fall back to keeping the fused order. Falling back to token-overlap
            # would be worse than doing nothing: it would actively re-sort a good
            # dense ranking by literal token overlap.
            _log.warning(
                "reranker=bge could not load (%s) — falling back to NoopReranker "
                "(fused order kept). Set HF_HUB_OFFLINE=0 once to download it.",
                exc,
            )
            return NoopReranker()

    if name == "noop":
        return NoopReranker()
    return TokenOverlapReranker()
