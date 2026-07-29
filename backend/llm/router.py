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
import logging
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol

from backend.domain.models import Persona, TaskType
from backend.failures import ContentFailure, RetryPolicy, TransientGatewayError
from backend.llm import catalog
from backend.llm.base import (
    GatewayClient,
    GatewayRequest,
    GatewayResult,
    ModelName,
    RoutedResponse,
    UsageRecord,
)
from backend.llm.catalog import SelectableModel
from backend.llm.config import max_tokens_for, model_for, task_temperature, task_uses_json


def _llm_audit_logger() -> logging.Logger:
    """A dedicated stdout logger for full LLM request/response dumps.

    Its own handler + ``propagate=False`` so it always reaches ``docker compose
    logs`` regardless of uvicorn's root log configuration, and never doubles up if
    several routers are constructed."""
    audit = logging.getLogger("backend.llm.audit")
    if not audit.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(asctime)s [LLM] %(message)s"))
        audit.addHandler(handler)
        audit.setLevel(logging.INFO)
        audit.propagate = False
    return audit


class TokenSink(Protocol):
    """Receives every :class:`UsageRecord`. The quota ledger and the telemetry
    exporter are both sinks (§7.1) — accounting fans out to all of them."""

    def record(self, usage: UsageRecord) -> None: ...


class LLMRouter:
    def __init__(
        self,
        gateway: GatewayClient,
        *,
        clients: dict[str, GatewayClient] | None = None,
        sinks: list[TokenSink] | None = None,
        retry_policy: RetryPolicy | None = None,
        default_max_tokens: int = 1024,
        log_llm: bool = False,
        clock: Callable[[], datetime] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._gateway = gateway
        # provider → client, for per-request model overrides (the model dropdown).
        # Empty by default: the tiered path only ever uses the primary ``gateway``.
        self._clients = dict(clients or {})
        self._sinks = list(sinks or [])
        self._audit = _llm_audit_logger() if log_llm else None
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
        and may contain ``system``, ``max_tokens``, and ``model_override_id`` (a
        model-catalog id — routes this one call to that concrete model/provider)."""
        entry = catalog.find(payload.get("model_override_id"))
        request = self._build_request(task_type, prompt_version, payload, entry)
        self._audit_request(request, entry)
        result = self._call_with_retry(request, self._client_for(entry))
        self._audit_response(request, result)

        usage = UsageRecord(
            task_type=task_type,
            persona=persona,
            model=request.model,
            model_id=result.model,  # the concrete model the client actually called
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
        self,
        task_type: TaskType,
        prompt_version: str,
        payload: dict[str, Any],
        entry: SelectableModel | None = None,
    ) -> GatewayRequest:
        prompt = payload.get("prompt")
        if not isinstance(prompt, str) or not prompt:
            raise ValueError("payload must contain a non-empty 'prompt' string")
        # Precedence: explicit per-request override (depth) > per-task ceiling
        # (llm/config.py) > the router's global default. Before per-task ceilings
        # existed, every task shared one number and synthesis was capped at the
        # same size as a one-line verdict.
        max_tokens = int(payload.get("max_tokens") or max_tokens_for(task_type))
        if entry is not None:
            # A chosen model: clamp to its ceiling and carry its API convention.
            # Reasoning models spend hidden reasoning tokens from this same budget,
            # so floor it well above the depth budget or the visible answer starves.
            if entry.uses_max_completion_tokens:
                max_tokens = max(max_tokens, catalog.REASONING_OUTPUT_FLOOR)
            max_tokens = min(max_tokens, entry.max_output)
        return GatewayRequest(
            model=model_for(task_type),
            prompt=prompt,
            system=payload.get("system"),
            json_mode=task_uses_json(task_type),
            temperature=task_temperature(task_type),
            max_tokens=max_tokens,
            task_type=task_type,
            prompt_version=prompt_version,
            model_override=entry.concrete if entry is not None else None,
            override_uses_max_completion_tokens=(
                entry.uses_max_completion_tokens if entry is not None else False
            ),
            override_supports_temperature=(
                entry.supports_temperature if entry is not None else True
            ),
            override_reasoning_effort=entry.reasoning_effort if entry is not None else None,
        )

    def _client_for(self, entry: SelectableModel | None) -> GatewayClient:
        """The gateway that answers this call: the chosen model's provider client
        if one is wired, else the primary gateway (the default tiered path)."""
        if entry is not None:
            return self._clients.get(entry.provider, self._gateway)
        return self._gateway

    def _call_with_retry(
        self, request: GatewayRequest, client: GatewayClient | None = None
    ) -> GatewayResult:
        target = client or self._gateway
        attempt = 0
        while True:
            attempt += 1
            try:
                return target.complete(request)
            except TransientGatewayError:
                if not self._retry.should_retry(attempt):
                    raise
                self._sleep(self._retry.delay_for(attempt + 1))

    def _emit(self, usage: UsageRecord) -> None:
        for sink in self._sinks:
            sink.record(usage)

    def _audit_request(self, request: GatewayRequest, entry: SelectableModel | None) -> None:
        if self._audit is None:
            return
        concrete = request.model_override or request.model.value
        effort = request.override_reasoning_effort or "—"
        self._audit.info(
            "REQUEST task=%s model=%s max_tokens=%s temperature=%s reasoning_effort=%s\n"
            "----- system -----\n%s\n----- prompt -----\n%s",
            request.task_type.value,
            concrete,
            request.max_tokens,
            request.temperature,
            effort,
            request.system or "(none)",
            request.prompt,
        )

    def _audit_response(self, request: GatewayRequest, result: GatewayResult) -> None:
        if self._audit is None:
            return
        self._audit.info(
            "RESPONSE task=%s input_tokens=%d output_tokens=%d\n----- text -----\n%s\n=== end ===",
            request.task_type.value,
            result.input_tokens,
            result.output_tokens,
            result.text,
        )

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
