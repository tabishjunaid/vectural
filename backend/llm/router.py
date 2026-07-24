"""The LLM router — the single, only egress to a model (§5.6, §7.3).

Responsibilities, and nothing beyond them:
- **model selection** from ``task_type`` (config, §config.py)
- **prompt versioning** — every call carries and records a ``prompt_version``
- **token accounting** — a :class:`UsageRecord` is pushed to every sink
  *synchronously*, before the response is even parsed, because the quota governor
  depends on the counter being current (§5.1) and a malformed response still
  spent tokens (§7.2)
- **mode** — JSON + near-zero temperature except synthesis

Pacing (quota) is *not* here — the governor gates callers before they route.
Prompt *content* is not here — callers pass a rendered prompt (§2.1).
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol

from backend.domain.models import Persona, TaskType
from backend.failures import ContentFailure, RetryPolicy, TransientGatewayError
from backend.llm.base import (
    GatewayClient,
    GatewayRequest,
    GatewayResult,
    ModelName,
    RoutedResponse,
    UsageRecord,
)
from backend.llm.config import max_tokens_for, model_for, task_temperature, task_uses_json


class TokenSink(Protocol):
    """Receives every :class:`UsageRecord`. The quota ledger and the telemetry
    exporter are both sinks (§7.1) — accounting fans out to all of them."""

    def record(self, usage: UsageRecord) -> None: ...


class LLMRouter:
    def __init__(
        self,
        gateway: GatewayClient,
        *,
        sinks: list[TokenSink] | None = None,
        retry_policy: RetryPolicy | None = None,
        default_max_tokens: int = 1024,
        clock: Callable[[], datetime] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._gateway = gateway
        self._sinks = list(sinks or [])
        self._retry = retry_policy or RetryPolicy()
        self._default_max_tokens = default_max_tokens
        self._clock = clock or (lambda: datetime.now(UTC))
        self._sleep = sleep

    def add_sink(self, sink: TokenSink) -> None:
        self._sinks.append(sink)

    def route(
        self,
        task_type: TaskType,
        prompt_version: str,
        payload: dict[str, Any],
        persona: Persona | None = None,
    ) -> RoutedResponse:
        """Route one call. ``payload`` must contain ``prompt`` (a rendered string)
        and may contain ``system`` and ``max_tokens``."""
        request = self._build_request(task_type, prompt_version, payload)
        result = self._call_with_retry(request)

        usage = UsageRecord(
            task_type=task_type,
            persona=persona,
            model=request.model,
            prompt_version=prompt_version,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            at=self._clock(),
        )
        self._emit(usage)  # synchronous, before parsing (§5.1, §7.2)

        parsed = self._parse_if_json(request, result.text)
        return RoutedResponse(
            text=result.text,
            parsed=parsed,
            model=request.model,
            task_type=task_type,
            prompt_version=prompt_version,
            persona=persona,
            usage=usage,
        )

    # -- internals ---------------------------------------------------------- #

    def _build_request(
        self, task_type: TaskType, prompt_version: str, payload: dict[str, Any]
    ) -> GatewayRequest:
        prompt = payload.get("prompt")
        if not isinstance(prompt, str) or not prompt:
            raise ValueError("payload must contain a non-empty 'prompt' string")
        return GatewayRequest(
            model=model_for(task_type),
            prompt=prompt,
            system=payload.get("system"),
            json_mode=task_uses_json(task_type),
            temperature=task_temperature(task_type),
            # Precedence: explicit per-request override (depth) > per-task ceiling
            # (llm/config.py) > the router's global default. Before per-task
            # ceilings existed, every task shared one number and synthesis was
            # capped at the same size as a one-line verdict.
            max_tokens=int(payload.get("max_tokens") or max_tokens_for(task_type)),
            task_type=task_type,
            prompt_version=prompt_version,
        )

    def _call_with_retry(self, request: GatewayRequest) -> GatewayResult:
        attempt = 0
        while True:
            attempt += 1
            try:
                return self._gateway.complete(request)
            except TransientGatewayError:
                if not self._retry.should_retry(attempt):
                    raise
                self._sleep(self._retry.delay_for(attempt + 1))

    def _emit(self, usage: UsageRecord) -> None:
        for sink in self._sinks:
            sink.record(usage)

    def _parse_if_json(self, request: GatewayRequest, text: str) -> dict[str, Any] | None:
        if not request.json_mode:
            return None
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            # Malformed structured output is a content failure (§5.8) — the spend
            # was already accounted above.
            raise ContentFailure(
                f"model returned invalid JSON for {request.task_type}",
                kind="malformed_json",
                detail=str(exc),
            ) from exc
        if not isinstance(parsed, dict):
            raise ContentFailure(
                f"model returned non-object JSON for {request.task_type}",
                kind="malformed_json",
            )
        return parsed


def concrete_model_ids() -> dict[ModelName, str]:
    """Logical → concrete gateway model ids, for the real adapter to resolve.
    Kept here so the mapping is one edit when the gateway bumps model versions."""
    return {ModelName.HAIKU: "claude-haiku-4-5", ModelName.SONNET: "claude-sonnet-5"}
