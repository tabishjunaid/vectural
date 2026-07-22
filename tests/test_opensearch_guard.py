"""OpenSearch index guard: a zero-norm embedding must not abort the bulk (§5.3).

A trivial file (e.g. content ".") embeds to an all-zero vector, which OpenSearch's
cosinesimil knn_vector rejects. One such chunk previously failed the entire estate
bulk; the guard drops it instead. No real cluster needed — the bulk call is stubbed.
"""

from __future__ import annotations

from typing import Any

import pytest

from backend.domain.models import Chunk, ChunkKind, Language, Span
from backend.embedding.base import Vector
from backend.retrieval.opensearch_backend import OpenSearchBackend


class _ZeroForDotEmbedder:
    """Embeds "." to a zero vector (un-embeddable), everything else non-zero."""

    dims = 4

    def embed(self, texts: list[str]) -> list[Vector]:
        return [self.embed_one(t) for t in texts]

    def embed_one(self, text: str) -> Vector:
        return [0.0, 0.0, 0.0, 0.0] if text.strip() == "." else [1.0, 0.0, 0.0, 0.0]


def _chunk(cid: str, content: str) -> Chunk:
    return Chunk(
        chunk_id=cid, service="svc", path="svc/f", language=Language.PYTHON,
        kind=ChunkKind.MODULE, span=Span(start=1, end=1), content=content,
        commit_sha="c", content_hash=cid.ljust(16, "0"),
    )


def test_zero_norm_chunk_is_skipped_not_indexed(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[dict[str, Any]] = []

    def fake_bulk(client: Any, actions: Any, **kwargs: Any) -> tuple[int, list[Any]]:
        acts = list(actions)
        captured.extend(acts)
        return len(acts), []

    import opensearchpy.helpers

    monkeypatch.setattr(opensearchpy.helpers, "bulk", fake_bulk)

    backend = OpenSearchBackend(client=object(), index="test-idx", embedder=_ZeroForDotEmbedder())
    backend.index([_chunk("good", "def f(): ..."), _chunk("dot", ".")])

    indexed_ids = {a["_id"] for a in captured}
    assert indexed_ids == {"good"}  # the zero-norm "dot" chunk was dropped, not sent
