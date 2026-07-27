"""Real LLM gateway — the platform's single outbound Claude client (§5.6, §2).

Implements :class:`GatewayClient` against the Anthropic Messages API using the
official ``anthropic`` SDK (optional ``gateway`` extra). It is the platform's one
egress to a model; enable it with ``VECTURAL_GATEWAY=real``.

Credentials are resolved **by the SDK from the environment** (``ANTHROPIC_API_KEY``,
``ANTHROPIC_AUTH_TOKEN``, or an ``ant auth login`` profile) — this module never
holds or handles a key. Logical models (HAIKU / SONNET, §5.6 routing) map to
concrete Claude IDs, overridable via settings; the defaults mirror
``backend.llm.router.concrete_model_ids``.

Sampling parameters are intentionally not sent: current Claude models reject a
non-default ``temperature`` (400), and the design's near-zero temperature is
approximated by JSON-mode structured prompts instead.
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
    from anthropic import Anthropic

_DEFAULT_MODELS: dict[ModelName, str] = {
    ModelName.HAIKU: "claude-haiku-4-5",
    ModelName.SONNET: "claude-sonnet-5",
}

_JSON_SUFFIX = (
    "Respond with a single valid JSON object and nothing else — no markdown, "
    "no code fences, no commentary before or after."
)


class AnthropicGatewayClient:
    """GatewayClient backed by the Anthropic Messages API."""

    def __init__(
        self,
        *,
        haiku_model: str | None = None,
        sonnet_model: str | None = None,
        base_url: str | None = None,
        client: Any | None = None,
    ) -> None:
        self._models: dict[ModelName, str] = {
            ModelName.HAIKU: haiku_model or _DEFAULT_MODELS[ModelName.HAIKU],
            ModelName.SONNET: sonnet_model or _DEFAULT_MODELS[ModelName.SONNET],
        }
        # Lazy import so the base install never needs the SDK. The SDK resolves
        # credentials from the environment — we pass no key. An explicit base_url
        # points the same client at a company AI gateway that speaks the Anthropic
        # Messages API (its own key + URL) instead of api.anthropic.com (§2).
        self._client: Anthropic
        if client is not None:
            self._client = client
        else:
            import anthropic

            self._client = (
                anthropic.Anthropic(base_url=base_url) if base_url else anthropic.Anthropic()
            )

    def complete(self, request: GatewayRequest) -> GatewayResult:
        import anthropic

        system = request.system
        if request.json_mode:
            system = f"{system}\n\n{_JSON_SUFFIX}" if system else _JSON_SUFFIX

        # A per-request model override (the model dropdown) selects a concrete
        # Claude id; temperature stays omitted (current Claude models reject it).
        kwargs: dict[str, Any] = {
            "model": request.model_override or self._models[request.model],
            "max_tokens": request.max_tokens,
            "messages": [{"role": "user", "content": request.prompt}],
        }
        if system:
            kwargs["system"] = system

        try:
            response = self._client.messages.create(**kwargs)
        except anthropic.APIStatusError as exc:  # 4xx/5xx with a response
            if exc.status_code == 429 or exc.status_code >= 500:
                raise TransientGatewayError(str(exc), status_code=exc.status_code) from exc
            if exc.status_code in CONTENT_STATUS_CODES:
                # Per-item content failure (oversized prompt, unprocessable body) —
                # dead-lettered so one file cannot abort the batch (§5.8). Matches
                # the OpenAI client so both providers behave identically.
                raise ContentFailure(
                    str(exc),
                    kind=content_failure_kind(str(exc)),
                    detail=f"status={exc.status_code}",
                ) from exc
            raise
        except anthropic.APIConnectionError as exc:  # network failure before a response
            raise TransientGatewayError(str(exc)) from exc

        text = "".join(
            block.text for block in response.content if getattr(block, "type", None) == "text"
        )
        if request.json_mode:
            text = _coerce_json(text)
        return GatewayResult(
            text=text,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            model=request.model_override or self._models[request.model],
        )


def _coerce_json(text: str) -> str:
    """Strip a ```json fence if the model wrapped its object in one, so the
    router's ``json.loads`` succeeds."""
    stripped = text.strip()
    if stripped.startswith("```"):
        # Drop the opening fence line (```json / ```), keep the body.
        stripped = stripped.split("\n", 1)[1] if "\n" in stripped else stripped[3:]
        if stripped.rstrip().endswith("```"):
            stripped = stripped.rstrip()[:-3]
    return stripped.strip()
