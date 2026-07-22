"""Real BGE-M3 dense embedder (optional ``embeddings`` extra) — the §5.3 semantic
retrieval seam the ``HashingEmbedder`` stands in for.

BGE-M3 (``BAAI/bge-m3``) produces **1024-dim** dense vectors — the exact dimension
of the OpenSearch ``knn_vector`` (``opensearch_template.py``) and the hashing stub,
so swapping it in needs no index change, only re-embedding. Vectors are L2-normalised
so cosine similarity is a dot product. The model is loaded once and reused; the first
load downloads ~2 GB into the HuggingFace cache.
"""

from __future__ import annotations

from backend.embedding.base import Vector

_MODEL = "BAAI/bge-m3"
_DIMS = 1024
# Cap sequence length: BGE-M3 defaults to an 8192-token window, but its full O(n²)
# attention makes long inputs blow up CPU memory (OOM) and time. Code chunks are
# function-sized, so 512 tokens is ample for retrieval and keeps it fast + frugal.
_MAX_SEQ = 512
_BATCH = 16  # modest batch so peak memory stays bounded on CPU


class BgeM3Embedder:
    def __init__(self, model: str = _MODEL, *, max_seq_length: int = _MAX_SEQ) -> None:
        # Lazy import so the base install (and every offline code path) never needs
        # sentence-transformers / torch.
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(model)
        self._model.max_seq_length = max_seq_length

    @property
    def dims(self) -> int:
        return _DIMS

    def embed(self, texts: list[str]) -> list[Vector]:
        if not texts:
            return []
        vectors = self._model.encode(
            texts, normalize_embeddings=True, convert_to_numpy=True, batch_size=_BATCH
        )
        return [[float(x) for x in row] for row in vectors]

    def embed_one(self, text: str) -> Vector:
        return self.embed([text])[0]
