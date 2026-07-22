"""Gateway selection + DI loader (§2). The loader only *imports* a user-supplied
GatewayClient — it performs no network I/O. Here we point it at the shipped
FakeGatewayClient (a real GatewayClient) to prove the dotted-path loader works."""

from __future__ import annotations

import pytest

from backend.config import Settings
from backend.llm.factory import build_gateway
from backend.llm.fake import FakeGatewayClient


def test_default_is_fake() -> None:
    assert isinstance(build_gateway(None), FakeGatewayClient)
    assert isinstance(build_gateway(Settings(gateway="fake")), FakeGatewayClient)


def test_real_loads_dotted_path() -> None:
    # A dotted path to any GatewayClient impl is loaded + instantiated.
    settings = Settings(gateway="real", gateway_client="backend.llm.fake:FakeGatewayClient")
    client = build_gateway(settings)
    assert isinstance(client, FakeGatewayClient)


def test_real_without_client_spec_raises() -> None:
    with pytest.raises(RuntimeError, match="VECTURAL_GATEWAY_CLIENT"):
        build_gateway(Settings(gateway="real"))

    with pytest.raises(RuntimeError, match="VECTURAL_GATEWAY_CLIENT"):
        build_gateway(Settings(gateway="real", gateway_client="no-colon-here"))
