"""Embedder protocol and small vector helpers."""

from __future__ import annotations

import math
from typing import Protocol, runtime_checkable

Vector = list[float]


@runtime_checkable
class Embedder(Protocol):
    """Turns text into dense vectors. The one seam every dense-retrieval path
    depends on, so the real BGE-M3 client and the offline stub are swappable."""

    @property
    def dims(self) -> int: ...

    def embed(self, texts: list[str]) -> list[Vector]:
        """Embed a batch. Batching is explicit because the real serving pod is
        far more efficient per-call batched than one text at a time."""
        ...

    def embed_one(self, text: str) -> Vector:
        ...


def l2_normalize(vec: Vector) -> Vector:
    norm = math.sqrt(sum(v * v for v in vec))
    if norm == 0.0:
        return list(vec)
    return [v / norm for v in vec]


def cosine(a: Vector, b: Vector) -> float:
    """Cosine similarity. Returns 0.0 for a zero vector rather than dividing by
    zero, so an un-embeddable input degrades to "no dense signal", not a crash."""
    if len(a) != len(b):
        raise ValueError(f"vector length mismatch: {len(a)} vs {len(b)}")
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)
