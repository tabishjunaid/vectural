"""Select the dense embedder from configuration.

``hashing`` is the offline stand-in (default, no dependencies); ``bge-m3`` is the
real local model (via the ``embeddings`` extra). The **same** embedder must run at
index time (worker) and query time (API), so both read ``Settings.embedder``.
"""

from __future__ import annotations

from backend.config import Settings
from backend.embedding.base import Embedder
from backend.embedding.hashing import HashingEmbedder


def build_embedder(settings: Settings | None = None) -> Embedder:
    name = settings.embedder if settings is not None else "hashing"
    if name == "bge-m3":
        from backend.embedding.bge import BgeM3Embedder

        return BgeM3Embedder()
    return HashingEmbedder()
