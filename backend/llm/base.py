"""Routing-layer data contracts and the gateway-client seam."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel

from backend.domain.models import Persona, TaskType


class ModelName(StrEnum):
    """Logical models on the company gateway (§2). Concrete model ids are a
    config detail of the real gateway adapter; routing decisions use these names
    so the quota pool stays shared (§4.3: one counter, not one per model)."""

    HAIKU = "haiku"
    SONNET = "sonnet"


class GatewayRequest(BaseModel):
    """A fully-resolved request to a single model. Built by the router from the
    task type + caller-supplied prompt; the gateway client only transports it."""

    model: ModelName
    prompt: str
    system: str | None = None
    json_mode: bool = True
    temperature: float = 0.0
    max_tokens: int = 1024
    # Carried for the client/telemetry; the router set these, not the caller.
    task_type: TaskType
    prompt_version: str
    # Per-request concrete-model override (the model dropdown). When set, the
    # client uses this id in place of its ``model``-tier default. The two flags
    # carry the chosen model's API convention: newer OpenAI models take
    # ``max_completion_tokens`` instead of ``max_tokens`` and reject a custom
    # ``temperature``. The router resolves these from the model catalog; the
    # tiered default path leaves them unset and behaves exactly as before.
    model_override: str | None = None
    override_uses_max_completion_tokens: bool = False
    override_supports_temperature: bool = True
    override_reasoning_effort: str | None = None


class GatewayResult(BaseModel):
    """What a gateway client returns: text plus the authoritative token counts.
    Token counts come from the gateway, never estimated by us."""

    text: str
    input_tokens: int
    output_tokens: int


class UsageRecord(BaseModel):
    """One accounting row (§5.1 token accounting). Emitted synchronously to the
    quota ledger and telemetry — the quota governor depends on it being current."""

    task_type: TaskType
    persona: Persona | None
    model: ModelName
    prompt_version: str
    input_tokens: int
    output_tokens: int
    at: datetime

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class RoutedResponse(BaseModel):
    """The router's return value: raw text, parsed JSON (when in JSON mode), and
    the usage that was recorded."""

    text: str
    parsed: dict[str, Any] | None
    model: ModelName
    task_type: TaskType
    prompt_version: str
    persona: Persona | None
    usage: UsageRecord


class GatewayClient(Protocol):
    """Transport to the gateway. The real adapter (httpx → company AI gateway) and
    :class:`~backend.llm.fake.FakeGatewayClient` both satisfy this. Only the
    router is allowed to hold one."""

    def complete(self, request: GatewayRequest) -> GatewayResult: ...
