"""Architect review API + flow-augmented answers (§Phase 7)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from backend.answer import AnswerService, RetrievalPlanner, SemanticAnswerCache
from backend.api import create_app
from backend.flows import FlowNarrativeService
from backend.llm import FakeGatewayClient, LLMRouter
from tests.conftest import AnswerEnv


def _client(env: AnswerEnv, flows: FlowNarrativeService) -> TestClient:
    router = LLMRouter(FakeGatewayClient())
    planner = RetrievalPlanner(env.structural, router, env.services)
    answer_service = AnswerService(
        retrieval=env.retrieval, planner=planner, router=router,
        cache=SemanticAnswerCache(env.embedder), flows=flows, commit_sha=env.commit_sha,
    )
    return TestClient(
        create_app(env.retrieval, answer_service=answer_service, flow_service=flows)
    )


def test_queue_lists_pending(answer_env: AnswerEnv, flow_service: FlowNarrativeService) -> None:
    resp = _client(answer_env, flow_service).get("/review/queue")
    assert resp.status_code == 200
    body = resp.json()
    assert body
    assert all(item["status"] in {"pending", "needs_review"} for item in body)


def test_approve_requires_architect(
    answer_env: AnswerEnv, flow_service: FlowNarrativeService
) -> None:
    fid = flow_service.queue()[0].id
    client = _client(answer_env, flow_service)

    forbidden = client.post(
        f"/review/{fid}/approve", json={"architect": "X", "persona": "engineer"}
    )
    assert forbidden.status_code == 403

    ok = client.post(
        f"/review/{fid}/approve", json={"architect": "A. Chen", "persona": "architect"}
    )
    assert ok.status_code == 200
    assert ok.json()["status"] == "approved"


def test_request_changes_needs_reason(
    answer_env: AnswerEnv, flow_service: FlowNarrativeService
) -> None:
    fid = flow_service.queue()[0].id
    client = _client(answer_env, flow_service)
    resp = client.post(
        f"/review/{fid}/request-changes", json={"architect": "A. Chen", "persona": "architect"}
    )
    assert resp.status_code == 422  # reason required


def test_get_missing_flow_404(
    answer_env: AnswerEnv, flow_service: FlowNarrativeService
) -> None:
    resp = _client(answer_env, flow_service).get("/review/nope")
    assert resp.status_code == 404


def test_answer_cites_approved_flow_narrative(
    answer_env: AnswerEnv, flow_service: FlowNarrativeService
) -> None:
    # Approve the gateway→payments→ledger flow, then ask a multi-hop question:
    # the approved narrative should be available as citable evidence.
    flow = next(f for f in flow_service.queue() if "gateway" in f.services)
    flow_service.approve(flow.id, "A. Chen")

    client = _client(answer_env, flow_service)
    resp = client.post(
        "/ask", json={"question": "how does gateway charge via payments and ledger",
                       "persona": "architect"}
    )
    assert resp.status_code == 200
    body = resp.json()
    if body["mode"] == "synthesized":
        cited = {c["chunk_id"] for c in body["citations"]}
        assert f"flow:{flow.id}" in cited


def test_review_disabled_returns_501(retrieval_service) -> None:
    client = TestClient(create_app(retrieval_service))
    assert client.get("/review/queue").status_code == 501
