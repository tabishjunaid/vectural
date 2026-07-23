"""The real Anthropic gateway client (§5.6). A fake Anthropic client stands in for
the SDK so these run offline with no key and no network."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from backend.domain.models import TaskType
from backend.failures import TransientGatewayError
from backend.llm.anthropic_client import AnthropicGatewayClient, _coerce_json
from backend.llm.base import GatewayRequest, ModelName


@dataclass
class _Text:
    text: str
    type: str = "text"


@dataclass
class _Usage:
    input_tokens: int
    output_tokens: int


@dataclass
class _Response:
    content: list[_Text]
    usage: _Usage


class _FakeMessages:
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
        self.messages = _FakeMessages(response, error)


def _req(**over: Any) -> GatewayRequest:
    base: dict[str, Any] = dict(
        model=ModelName.SONNET, prompt="hello", json_mode=False, max_tokens=256,
        task_type=TaskType.SYNTHESIS, prompt_version="v1",
    )
    base.update(over)
    return GatewayRequest(**base)


def test_maps_model_and_returns_text_and_tokens() -> None:
    fake = _FakeClient(_Response(content=[_Text("the answer")], usage=_Usage(12, 34)))
    gw = AnthropicGatewayClient(client=fake)

    result = gw.complete(_req(model=ModelName.HAIKU, prompt="Q?"))

    assert result.text == "the answer"
    assert result.input_tokens == 12
    assert result.output_tokens == 34
    assert fake.messages.last_kwargs is not None
    assert fake.messages.last_kwargs["model"] == "claude-haiku-4-5"  # HAIKU → concrete id
    assert "temperature" not in fake.messages.last_kwargs  # never sent (400 on 4.8/5)


def test_sonnet_and_model_override() -> None:
    fake = _FakeClient(_Response(content=[_Text("x")], usage=_Usage(1, 1)))
    gw = AnthropicGatewayClient(client=fake, sonnet_model="claude-opus-4-8")
    gw.complete(_req(model=ModelName.SONNET))
    assert fake.messages.last_kwargs["model"] == "claude-opus-4-8"


def test_json_mode_adds_instruction_and_strips_fences() -> None:
    fenced = "```json\n{\"purpose\": \"x\"}\n```"
    fake = _FakeClient(_Response(content=[_Text(fenced)], usage=_Usage(5, 5)))
    gw = AnthropicGatewayClient(client=fake)

    result = gw.complete(_req(json_mode=True, system="You summarise."))

    assert result.text == '{"purpose": "x"}'  # fence stripped → router json.loads ok
    assert "JSON object" in fake.messages.last_kwargs["system"]  # instruction appended


def test_transient_error_mapping() -> None:
    import anthropic
    import httpx

    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    resp = httpx.Response(status_code=529, request=req)
    fake = _FakeClient(error=anthropic.APIStatusError("overloaded", response=resp, body=None))
    gw = AnthropicGatewayClient(client=fake)

    with pytest.raises(TransientGatewayError):
        gw.complete(_req())


def test_coerce_json_bare_passthrough() -> None:
    assert _coerce_json('{"a": 1}') == '{"a": 1}'
    assert _coerce_json('```\n{"a": 1}\n```') == '{"a": 1}'


def test_base_url_points_at_company_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    """§2: the same client targets a company AI Gateway fronting Claude — its own
    key + URL. Swapping is config, not code."""
    from backend.config import Settings
    from backend.llm.factory import build_gateway

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-not-real")
    gw = build_gateway(
        Settings(
            gateway="anthropic",
            anthropic_base_url="https://ai-gateway.corp.example",
            anthropic_sonnet_model="corp-sonnet",
        )
    )
    assert isinstance(gw, AnthropicGatewayClient)
    assert "api.anthropic.com" not in str(gw._client.base_url)
    assert "ai-gateway.corp.example" in str(gw._client.base_url)
    # The gateway publishes its own model names — routing uses them verbatim.
    assert gw._models[ModelName.SONNET] == "corp-sonnet"
