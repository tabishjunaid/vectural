"""Select the LLM gateway client from configuration (§5.6, §2 licence boundary).

The platform has exactly one model egress. Choose it with ``VECTURAL_GATEWAY``:

- ``openai``    — OpenAI, or any OpenAI-compatible gateway (``VECTURAL_OPENAI_BASE_URL``)
- ``anthropic`` — Claude, or a company AI Gateway fronting it (``VECTURAL_ANTHROPIC_BASE_URL``)
- ``fake``      — the offline stand-in: **canned text, no model is called**. For tests
  and the no-infra demo only.

Or set ``VECTURAL_GATEWAY_CLIENT='pkg.module:Class'`` to inject your own
:class:`~backend.llm.base.GatewayClient`; the loader only *imports* it (no network
I/O, no credential handling here). Every SDK reads its key from the environment.

**The fake gateway is never a fallback.** An unrecognised ``VECTURAL_GATEWAY`` raises
rather than silently degrading to canned answers, and selecting ``fake`` logs a
warning — a misconfigured deployment fails loudly instead of quietly faking.
"""

from __future__ import annotations

import importlib
import logging

from backend.config import Settings
from backend.llm.base import GatewayClient
from backend.llm.fake import FakeGatewayClient

_log = logging.getLogger(__name__)

_ANTHROPIC = {"real", "anthropic"}  # "real" kept as a back-compat alias
_VALID = {"fake", "openai"} | _ANTHROPIC


def build_gateway(settings: Settings | None = None) -> GatewayClient:
    name = (settings.gateway if settings is not None else "fake").strip().lower()
    if name not in _VALID:
        raise RuntimeError(
            f"unknown VECTURAL_GATEWAY={name!r} — expected one of {sorted(_VALID)}. "
            "Refusing to fall back to the fake gateway (that would silently serve "
            "canned answers)."
        )

    if name == "fake":
        _log.warning(
            "LLM gateway = FAKE — responses are canned templates and NO model is "
            "called. Set VECTURAL_GATEWAY=openai|anthropic for real answers."
        )
        return FakeGatewayClient()

    assert settings is not None  # only "fake" is reachable without settings
    spec = settings.gateway_client
    if spec:
        # A user-supplied client (dotted path) — we only import + instantiate it.
        if ":" not in spec:
            raise RuntimeError(
                "VECTURAL_GATEWAY_CLIENT must be 'pkg.module:ClassName' (dotted path to "
                "your GatewayClient implementation)."
            )
        module_path, _, attr = spec.partition(":")
        cls = getattr(importlib.import_module(module_path), attr)
        client: GatewayClient = cls()  # your class reads its own endpoint/key from env
        _log.info("LLM gateway = %s (injected via VECTURAL_GATEWAY_CLIENT)", type(client).__name__)
        return client

    if name == "openai":
        from backend.llm.openai_client import OpenAIGatewayClient

        openai_client = OpenAIGatewayClient(
            small_model=settings.openai_small_model or None,
            large_model=settings.openai_large_model or None,
            base_url=settings.openai_base_url or None,
        )
        _log.info("LLM gateway = openai (%s)", settings.openai_base_url or "api.openai.com")
        return openai_client

    from backend.llm.anthropic_client import AnthropicGatewayClient

    anthropic_client = AnthropicGatewayClient(
        haiku_model=settings.anthropic_haiku_model or None,
        sonnet_model=settings.anthropic_sonnet_model or None,
        base_url=settings.anthropic_base_url or None,
    )
    _log.info("LLM gateway = anthropic (%s)", settings.anthropic_base_url or "api.anthropic.com")
    return anthropic_client
