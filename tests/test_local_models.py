"""Local (Ollama) model provider: discovery, catalog registration, $0 pricing,
and the whole-pipeline model override (a pick routes ALL query calls, not just
synthesis — so a local pick is truly zero-cost and leaks nothing to OpenAI)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from backend.answer.groundedness import check_groundedness
from backend.answer.plan import RetrievalPlanner
from backend.domain.models import ChunkKind, Language, Persona, Span, TaskType
from backend.llm import catalog
from backend.llm.base import ModelName, UsageRecord
from backend.llm.catalog import SelectableModel
from backend.llm.factory import _ollama_available
from backend.llm.ollama_discovery import discover_ollama_models
from backend.llm.router import LLMRouter
from backend.retrieval.base import SearchHit
from tests.conftest import AnswerEnv


def _hit(chunk_id: str) -> SearchHit:
    return SearchHit(
        chunk_id=chunk_id, service="svc", path="svc/a.py", span=Span(start=1, end=2),
        language=Language.PYTHON, kind=ChunkKind.MODULE, symbol=None,
        content="def a(): ...", commit_sha="abc", score=1.0,
    )


# ---- discovery ------------------------------------------------------------


def test_discover_parses_v1_models(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeResp:
        def raise_for_status(self) -> None: ...
        def json(self) -> dict:
            return {"data": [{"id": "qwen2.5-coder:7b"}, {"id": "llama3.1:8b"}]}

    import httpx

    monkeypatch.setattr(httpx, "get", lambda url, timeout=None: FakeResp())
    models = discover_ollama_models("http://host:11434/v1")
    assert {m.id for m in models} == {"qwen2.5-coder:7b", "llama3.1:8b"}
    assert all(m.provider == "ollama" and m.supports_temperature for m in models)
    assert all(not m.uses_max_completion_tokens for m in models)


def test_discover_unreachable_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    def boom(url: str, timeout: float | None = None) -> object:
        raise httpx.ConnectError("nope")

    monkeypatch.setattr(httpx, "get", boom)
    assert discover_ollama_models("http://host:11434/v1") == []
    assert discover_ollama_models("") == []  # unconfigured → no probe, empty


# ---- catalog registration + $0 pricing -----------------------------------


def test_register_dynamic_models_and_zero_price() -> None:
    m = SelectableModel("qwen2.5-coder:7b", "qwen2.5-coder:7b", "ollama", "qwen2.5-coder:7b", 8192)
    catalog.register_dynamic_models([m])
    assert catalog.find("qwen2.5-coder:7b") is m
    assert "qwen2.5-coder:7b" in {x.id for x in catalog.available_models({"ollama"})}
    # Local models are free — report $0 (not "unknown"), so the compare cost tile
    # shows $0.00 next to OpenAI's dollars.
    assert catalog.price_of("qwen2.5-coder:7b") == (0.0, 0.0)
    assert m.hint == "Ollama · local · free"


def test_estimate_cost_is_zero_for_local() -> None:
    from backend.answer.service import _estimate_cost

    catalog.register_dynamic_models(
        [SelectableModel("q7b", "q7b", "ollama", "q7b", 8192)]
    )
    u = UsageRecord(
        task_type=TaskType.SYNTHESIS, persona=None, model=ModelName.SONNET, model_id="q7b",
        prompt_version="v", input_tokens=5000, output_tokens=5000, at=datetime.now(UTC),
    )
    assert _estimate_cost([u]) == 0.0


# ---- whole-pipeline override threading ------------------------------------


class _RecordingRouter(LLMRouter):
    """Wraps a real router, recording the (task, payload) of every call so a test
    can assert the model override reached each stage."""

    def __init__(self, gateway) -> None:  # type: ignore[no-untyped-def]
        super().__init__(gateway)
        self.calls: list[tuple[TaskType, dict]] = []

    def route(self, task_type, prompt_version, payload, persona=None):  # type: ignore[no-untyped-def]
        self.calls.append((task_type, dict(payload)))
        return super().route(task_type, prompt_version, payload, persona)


def test_override_reaches_planner_calls(answer_env: AnswerEnv) -> None:
    from backend.llm import FakeGatewayClient

    router = _RecordingRouter(FakeGatewayClient())
    planner = RetrievalPlanner(answer_env.structural, router, answer_env.services)
    planner.plan("how does gateway call payments", Persona.ENGINEER, model_override_id="q7b")

    entity = [p for t, p in router.calls if t is TaskType.ENTITY_LINKING]
    cypher = [p for t, p in router.calls if t is TaskType.CYPHER_GENERATION]
    assert entity and entity[0]["model_override_id"] == "q7b"
    assert cypher and all(p["model_override_id"] == "q7b" for p in cypher)


def test_override_reaches_groundedness(answer_env: AnswerEnv) -> None:
    from backend.llm import FakeGatewayClient

    router = _RecordingRouter(FakeGatewayClient())
    check_groundedness(
        router, answer_text="A claim. [svc:a.py:1-2:aaa]", chunks=[_hit("svc:a.py:1-2:aaa")],
        model_override_id="q7b",
    )
    ground = [p for t, p in router.calls if t is TaskType.GROUNDEDNESS]
    assert ground and ground[0]["model_override_id"] == "q7b"


# ---- factory registration -------------------------------------------------


def test_ollama_available_flag() -> None:
    from backend.config import Settings

    assert _ollama_available(Settings(ollama_base_url="http://host:11434/v1"))
    assert not _ollama_available(Settings(ollama_base_url=""))


def test_build_gateways_registers_ollama_alongside_openai(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("openai")
    from backend.config import Settings
    from backend.llm.factory import build_gateways

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    provider, clients = build_gateways(
        Settings(gateway="openai", ollama_base_url="http://host:11434/v1")
    )
    assert provider == "openai"
    assert "openai" in clients and "ollama" in clients  # both selectable in the dropdown
