"""Per-question model selection: catalog, override routing, param conventions."""

from __future__ import annotations

import pytest

from backend.domain.models import TaskType
from backend.llm import catalog
from backend.llm.base import GatewayRequest, GatewayResult, ModelName
from backend.llm.router import LLMRouter

# --- catalog --------------------------------------------------------------- #


def test_available_models_filters_to_wired_providers() -> None:
    """The dropdown must never offer a model whose gateway isn't wired."""
    openai_only = {m.id for m in catalog.available_models({"openai"})}
    assert "gpt-4o" in openai_only
    assert not any(m.startswith("claude") for m in openai_only)
    both = {m.provider for m in catalog.available_models({"openai", "anthropic"})}
    assert both == {"openai", "anthropic"}
    assert catalog.available_models(set()) == []


def test_find_resolves_ids() -> None:
    assert catalog.find("gpt-4o").concrete == "gpt-4o"  # type: ignore[union-attr]
    assert catalog.find("gpt-5").uses_max_completion_tokens is True  # type: ignore[union-attr]
    assert catalog.find(None) is None
    assert catalog.find("no-such-model") is None


# --- router: override routing --------------------------------------------- #


class _Recorder:
    """A GatewayClient that records the request it was given."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.seen: GatewayRequest | None = None

    def complete(self, request: GatewayRequest) -> GatewayResult:
        self.seen = request
        return GatewayResult(text="{}", input_tokens=1, output_tokens=1)


def _route(router: LLMRouter, model_id: str | None) -> None:
    payload: dict[str, object] = {"prompt": "p", "max_tokens": 6500}
    if model_id:
        payload["model_override_id"] = model_id
    router.route(TaskType.SYNTHESIS, "v", payload)


def test_no_override_uses_primary_gateway_unchanged() -> None:
    primary, other = _Recorder("primary"), _Recorder("other")
    router = LLMRouter(primary, clients={"openai": primary, "anthropic": other})
    _route(router, None)
    assert primary.seen is not None and other.seen is None
    assert primary.seen.model_override is None  # tiered default path
    assert primary.seen.max_tokens == 6500


def test_override_routes_to_the_models_provider_client() -> None:
    openai_client, anthropic_client = _Recorder("openai"), _Recorder("anthropic")
    router = LLMRouter(
        openai_client, clients={"openai": openai_client, "anthropic": anthropic_client}
    )
    _route(router, "claude-opus-4-8")  # an anthropic model
    assert anthropic_client.seen is not None
    assert anthropic_client.seen.model_override == "claude-opus-4-8"


def test_override_carries_convention_and_clamps_to_cap() -> None:
    primary = _Recorder("openai")
    router = LLMRouter(primary, clients={"openai": primary})
    # gpt-4o caps at 16384 and uses the classic convention.
    _route(router, "gpt-4o")
    assert primary.seen is not None
    assert primary.seen.model_override == "gpt-4o"
    assert primary.seen.override_uses_max_completion_tokens is False
    assert primary.seen.override_supports_temperature is True
    assert primary.seen.max_tokens == 6500  # below the cap, unchanged
    # gpt-5: newer convention, and a big depth budget is clamped to nothing here
    # (6500 < cap) but the flags flip.
    _route(router, "gpt-5")
    assert primary.seen is not None
    assert primary.seen.override_uses_max_completion_tokens is True
    assert primary.seen.override_supports_temperature is False


def test_reasoning_models_get_a_larger_completion_floor() -> None:
    """gpt-5 spends reasoning tokens from max_completion_tokens; a small depth
    budget would starve the visible answer and trip the citation gate, so the
    router floors the budget for reasoning models."""
    primary = _Recorder("openai")
    router = LLMRouter(primary, clients={"openai": primary})
    # STANDARD-sized budget (6500) is floored up for a reasoning model...
    _route(router, "gpt-5")
    assert primary.seen is not None
    assert primary.seen.max_tokens >= catalog.REASONING_OUTPUT_FLOOR
    # ...but a classic model keeps the exact requested budget.
    _route(router, "gpt-4o")
    assert primary.seen is not None
    assert primary.seen.max_tokens == 6500


def test_override_falls_back_to_primary_when_provider_not_wired() -> None:
    """Anthropic not wired: a Claude pick still routes somewhere (primary) rather
    than crashing — the /models filter is what normally prevents offering it."""
    primary = _Recorder("openai")
    router = LLMRouter(primary, clients={"openai": primary})
    _route(router, "claude-sonnet-5")
    assert primary.seen is not None
    assert primary.seen.model_override == "claude-sonnet-5"


# --- OpenAI client: the two API conventions -------------------------------- #


def _fake_openai_response():
    from types import SimpleNamespace

    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="hi"))],
        usage=SimpleNamespace(prompt_tokens=3, completion_tokens=4),
    )


def _req(**over: object) -> GatewayRequest:
    base: dict[str, object] = dict(
        model=ModelName.SONNET,
        prompt="p",
        json_mode=False,
        temperature=0.3,
        max_tokens=6500,
        task_type=TaskType.SYNTHESIS,
        prompt_version="v",
    )
    base.update(over)
    return GatewayRequest(**base)  # type: ignore[arg-type]


def test_openai_client_switches_param_convention_on_override() -> None:
    pytest.importorskip("openai")
    from types import SimpleNamespace

    from backend.llm.openai_client import OpenAIGatewayClient

    captured: dict[str, object] = {}

    def _create(**kwargs: object):
        captured.clear()
        captured.update(kwargs)
        return _fake_openai_response()

    fake = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=_create)))
    client = OpenAIGatewayClient(client=fake)

    # Classic model (no override): max_tokens + temperature, tiered concrete id,
    # and no reasoning_effort.
    client.complete(_req())
    assert captured["model"] == "gpt-4o"
    assert "max_tokens" in captured and "max_completion_tokens" not in captured
    assert "temperature" in captured
    assert "reasoning_effort" not in captured

    # gpt-5 override: max_completion_tokens, no temperature, reasoning_effort set.
    client.complete(
        _req(
            model_override="gpt-5",
            override_uses_max_completion_tokens=True,
            override_supports_temperature=False,
            override_reasoning_effort="low",
        )
    )
    assert captured["model"] == "gpt-5"
    assert "max_completion_tokens" in captured and "max_tokens" not in captured
    assert "temperature" not in captured
    assert captured["reasoning_effort"] == "low"


# --- cache keys on the chosen model --------------------------------------- #


def test_cache_keys_on_model() -> None:
    """Regression guard: switching the model must recompute, not replay the
    previous model's answer (same shape as the depth-key fix)."""
    from backend.answer.cache import SemanticAnswerCache
    from backend.answer.models import Answer
    from backend.domain.models import Depth, Persona
    from backend.embedding.hashing import HashingEmbedder

    cache = SemanticAnswerCache(HashingEmbedder())
    ans = Answer.synthesized(persona=Persona.ENGINEER, question="q", text="a [x]", citations=[])
    cache.put("q", "sha", Persona.ENGINEER, ans, Depth.DEEP, "gpt-4o")

    assert cache.get("q", "sha", Persona.ENGINEER, Depth.DEEP, "gpt-4o") is not None
    assert cache.get("q", "sha", Persona.ENGINEER, Depth.DEEP, "gpt-5") is None  # recompute
    assert cache.get("q", "sha", Persona.ENGINEER, Depth.DEEP, None) is None


# --- GET /models endpoint -------------------------------------------------- #


def test_models_endpoint_lists_only_wired_providers(retrieval_service) -> None:  # type: ignore[no-untyped-def]
    from fastapi.testclient import TestClient

    from backend.api import create_app

    client = TestClient(create_app(retrieval_service, model_providers={"openai"}))
    rows = client.get("/models").json()
    ids = {r["id"] for r in rows}
    assert "gpt-4o" in ids and "gpt-5" in ids
    assert not any(r["provider"] == "anthropic" for r in rows)
    assert all({"id", "label", "provider", "hint"} <= r.keys() for r in rows)


def test_ask_request_accepts_model_field() -> None:
    from backend.api.answer_schemas import AskRequest

    req = AskRequest(question="q", model="gpt-5")
    assert req.model == "gpt-5"
    assert AskRequest(question="q").model is None  # optional, back-compatible
