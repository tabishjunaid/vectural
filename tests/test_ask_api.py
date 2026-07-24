"""The /ask answer endpoint + SSE stream (§Phase 6)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from backend.answer import AnswerService, RetrievalPlanner, SemanticAnswerCache
from backend.api import create_app
from backend.llm import FakeGatewayClient, LLMRouter
from tests.conftest import AnswerEnv


def _client(env: AnswerEnv) -> TestClient:
    router = LLMRouter(FakeGatewayClient())
    planner = RetrievalPlanner(env.structural, router, env.services)
    answer_service = AnswerService(
        retrieval=env.retrieval, planner=planner, router=router,
        cache=SemanticAnswerCache(env.embedder), commit_sha=env.commit_sha,
    )
    return TestClient(create_app(env.retrieval, answer_service=answer_service))


def test_ask_returns_synthesized_answer(answer_env: AnswerEnv) -> None:
    resp = _client(answer_env).post(
        "/ask", json={"question": "how does gateway charge via payments", "persona": "engineer"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] in {"synthesized", "refusal"}
    if body["mode"] == "synthesized":
        assert body["citations"]
    assert body["persona"] == "engineer"


def test_ask_rejects_empty_question(answer_env: AnswerEnv) -> None:
    resp = _client(answer_env).post("/ask", json={"question": ""})
    assert resp.status_code == 422


def test_ask_stream_emits_sse_events(answer_env: AnswerEnv) -> None:
    resp = _client(answer_env).post(
        "/ask/stream", json={"question": "how does gateway charge via payments"}
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    body = resp.text
    # Live progress: a `stage` event per pipeline step, then one terminal `done`.
    assert "event: stage" in body
    assert "event: done" in body
    assert '"stage": "retrieve"' in body  # a named stage the UI renders
    assert body.count("event: done") == 1
    # `done` is last — the answer arrives after the narration.
    assert body.rindex("event: done") > body.rindex("event: stage")


def test_ask_disabled_without_answer_service(retrieval_service) -> None:
    # /search-only app (no answer path wired) returns 501 for /ask.
    client = TestClient(create_app(retrieval_service))
    resp = client.post("/ask", json={"question": "x"})
    assert resp.status_code == 501
