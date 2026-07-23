"""The OpenAI gateway client (§5.6). A fake OpenAI client stands in for the SDK so
these run offline with no key and no network."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from backend.config import Settings
from backend.domain.models import TaskType
from backend.failures import TransientGatewayError
from backend.llm.base import GatewayRequest, ModelName
from backend.llm.openai_client import OpenAIGatewayClient


@dataclass
class _Msg:
    content: str | None


@dataclass
class _Choice:
    message: _Msg


@dataclass
class _Usage:
    prompt_tokens: int
    completion_tokens: int


@dataclass
class _Response:
    choices: list[_Choice]
    usage: _Usage


class _FakeCompletions:
    def __init__(self, response: Any = None, error: Exception | None = None) -> None:
        self._response = response
        self._error = error
        self.last_kwargs: dict[str, Any] | None = None

    def create(self, **kwargs: Any) -> Any:
        self.last_kwargs = kwargs
        if self._error is not None:
            raise self._error
        return self._response


class _FakeClient:
    def __init__(self, response: Any = None, error: Exception | None = None) -> None:
        self.chat = type("Chat", (), {"completions": _FakeCompletions(response, error)})()


def _resp(text: str = "ok") -> _Response:
    return _Response(choices=[_Choice(_Msg(text))], usage=_Usage(11, 22))


def _req(**over: Any) -> GatewayRequest:
    base: dict[str, Any] = dict(
        model=ModelName.SONNET, prompt="hello", json_mode=False, max_tokens=256,
        temperature=0.0, task_type=TaskType.SYNTHESIS, prompt_version="v1",
    )
    base.update(over)
    return GatewayRequest(**base)


def test_maps_model_sends_temperature_and_returns_tokens() -> None:
    fake = _FakeClient(_resp("the answer"))
    gw = OpenAIGatewayClient(client=fake)

    result = gw.complete(_req(model=ModelName.HAIKU))

    assert result.text == "the answer"
    assert result.input_tokens == 11 and result.output_tokens == 22
    kw = fake.chat.completions.last_kwargs
    assert kw["model"] == "gpt-4o-mini"          # HAIKU tier → small model
    assert kw["temperature"] == 0.0              # OpenAI accepts it (unlike Claude 4.8/5)
    assert "response_format" not in kw           # not JSON mode


def test_json_mode_uses_guaranteed_json_object() -> None:
    fake = _FakeClient(_resp('{"purpose": "x"}'))
    gw = OpenAIGatewayClient(client=fake)

    result = gw.complete(_req(json_mode=True, system="You summarise."))

    assert result.text == '{"purpose": "x"}'
    kw = fake.chat.completions.last_kwargs
    assert kw["response_format"] == {"type": "json_object"}
    # json_object mode requires "json" to appear in the messages.
    assert "json" in kw["messages"][0]["content"].lower()


def test_model_overrides() -> None:
    fake = _FakeClient(_resp())
    gw = OpenAIGatewayClient(client=fake, large_model="gpt-4.1")
    gw.complete(_req(model=ModelName.SONNET))
    assert fake.chat.completions.last_kwargs["model"] == "gpt-4.1"


def test_transient_error_mapping() -> None:
    import httpx
    import openai

    req = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    resp = httpx.Response(status_code=503, request=req)
    fake = _FakeClient(error=openai.APIStatusError("overloaded", response=resp, body=None))
    gw = OpenAIGatewayClient(client=fake)

    with pytest.raises(TransientGatewayError):
        gw.complete(_req())


def test_factory_selects_openai(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.llm.factory import build_gateway

    # The SDK raises at construction with no credential — so a missing key fails fast
    # at boot rather than at first call.
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    client = build_gateway(Settings(gateway="openai", openai_small_model="m"))
    assert isinstance(client, OpenAIGatewayClient)


def test_base_url_points_at_enterprise_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    """Same client, different endpoint — swapping OpenAI ⇄ company gateway is
    just a base_url + key change."""
    from backend.llm.factory import build_gateway

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    gw = build_gateway(
        Settings(gateway="openai", openai_base_url="https://ai-gateway.corp.example/v1")
    )
    assert isinstance(gw, OpenAIGatewayClient)
    # The SDK normalises/keeps the override; assert it isn't the public endpoint.
    assert "api.openai.com" not in str(gw._client.base_url)
    assert "ai-gateway.corp.example" in str(gw._client.base_url)
