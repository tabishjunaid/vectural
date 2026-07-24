"""OpenAI-backed LLM gateway — an alternative single egress (§5.6).

Implements :class:`GatewayClient` against the OpenAI Chat Completions API using the
official ``openai`` SDK (optional ``gateway`` extra). Enable with
``VECTURAL_GATEWAY=openai``.

Credentials are resolved **by the SDK from the environment** (``OPENAI_API_KEY``) —
this module never holds or handles a key. The router's logical tiers map to two
concrete OpenAI models (small = high-volume tiers 1-2 + groundedness, large = tier 3
+ synthesis + planning), overridable via settings.

Unlike the Anthropic path, OpenAI accepts ``temperature`` and offers a guaranteed
JSON mode (``response_format={"type": "json_object"}``), so the design's near-zero
temperature and structured-output requirements are honoured directly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from backend.failures import (
    CONTENT_STATUS_CODES,
    ContentFailure,
    TransientGatewayError,
    content_failure_kind,
)
from backend.llm.base import GatewayRequest, GatewayResult, ModelName

if TYPE_CHECKING:
    from openai import OpenAI

# Defaults chosen for the design's cost tiering; override per your account's access.
_DEFAULT_MODELS: dict[ModelName, str] = {
    ModelName.HAIKU: "gpt-4o-mini",  # cheap + fast: tiers 1-2, groundedness
    ModelName.SONNET: "gpt-4o",  # stronger: tier 3, synthesis, planning
}

_JSON_SUFFIX = "Respond with a single valid JSON object."


class OpenAIGatewayClient:
    """GatewayClient backed by the OpenAI Chat Completions API."""

    def __init__(
        self,
        *,
        small_model: str | None = None,
        large_model: str | None = None,
        base_url: str | None = None,
        client: Any | None = None,
    ) -> None:
        self._models: dict[ModelName, str] = {
            ModelName.HAIKU: small_model or _DEFAULT_MODELS[ModelName.HAIKU],
            ModelName.SONNET: large_model or _DEFAULT_MODELS[ModelName.SONNET],
        }
        # Lazy import so the base install never needs the SDK. The SDK reads
        # OPENAI_API_KEY (and OPENAI_BASE_URL) from the environment — we pass no key.
        # An explicit base_url points the same client at an OpenAI-compatible
        # enterprise gateway instead of api.openai.com.
        self._client: OpenAI
        if client is not None:
            self._client = client
        else:
            import openai

            self._client = openai.OpenAI(base_url=base_url) if base_url else openai.OpenAI()

    def complete(self, request: GatewayRequest) -> GatewayResult:
        import openai

        system = request.system
        if request.json_mode:
            # json_object mode requires the word "json" to appear in the messages.
            system = f"{system}\n\n{_JSON_SUFFIX}" if system else _JSON_SUFFIX

        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": request.prompt})

        kwargs: dict[str, Any] = {
            "model": self._models[request.model],
            "messages": messages,
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
        }
        if request.json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        try:
            response = self._client.chat.completions.create(**kwargs)
        except openai.APIStatusError as exc:  # 4xx/5xx with a response
            status = getattr(exc, "status_code", 0) or 0
            if status == 429 or status >= 500:
                raise TransientGatewayError(str(exc), status_code=status) from exc
            if status in CONTENT_STATUS_CODES:
                # Per-item content failure (oversized prompt, unprocessable body) —
                # dead-lettered so one bad file cannot abort a whole service batch (§5.8).
                raise ContentFailure(
                    str(exc), kind=content_failure_kind(str(exc)), detail=f"status={status}"
                ) from exc
            raise
        except openai.APIConnectionError as exc:  # network failure before a response
            raise TransientGatewayError(str(exc)) from exc

        text = response.choices[0].message.content or ""
        usage = response.usage
        return GatewayResult(
            text=text,
            input_tokens=getattr(usage, "prompt_tokens", 0) if usage else 0,
            output_tokens=getattr(usage, "completion_tokens", 0) if usage else 0,
        )
