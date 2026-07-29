"""FastAPI application factory.

``create_app(retrieval, answer_service=…)`` takes already-wired services, so the
same app runs over the OpenSearch/Neo4j stack in production and the in-memory
stack in tests — the HTTP surface never knows which. This is the dependency
boundary that keeps the API testable with no infrastructure.

- ``/search`` (Phase 2): ranked chunks, no synthesis.
- ``/ask`` (Phase 6): the full answer path — returns one of the terminal states
  (synthesized / instant / refusal) via the ``mode`` field.
- ``/ask/stream``: the same result as Server-Sent Events (design-doc §6 SSE edge).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from backend.answer.models import Answer
from backend.answer.service import AnswerService, AnswerStage
from backend.api.answer_schemas import AskRequest
from backend.api.coverage import CoverageRow, CoverageService
from backend.api.review_schemas import ReviewAction
from backend.api.schemas import HealthResponse, SearchRequest, SearchResponse
from backend.domain.models import Depth, Persona
from backend.flows.models import FlowNarrative
from backend.flows.service import FlowNarrativeService, FlowNotFoundError
from backend.ingestion.manager import IngestionError, IngestionService, RepoState
from backend.llm import catalog
from backend.observability.metrics import MetricsCollector, MetricsSnapshot
from backend.retrieval.service import RetrievalService


def _get_retrieval(request: Request) -> RetrievalService:
    service = getattr(request.app.state, "retrieval", None)
    if not isinstance(service, RetrievalService):  # pragma: no cover - misconfiguration guard
        raise RuntimeError("RetrievalService not configured on app.state.retrieval")
    return service


def _get_answer(request: Request) -> AnswerService:
    service = getattr(request.app.state, "answer_service", None)
    if not isinstance(service, AnswerService):
        raise HTTPException(status_code=501, detail="answer path not enabled")
    return service


def _get_flows(request: Request) -> FlowNarrativeService:
    service = getattr(request.app.state, "flow_service", None)
    if not isinstance(service, FlowNarrativeService):
        raise HTTPException(status_code=501, detail="flow review not enabled")
    return service


def _get_ingestion(request: Request) -> IngestionService:
    service = getattr(request.app.state, "ingestion_service", None)
    if not isinstance(service, IngestionService):
        raise HTTPException(status_code=501, detail="ingestion not enabled")
    return service


RetrievalDep = Annotated[RetrievalService, Depends(_get_retrieval)]
AnswerDep = Annotated[AnswerService, Depends(_get_answer)]
FlowsDep = Annotated[FlowNarrativeService, Depends(_get_flows)]
IngestionDep = Annotated[IngestionService, Depends(_get_ingestion)]


class AddRepoRequest(BaseModel):
    url: str


class EstimateRequest(BaseModel):
    model: str | None = None


class ModelOption(BaseModel):
    """One entry in the answer-model dropdown (GET /models)."""

    id: str
    label: str
    provider: str
    hint: str


def create_app(
    retrieval: RetrievalService,
    *,
    answer_service: AnswerService | None = None,
    flow_service: FlowNarrativeService | None = None,
    coverage_service: CoverageService | None = None,
    ingestion_service: IngestionService | None = None,
    metrics: MetricsCollector | None = None,
    cors_origins: list[str] | None = None,
    model_providers: set[str] | None = None,
) -> FastAPI:
    app = FastAPI(
        title="Vectural API",
        version="0.1.0",
        summary="Retrieval + graph-planned cited answers over the estate.",
    )
    app.state.retrieval = retrieval
    app.state.answer_service = answer_service
    app.state.flow_service = flow_service
    app.state.coverage_service = coverage_service
    app.state.ingestion_service = ingestion_service
    app.state.metrics = metrics
    # Providers a per-question model override can reach (drives GET /models). When
    # unset (e.g. tests calling create_app directly), fall back to whatever
    # providers the catalog knows so the dropdown is still populated.
    app.state.model_providers = model_providers or {m.provider for m in catalog.SELECTABLE_MODELS}

    # The React frontend (a different origin in dev) calls this API directly.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins or ["http://localhost:5175", "http://localhost:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/healthz", response_model=HealthResponse, tags=["ops"])
    def healthz() -> HealthResponse:
        return HealthResponse()

    @app.get("/metrics", response_model=MetricsSnapshot, tags=["ops"])
    def metrics_endpoint() -> MetricsSnapshot:
        collector = getattr(app.state, "metrics", None)
        if not isinstance(collector, MetricsCollector):
            raise HTTPException(status_code=501, detail="metrics not enabled")
        return collector.snapshot()

    @app.get("/coverage", response_model=list[CoverageRow], tags=["coverage"])
    def coverage() -> list[CoverageRow]:
        service = getattr(app.state, "coverage_service", None)
        if not isinstance(service, CoverageService):
            raise HTTPException(status_code=501, detail="coverage not enabled")
        return service.rows()

    @app.get("/ingest/repos", response_model=list[RepoState], tags=["ingestion"])
    def ingest_list(service: IngestionDep) -> list[RepoState]:
        return service.list_repos()

    @app.post("/ingest/repos", response_model=RepoState, tags=["ingestion"])
    def ingest_add(req: AddRepoRequest, service: IngestionDep) -> RepoState:
        try:
            return service.add_repo(req.url)
        except IngestionError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/ingest/repos/{repo}/estimate", tags=["ingestion"])
    def ingest_estimate(
        repo: str, req: EstimateRequest, service: IngestionDep
    ) -> dict[str, object]:
        try:
            return service.estimate(repo, model=req.model)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"unknown repo {repo!r}") from exc

    @app.delete("/ingest/repos/{repo}", tags=["ingestion"])
    def ingest_drop(repo: str, service: IngestionDep) -> dict[str, int]:
        try:
            return service.drop(repo)
        except IngestionError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

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

    @app.get("/models", response_model=list[ModelOption], tags=["answer"])
    def models() -> list[ModelOption]:
        # Only models whose provider gateway is actually wired — otherwise the
        # dropdown would offer a model whose calls just fail.
        providers = getattr(app.state, "model_providers", None) or set()
        return [
            ModelOption(id=m.id, label=m.label, provider=m.provider, hint=m.hint)
            for m in catalog.available_models(providers)
        ]

    @app.post("/ask", response_model=Answer, tags=["answer"])
    def ask(req: AskRequest, service: AnswerDep) -> Answer:
        return service.answer(req.question, req.persona, req.depth, req.model)

    @app.post("/ask/stream", tags=["answer"])
    def ask_stream(req: AskRequest, service: AnswerDep) -> StreamingResponse:
        # Real progress: the pipeline is streamed as it runs, so a `stage` event
        # reaches the client the instant each step starts — not after the whole
        # answer is already computed. `X-Accel-Buffering: no` stops nginx from
        # buffering the stream and defeating that.
        return StreamingResponse(
            _sse(service, req.question, req.persona, req.depth, req.model),
            media_type="text/event-stream",
            headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
        )

    # -- architect flow-narrative review (§Phase 7) ------------------------- #

    @app.get("/review/queue", response_model=list[FlowNarrative], tags=["review"])
    def review_queue(flows: FlowsDep) -> list[FlowNarrative]:
        return flows.queue()

    @app.get("/review/{flow_id}", response_model=FlowNarrative, tags=["review"])
    def review_get(flow_id: str, flows: FlowsDep) -> FlowNarrative:
        narrative = flows.get(flow_id)
        if narrative is None:
            raise HTTPException(status_code=404, detail=f"flow {flow_id} not found")
        return narrative

    @app.post("/review/{flow_id}/approve", response_model=FlowNarrative, tags=["review"])
    def review_approve(flow_id: str, action: ReviewAction, flows: FlowsDep) -> FlowNarrative:
        _require_architect(action.persona)
        return _flow_action(lambda: flows.approve(flow_id, action.architect))

    @app.post("/review/{flow_id}/request-changes", response_model=FlowNarrative, tags=["review"])
    def review_request_changes(
        flow_id: str, action: ReviewAction, flows: FlowsDep
    ) -> FlowNarrative:
        _require_architect(action.persona)
        if not action.reason:
            raise HTTPException(status_code=422, detail="reason required for request-changes")
        return _flow_action(
            lambda: flows.request_changes(flow_id, action.architect, action.reason or "")
        )

    @app.post("/review/{flow_id}/reject", response_model=FlowNarrative, tags=["review"])
    def review_reject(flow_id: str, action: ReviewAction, flows: FlowsDep) -> FlowNarrative:
        _require_architect(action.persona)
        return _flow_action(lambda: flows.reject(flow_id, action.architect, action.reason))

    return app


def _require_architect(persona: Persona) -> None:
    # The review surface is architect-only (the only write surface in the product).
    if persona is not Persona.ARCHITECT:
        raise HTTPException(status_code=403, detail="flow review is architect-only")


def _flow_action(action: Callable[[], FlowNarrative]) -> FlowNarrative:
    try:
        return action()
    except FlowNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"flow {exc} not found") from exc


def _sse(
    service: AnswerService,
    question: str,
    persona: Persona,
    depth: Depth,
    model: str | None = None,
) -> Iterator[str]:
    """Stream the answer path as SSE: a ``stage`` event as each pipeline step runs,
    then a final ``done`` event with the answer (citations / refusal metadata).

    The generator is the live pipeline, so events are flushed as the work happens —
    the client sees "Searching…", "Drafting…", "Verifying…" while the slow gateway
    calls are still in flight, instead of nothing until the whole answer is ready."""
    for item in service.stream(question, persona, depth, model):
        if isinstance(item, AnswerStage):
            yield _event("stage", item.model_dump())
        else:  # the terminal Answer — exactly one, last
            yield _event("done", item.model_dump(mode="json"))


def _event(name: str, data: dict[str, object]) -> str:
    return f"event: {name}\ndata: {json.dumps(data)}\n\n"
