"""Select the LLM gateway client from configuration (§2 licence boundary).

``fake`` is the offline stand-in (default, no spend). ``real`` is the platform's
**single** outbound LLM egress — but that client is deliberately **not shipped in
this codebase**: per design §2, Opus never authors or operates the gateway. Instead
you supply your own :class:`~backend.llm.base.GatewayClient` implementation (holding
your endpoint/key) and point at it with ``VECTURAL_GATEWAY_CLIENT='pkg.module:Class'``.
This function only *loads* your class — it performs no network I/O and handles no
credentials.
"""

from __future__ import annotations

import importlib

from backend.config import Settings
from backend.llm.base import GatewayClient
from backend.llm.fake import FakeGatewayClient


def build_gateway(settings: Settings | None = None) -> GatewayClient:
    name = settings.gateway if settings is not None else "fake"
    if name != "real":
        return FakeGatewayClient()

    spec = settings.gateway_client if settings is not None else ""
    if ":" not in spec:
        raise RuntimeError(
            "VECTURAL_GATEWAY=real requires VECTURAL_GATEWAY_CLIENT='pkg.module:ClassName' — "
            "your own GatewayClient implementation (the platform's single LLM egress, §2). "
            "This codebase does not ship the real client."
        )
    module_path, _, attr = spec.partition(":")
    cls = getattr(importlib.import_module(module_path), attr)
    client: GatewayClient = cls()  # your class reads its own endpoint/key from env
    return client
