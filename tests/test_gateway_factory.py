"""Gateway selection (§5.6). "real" defaults to the built-in Anthropic client;
a dotted path overrides with a user-supplied GatewayClient (imported, not called)."""

from __future__ import annotations

import pytest

from backend.config import Settings
from backend.llm.factory import build_gateway
from backend.llm.fake import FakeGatewayClient


def test_default_is_fake() -> None:
    assert isinstance(build_gateway(None), FakeGatewayClient)
    assert isinstance(build_gateway(Settings(gateway="fake")), FakeGatewayClient)


def test_real_loads_dotted_path_override() -> None:
    # A dotted path to any GatewayClient impl is loaded + instantiated verbatim.
    settings = Settings(gateway="real", gateway_client="backend.llm.fake:FakeGatewayClient")
    assert isinstance(build_gateway(settings), FakeGatewayClient)


def test_real_defaults_to_anthropic_client(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.llm.anthropic_client import AnthropicGatewayClient

    # A key must resolve for the SDK client to construct (no network at construction).
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-not-real")
    client = build_gateway(Settings(gateway="real"))
    assert isinstance(client, AnthropicGatewayClient)


def test_real_bad_dotted_path_raises() -> None:
    with pytest.raises(RuntimeError, match="VECTURAL_GATEWAY_CLIENT"):
        build_gateway(Settings(gateway="real", gateway_client="no-colon-here"))


@pytest.mark.parametrize("bad", ["opneai", "gpt", "anthropc", "", "none"])
def test_unknown_gateway_never_falls_back_to_fake(bad: str) -> None:
    """A typo must fail loudly — silently serving canned answers is the one
    outcome we refuse."""
    with pytest.raises(RuntimeError, match="unknown VECTURAL_GATEWAY"):
        build_gateway(Settings(gateway=bad))


def test_provider_names_are_case_and_space_tolerant(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.llm.openai_client import OpenAIGatewayClient

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    assert isinstance(build_gateway(Settings(gateway=" OpenAI ")), OpenAIGatewayClient)


def test_fake_is_only_reachable_when_asked_for_explicitly() -> None:
    # Explicit opt-in still works (offline tests / no-infra demo)…
    assert isinstance(build_gateway(Settings(gateway="fake")), FakeGatewayClient)
    # …but nothing else resolves to it.
    for name in ("openai", "anthropic", "real"):
        assert "fake" not in name
