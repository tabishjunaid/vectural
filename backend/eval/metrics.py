"""Pure retrieval metrics (§7): recall@k and reciprocal rank.

Relevance is at *file* granularity — a question's known-correct targets are file
paths (a chunk in the right file counts), which is the unit the golden set can be
authored and audited in without pinning exact line ranges that shift over time.
"""

from __future__ import annotations

from collections.abc import Sequence


def rank_of_first_relevant(retrieved: Sequence[str], relevant: set[str]) -> int | None:
    """1-indexed rank of the first retrieved path that is relevant, else ``None``."""
    for i, path in enumerate(retrieved, start=1):
        if path in relevant:
            return i
    return None


def reciprocal_rank(retrieved: Sequence[str], relevant: set[str]) -> float:
    """1/rank of the first relevant result, or 0.0 if none retrieved (MRR term)."""
    rank = rank_of_first_relevant(retrieved, relevant)
    return 1.0 / rank if rank is not None else 0.0


def recall_at_k(retrieved: Sequence[str], relevant: set[str], k: int) -> float:
    """Fraction of relevant paths appearing in the top ``k`` retrieved."""
    if not relevant:
        return 0.0
    top_k = set(retrieved[:k])
    return len(top_k & relevant) / len(relevant)


def hit_at_k(retrieved: Sequence[str], relevant: set[str], k: int) -> bool:
    """Whether any relevant path appears in the top ``k`` (recall's binary cousin)."""
    return bool(set(retrieved[:k]) & relevant)
