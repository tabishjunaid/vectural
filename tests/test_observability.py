"""Metrics collector + /metrics endpoint (§7.1)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from backend.answer import AnswerService, RetrievalPlanner, SemanticAnswerCache
from backend.answer.models import AnswerMode
from backend.api import create_app
from backend.domain.models import Persona, TaskType
from backend.llm import FakeGatewayClient, LLMRouter
from backend.observability import MetricsCollector
from tests.conftest import AnswerEnv


def test_collector_records_token_spend_by_dimension() -> None:
    collector = MetricsCollector()
    router = LLMRouter(FakeGatewayClient(), sinks=[collector])
    router.route(TaskType.FILE_SUMMARY, "v", {"prompt": "a b c"}, Persona.ENGINEER)
    router.route(TaskType.SYNTHESIS, "v", {"prompt": "x y"}, Persona.ARCHITECT)

    snap = collector.snapshot()
    assert snap.total_calls == 2
    assert snap.tokens_by_task["file_summary"] > 0
    assert snap.tokens_by_task["synthesis"] > 0
    assert snap.tokens_by_persona["engineer"] > 0
    assert set(snap.tokens_by_model) == {"haiku", "sonnet"}
    assert snap.total_input_tokens > 0


def test_collector_answer_rates_and_latency() -> None:
    collector = MetricsCollector()
    collector.record_answer(AnswerMode.SYNTHESIZED)
    collector.record_answer(AnswerMode.REFUSAL)
    collector.record_answer(AnswerMode.INSTANT)
    collector.record_answer(AnswerMode.INSTANT)
    for ms in (10.0, 20.0, 30.0, 40.0):
        collector.record_latency_ms(ms)

    snap = collector.snapshot()
    assert snap.answers_total == 4
    assert snap.refusal_rate == 0.25
    assert snap.cache_hit_rate == 0.5
    assert snap.latency_p50_ms == 25.0  # median of 10,20,30,40
    assert snap.latency_p95_ms >= 38.0


def test_empty_collector_snapshot_is_zeroed() -> None:
    snap = MetricsCollector().snapshot()
    assert snap.total_calls == 0
    assert snap.refusal_rate == 0.0
    assert snap.latency_p95_ms == 0.0


def test_answer_service_feeds_metrics(answer_env: AnswerEnv) -> None:
    collector = MetricsCollector()
    router = LLMRouter(FakeGatewayClient(), sinks=[collector])
    planner = RetrievalPlanner(answer_env.structural, router, answer_env.services)
    service = AnswerService(
        retrieval=answer_env.retrieval, planner=planner, router=router,
        cache=SemanticAnswerCache(answer_env.embedder), metrics=collector,
        commit_sha=answer_env.commit_sha,
    )
    service.answer("how does gateway charge via payments", Persona.ENGINEER)
    snap = collector.snapshot()
    assert snap.answers_total == 1
    assert snap.total_calls > 0  # token spend captured via the same collector sink


def test_metrics_endpoint(answer_env: AnswerEnv) -> None:
    collector = MetricsCollector()
    router = LLMRouter(FakeGatewayClient(), sinks=[collector])
    planner = RetrievalPlanner(answer_env.structural, router, answer_env.services)
    service = AnswerService(
        retrieval=answer_env.retrieval, planner=planner, router=router, metrics=collector,
        commit_sha=answer_env.commit_sha,
    )
    client = TestClient(create_app(answer_env.retrieval, answer_service=service, metrics=collector))
    service.answer("how does gateway charge", Persona.ENGINEER)

    resp = client.get("/metrics")
    assert resp.status_code == 200
    body = resp.json()
    assert body["answers_total"] == 1
    assert "tokens_by_task" in body


def test_metrics_endpoint_501_when_disabled(retrieval_service) -> None:
    client = TestClient(create_app(retrieval_service))
    assert client.get("/metrics").status_code == 501
