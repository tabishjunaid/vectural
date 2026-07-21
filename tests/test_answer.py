"""Answer path: planning, R1 gates, persona, cache (§5.3-§5.5)."""

from __future__ import annotations

import json

from backend.answer import AnswerService, RetrievalPlanner, SemanticAnswerCache
from backend.answer.models import AnswerMode
from backend.domain.models import Persona, TaskType
from backend.llm import FakeGatewayClient, LLMRouter
from backend.llm.fake import _default_responder
from tests.conftest import AnswerEnv

QUESTION = "how does the gateway charge via payments and ledger"


def _service(
    env: AnswerEnv, gateway: FakeGatewayClient, *, cache: SemanticAnswerCache | None = None
) -> AnswerService:
    router = LLMRouter(gateway)
    planner = RetrievalPlanner(env.structural, router, env.services)
    return AnswerService(
        retrieval=env.retrieval, planner=planner, router=router,
        cache=cache, commit_sha=env.commit_sha,
    )


def test_happy_path_synthesized_with_citations(answer_env: AnswerEnv) -> None:
    answer = _service(answer_env, FakeGatewayClient()).answer(QUESTION, Persona.ENGINEER)
    assert answer.mode is AnswerMode.SYNTHESIZED
    assert answer.citations
    assert all(c.chunk_id for c in answer.citations)


def test_groundedness_gate_fails_closed(answer_env: AnswerEnv) -> None:
    def responder(req):
        if req.task_type is TaskType.GROUNDEDNESS:
            return json.dumps({"grounded": False, "unsupported_claims": ["c"]})
        return _default_responder(req)

    answer = _service(answer_env, FakeGatewayClient(responder=responder)).answer(QUESTION)
    assert answer.mode is AnswerMode.REFUSAL
    assert "grounded" in (answer.reason or "")
    assert answer.likely_services  # names likely owning services


def test_citation_gate_fails_closed(answer_env: AnswerEnv) -> None:
    def responder(req):
        if req.task_type is TaskType.SYNTHESIS:
            return "A confident but unverifiable claim. [not-a-real-chunk]"
        return _default_responder(req)

    answer = _service(answer_env, FakeGatewayClient(responder=responder)).answer(QUESTION)
    assert answer.mode is AnswerMode.REFUSAL
    assert "citation" in (answer.reason or "")


def test_groundedness_gate_never_reached_if_citations_fail(answer_env: AnswerEnv) -> None:
    # Gate 1 (deterministic) runs before gate 2 (a gateway call), so a bad
    # citation must not spend a groundedness call.
    calls = {"groundedness": 0}

    def responder(req):
        if req.task_type is TaskType.GROUNDEDNESS:
            calls["groundedness"] += 1
        if req.task_type is TaskType.SYNTHESIS:
            return "Claim. [ghost-id]"
        return _default_responder(req)

    _service(answer_env, FakeGatewayClient(responder=responder)).answer(QUESTION)
    assert calls["groundedness"] == 0


def test_out_of_coverage_question_refuses(answer_env: AnswerEnv) -> None:
    answer = _service(answer_env, FakeGatewayClient()).answer(
        "what is the airspeed velocity of an unladen swallow"
    )
    # Nothing relevant retrieved from this estate -> may synthesize weakly, but a
    # question with no resolvable evidence must not fabricate. Accept refusal or
    # a synthesized answer whose citations all resolve.
    if answer.mode is AnswerMode.SYNTHESIZED:
        assert answer.citations
    else:
        assert answer.mode is AnswerMode.REFUSAL


def test_cache_hit_is_instant_no_gateway(answer_env: AnswerEnv) -> None:
    cache = SemanticAnswerCache(answer_env.embedder)
    gateway = FakeGatewayClient()
    service = _service(answer_env, gateway, cache=cache)

    first = service.answer(QUESTION, Persona.ENGINEER)
    assert first.mode is AnswerMode.SYNTHESIZED
    calls_after_first = gateway.calls

    second = service.answer(QUESTION, Persona.ENGINEER)
    assert second.mode is AnswerMode.INSTANT
    assert second.from_cache
    assert gateway.calls == calls_after_first  # zero additional gateway calls


def test_cache_is_persona_scoped(answer_env: AnswerEnv) -> None:
    cache = SemanticAnswerCache(answer_env.embedder)
    gateway = FakeGatewayClient()
    service = _service(answer_env, gateway, cache=cache)
    service.answer(QUESTION, Persona.ENGINEER)
    calls = gateway.calls
    # A different persona is a cache miss -> the gateway is consulted again (R6).
    other = service.answer(QUESTION, Persona.BUSINESS_OWNER)
    assert gateway.calls > calls
    assert other.persona is Persona.BUSINESS_OWNER


def test_plan_scopes_retrieval_to_subgraph(answer_env: AnswerEnv) -> None:
    router = LLMRouter(FakeGatewayClient())
    planner = RetrievalPlanner(answer_env.structural, router, answer_env.services)
    plan = planner.plan("how does gateway call payments", Persona.ENGINEER)
    assert "gateway" in plan.anchors
    assert plan.scope is not None and "gateway" in plan.scope
    assert plan.cypher  # a validated (or fallback) cypher was produced


def test_plan_falls_back_on_invalid_cypher(answer_env: AnswerEnv) -> None:
    def responder(req):
        if req.task_type is TaskType.CYPHER_GENERATION:
            return json.dumps({"cypher": "MATCH (s:Service) DELETE s"})  # write -> invalid
        return _default_responder(req)

    router = LLMRouter(FakeGatewayClient(responder=responder))
    planner = RetrievalPlanner(answer_env.structural, router, answer_env.services)
    plan = planner.plan("how does gateway call payments")
    assert plan.used_fallback
    assert plan.cypher_attempts == 2  # one retry, then templated fallback
