"""Embedder selection (§5.3). The bge-m3 wrapper is exercised with a fake
SentenceTransformer so no 2 GB model download is needed offline."""

from __future__ import annotations

import sys
import types

import pytest

from backend.config import Settings
from backend.embedding.factory import build_embedder
from backend.embedding.hashing import HashingEmbedder


def test_default_is_hashing() -> None:
    assert isinstance(build_embedder(None), HashingEmbedder)
    assert isinstance(build_embedder(Settings(embedder="hashing")), HashingEmbedder)
    assert build_embedder(None).dims == 1024  # matches the OpenSearch knn_vector dim


class _FakeST:
    def __init__(self, model: str) -> None:
        self.model = model

    def encode(self, texts: list[str], **kwargs: object) -> list[list[float]]:
        return [[0.1] * 1024 for _ in texts]  # 1024-dim rows, like BGE-M3


def test_bge_m3_selected_and_wrapped(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = types.ModuleType("sentence_transformers")
    fake.SentenceTransformer = _FakeST  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake)

    from backend.embedding.bge import BgeM3Embedder

    embedder = build_embedder(Settings(embedder="bge-m3"))
    assert isinstance(embedder, BgeM3Embedder)
    assert embedder.dims == 1024
    vec = embedder.embed_one("hello")
    assert len(vec) == 1024
    assert all(isinstance(x, float) for x in vec)
    assert embedder.embed([]) == []
