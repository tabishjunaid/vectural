"""Deterministic embedder + vector helpers."""

from __future__ import annotations

import pytest

from backend.embedding import HashingEmbedder, cosine, l2_normalize


def test_dims_and_determinism() -> None:
    emb = HashingEmbedder()
    assert emb.dims == 1024
    a = emb.embed_one("reverse_refund")
    b = emb.embed_one("reverse_refund")
    assert a == b
    assert len(a) == 1024


def test_normalized_unit_length() -> None:
    v = HashingEmbedder(dims=64).embed_one("apply charge to ledger")
    assert abs(sum(x * x for x in v) - 1.0) < 1e-9


def test_shared_tokens_more_similar_than_unrelated() -> None:
    emb = HashingEmbedder()
    q = emb.embed_one("refund reversal propagate")
    related = emb.embed_one("reverse_refund propagates the refund")
    unrelated = emb.embed_one("kubernetes pod scheduler affinity")
    assert cosine(q, related) > cosine(q, unrelated)


def test_cosine_edge_cases() -> None:
    assert cosine([0.0, 0.0], [1.0, 1.0]) == 0.0  # zero vector -> no signal
    assert cosine([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    with pytest.raises(ValueError, match="length mismatch"):
        cosine([1.0], [1.0, 2.0])


def test_l2_normalize_zero_vector_safe() -> None:
    assert l2_normalize([0.0, 0.0, 0.0]) == [0.0, 0.0, 0.0]
