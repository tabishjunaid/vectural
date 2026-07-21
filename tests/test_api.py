"""FastAPI retrieval surface (§Phase 2)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from backend.api import create_app
from backend.retrieval.service import RetrievalService


def _client(retrieval_service: RetrievalService) -> TestClient:
    return TestClient(create_app(retrieval_service))


def test_healthz(retrieval_service: RetrievalService) -> None:
    resp = _client(retrieval_service).get("/healthz")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_search_returns_ranked_chunks(retrieval_service: RetrievalService) -> None:
    resp = _client(retrieval_service).post(
        "/search", json={"query": "how does a refund reversal propagate", "persona": "engineer"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["persona"] == "engineer"
    assert body["count"] == len(body["hits"])
    assert body["hits"], "expected at least one hit"
    top = body["hits"][0]
    # Hit is directly citable: chunk_id -> path + lines.
    assert top["path"].startswith("payments-api/")
    assert "span" in top and "chunk_id" in top


def test_search_scope_filters_services(retrieval_service: RetrievalService) -> None:
    resp = _client(retrieval_service).post(
        "/search", json={"query": "reverse", "services": ["ledger-svc"]}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["scoped_services"] == ["ledger-svc"]
    assert all(h["service"] == "ledger-svc" for h in body["hits"])


def test_search_rejects_empty_query(retrieval_service: RetrievalService) -> None:
    resp = _client(retrieval_service).post("/search", json={"query": ""})
    assert resp.status_code == 422


def test_search_rejects_unknown_persona(retrieval_service: RetrievalService) -> None:
    resp = _client(retrieval_service).post(
        "/search", json={"query": "refund", "persona": "ceo"}
    )
    assert resp.status_code == 422
