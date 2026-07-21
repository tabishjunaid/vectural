"""Deterministic offline embedder (dev / test / in-memory retrieval).

A hashing-trick bag-of-tokens embedder: each code token is hashed to a bucket
and a sign, accumulated, then L2-normalised. It is **not** semantic — it stands
in for BGE-M3 so the retrieval and eval code can run and be tested without the
model-serving pod. Texts sharing identifiers get non-trivial cosine similarity,
which is enough to exercise the hybrid-fusion path end to end.
"""

from __future__ import annotations

import hashlib

from backend.embedding.base import Vector, l2_normalize
from backend.text import code_tokens

_DEFAULT_DIMS = 1024


class HashingEmbedder:
    def __init__(self, dims: int = _DEFAULT_DIMS) -> None:
        self._dims = dims

    @property
    def dims(self) -> int:
        return self._dims

    def embed(self, texts: list[str]) -> list[Vector]:
        return [self.embed_one(t) for t in texts]

    def embed_one(self, text: str) -> Vector:
        vec = [0.0] * self._dims
        for token in code_tokens(text):
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            bucket = int.from_bytes(digest[:4], "big") % self._dims
            sign = 1.0 if digest[4] & 1 else -1.0
            vec[bucket] += sign
        return l2_normalize(vec)
