"""FastAPI application factory.

``create_app(retrieval)`` takes an already-wired :class:`RetrievalService`, so
the same app runs over the OpenSearch backend in production and the in-memory
backend in tests — the HTTP surface never knows which. This is the dependency
boundary that keeps the API testable with no infrastructure.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, FastAPI, Request

from backend.api.schemas import HealthResponse, SearchRequest, SearchResponse
from backend.retrieval.service import RetrievalService


def _get_retrieval(request: Request) -> RetrievalService:
    service = getattr(request.app.state, "retrieval", None)
    if not isinstance(service, RetrievalService):  # pragma: no cover - misconfiguration guard
        raise RuntimeError("RetrievalService not configured on app.state.retrieval")
    return service


RetrievalDep = Annotated[RetrievalService, Depends(_get_retrieval)]


def create_app(retrieval: RetrievalService) -> FastAPI:
    app = FastAPI(
        title="Vectural Retrieval API",
        version="0.1.0",
        summary="Hybrid retrieval over the estate (Phase 2 — ranked chunks, no synthesis).",
    )
    app.state.retrieval = retrieval

    @app.get("/healthz", response_model=HealthResponse, tags=["ops"])
    def healthz() -> HealthResponse:
        return HealthResponse()

    @app.post("/search", response_model=SearchResponse, tags=["retrieval"])
    def search(req: SearchRequest, service: RetrievalDep) -> SearchResponse:
        scope = set(req.services) if req.services else None
        hits = service.search(
            req.query, services=scope, candidate_k=req.candidate_k, top_n=req.top_n
        )
        return SearchResponse(
            query=req.query,
            persona=req.persona,
            scoped_services=sorted(scope) if scope else None,
            count=len(hits),
            hits=hits,
        )

    return app
