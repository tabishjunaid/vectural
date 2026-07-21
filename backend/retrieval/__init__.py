"""Retrieval (implementation-plan §5.3).

Phase 2 implements steps 5-6 — scoped hybrid evidence search over OpenSearch and
cross-encoder rerank — returning ranked chunks with **no LLM synthesis**
(§Phase 2). The graph-planning steps 1-4 (entity linking → Cypher → execution)
arrive in Phase 3/6; here the service scope is an optional explicit filter.
"""

from backend.retrieval.base import Reranker, SearchBackend, SearchHit
from backend.retrieval.inmemory import InMemorySearchBackend
from backend.retrieval.rerank import NoopReranker, TokenOverlapReranker
from backend.retrieval.service import RetrievalService

__all__ = [
    "InMemorySearchBackend",
    "NoopReranker",
    "Reranker",
    "RetrievalService",
    "SearchBackend",
    "SearchHit",
    "TokenOverlapReranker",
]
