"""Embedding abstraction (implementation-plan §3: BGE-M3, 1024-dim).

The platform's production embedder is BGE-M3 served by vLLM/Infinity in-cluster.
That is a network dependency, so retrieval depends only on the :class:`Embedder`
protocol; the deterministic :class:`HashingEmbedder` provides the same interface
offline for development, tests, and the in-memory retrieval path.
"""

from backend.embedding.base import Embedder, Vector, cosine, l2_normalize
from backend.embedding.hashing import HashingEmbedder

__all__ = ["Embedder", "HashingEmbedder", "Vector", "cosine", "l2_normalize"]
