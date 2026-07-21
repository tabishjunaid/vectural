"""Hybrid retrieval, scoping, rerank, and query construction (§5.3)."""

from __future__ import annotations

from backend.domain.models import Chunk, ChunkKind, Language, Span
from backend.embedding import HashingEmbedder
from backend.retrieval import (
    InMemorySearchBackend,
    NoopReranker,
    RetrievalService,
    TokenOverlapReranker,
)
from backend.retrieval.opensearch_query import hybrid_query, lexical_query


def _chunk(cid: str, service: str, path: str, content: str, ids: list[str], symbol: str) -> Chunk:
    return Chunk(
        chunk_id=cid, service=service, path=path, language=Language.PYTHON,
        kind=ChunkKind.FUNCTION, span=Span(start=1, end=5), content=content,
        identifiers=ids, symbol=symbol, commit_sha="x", content_hash=cid.ljust(16, "0"),
    )


CORPUS = [
    _chunk("c1", "payments-api", "payments-api/refund.py",
           "def reverse_refund(refund_id): publish(refund_id)",
           ["reverse_refund", "refund_id", "publish"], "reverse_refund"),
    _chunk("c2", "ledger-svc", "ledger-svc/ledger.py",
           "def apply_charge(amount): save(amount)", ["apply_charge", "save"], "apply_charge"),
    _chunk("c3", "notification-svc", "notification-svc/notify.py",
           "def notify_customer(msg): send(msg)", ["notify_customer", "send"], "notify_customer"),
]


def _service() -> RetrievalService:
    emb = HashingEmbedder()
    backend = InMemorySearchBackend(embedder=emb)
    backend.index(CORPUS)
    return RetrievalService(backend=backend, embedder=emb, reranker=TokenOverlapReranker())


def test_finds_relevant_chunk_first() -> None:
    hits = _service().search("how does a refund reversal work", top_n=3)
    assert hits
    assert hits[0].chunk_id == "c1"


def test_identifier_tokenisation_matches_split_words() -> None:
    # "refund reversal" must reach reverse_refund via code-analyzer tokenisation.
    hits = _service().search("refund reversal", top_n=3)
    assert any(h.chunk_id == "c1" for h in hits)


def test_service_scope_is_a_hard_filter() -> None:
    hits = _service().search("charge", services={"ledger-svc"}, top_n=5)
    assert hits
    assert all(h.service == "ledger-svc" for h in hits)


def test_hybrid_fuses_lexical_and_dense() -> None:
    emb = HashingEmbedder()
    backend = InMemorySearchBackend(embedder=emb)
    backend.index(CORPUS)
    hit = backend.hybrid_search("reverse_refund", emb.embed_one("reverse_refund"), k=1)[0]
    assert hit.lexical_score > 0.0
    assert hit.dense_score > 0.0
    assert hit.score > 0.0  # RRF of both


def test_lexical_only_when_no_vector() -> None:
    backend = InMemorySearchBackend()  # no embedder -> dense half empty
    backend.index(CORPUS)
    hits = backend.hybrid_search("apply_charge", None, k=3)
    assert hits[0].chunk_id == "c2"
    assert hits[0].dense_score == 0.0


def test_reindex_same_id_overwrites_not_duplicates() -> None:
    backend = InMemorySearchBackend()
    backend.index(CORPUS)
    backend.index([CORPUS[0]])  # same id again
    hits = backend.hybrid_search("refund", None, k=10)
    assert sum(1 for h in hits if h.chunk_id == "c1") == 1


def test_noop_vs_overlap_reranker_top_n() -> None:
    emb = HashingEmbedder()
    backend = InMemorySearchBackend(embedder=emb)
    backend.index(CORPUS)
    svc_noop = RetrievalService(backend=backend, embedder=emb, reranker=NoopReranker())
    assert len(svc_noop.search("refund", top_n=1)) == 1


def test_opensearch_query_shapes() -> None:
    lex = lexical_query("refund reversal", services={"payments-api"})
    assert lex["bool"]["filter"] == [{"terms": {"service": ["payments-api"]}}]

    body = hybrid_query("refund", [0.1, 0.2, 0.3], k=5, services={"payments-api"})
    assert body["size"] == 5
    assert "hybrid" in body["query"]
    assert len(body["query"]["hybrid"]["queries"]) == 2

    lexical_only = hybrid_query("refund", None, k=5)
    assert "hybrid" not in lexical_only["query"]
