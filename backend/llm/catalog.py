"""Selectable synthesis models (the Ask model dropdown).

The router still maps every task to a logical tier (HAIKU/SONNET) by default. This
catalog is the *override* surface: a user may pick a concrete model per question,
and the answer path routes the synthesis call to whichever provider owns it.

One list, so "which models can a user pick" is a single reviewable decision rather
than logic scattered across the API layer and the frontend. Each entry carries the
provider's API convention because it differs across models:

- classic OpenAI chat models (gpt-4o*) take ``max_tokens`` and accept a custom
  ``temperature``;
- newer OpenAI models (the gpt-5 family / reasoning models) take
  ``max_completion_tokens`` and reject a non-default ``temperature`` (400);
- Anthropic models take ``max_tokens`` and we never send ``temperature`` anyway.

``max_output`` is the model's own completion ceiling. It is honoured as a clamp,
but note the gateway clients do not stream, so a single non-streaming completion
is only reliable up to ~16k tokens regardless of a larger ceiling here — exploiting
the full window needs streaming (out of scope). The concrete ids and caps for the
gpt-5 family depend on the account's access; adjust them here if yours differ.
"""

from __future__ import annotations

from dataclasses import dataclass

# Reasoning models (gpt-5 family) spend hidden reasoning tokens out of the SAME
# ``max_completion_tokens`` budget as the visible answer. A small depth budget
# (e.g. STANDARD's 6500) gets consumed by reasoning, starving the answer to a
# stub that fails the citation gate. So for these models the effective completion
# budget is floored well above the depth budget — reasoning has room AND a full
# answer still fits. Bounded (not the model's full 128K) because the clients do
# not stream: a single non-streaming completion this large is already slow.
REASONING_OUTPUT_FLOOR = 32000


@dataclass(frozen=True)
class SelectableModel:
    id: str  # stable value the UI sends back (e.g. "gpt-5")
    label: str  # human label for the dropdown (e.g. "GPT-5")
    provider: str  # "openai" | "anthropic" — which gateway client answers
    concrete: str  # the model id sent to the provider API
    max_output: int  # the model's completion-token ceiling
    uses_max_completion_tokens: bool = False  # newer OpenAI param convention
    supports_temperature: bool = True  # newer OpenAI models reject a custom temperature
    # For reasoning models (gpt-5 family): how hard to think. Synthesis is
    # writing a grounded answer from evidence already in the prompt — not a hard
    # reasoning task — so a low setting keeps the hidden reasoning from eating the
    # completion budget (which otherwise truncates the answer and trips the
    # citation gate) and cuts latency sharply. None = don't send the param.
    reasoning_effort: str | None = None

    @property
    def hint(self) -> str:
        vendor = "OpenAI" if self.provider == "openai" else "Anthropic"
        return f"{vendor} · up to {self.max_output // 1000}K out"


# The default catalog. Edit here to add/adjust models as account access allows.
SELECTABLE_MODELS: list[SelectableModel] = [
    # OpenAI — classic chat convention.
    SelectableModel("gpt-4o", "GPT-4o", "openai", "gpt-4o", 16384),
    SelectableModel("gpt-4o-mini", "GPT-4o mini", "openai", "gpt-4o-mini", 16384),
    # OpenAI — gpt-5 family: max_completion_tokens, no custom temperature. Concrete
    # ids/caps are account-dependent; change the third/fifth args if yours differ.
    SelectableModel(
        "gpt-5", "GPT-5", "openai", "gpt-5", 128000,
        uses_max_completion_tokens=True, supports_temperature=False, reasoning_effort="low",
    ),
    SelectableModel(
        "gpt-5-mini", "GPT-5 mini", "openai", "gpt-5-mini", 128000,
        uses_max_completion_tokens=True, supports_temperature=False, reasoning_effort="low",
    ),
    # Anthropic — appear in the dropdown only when the Anthropic gateway is wired.
    SelectableModel("claude-sonnet-5", "Claude Sonnet 5", "anthropic", "claude-sonnet-5", 128000),
    SelectableModel("claude-opus-4-8", "Claude Opus 4.8", "anthropic", "claude-opus-4-8", 128000),
    SelectableModel("claude-haiku-4-5", "Claude Haiku 4.5", "anthropic", "claude-haiku-4-5", 64000),
]

_BY_ID: dict[str, SelectableModel] = {m.id: m for m in SELECTABLE_MODELS}


def find(model_id: str | None) -> SelectableModel | None:
    """The catalog entry for a UI model id, or None (unknown / no override)."""
    return _BY_ID.get(model_id) if model_id else None


def available_models(providers: set[str]) -> list[SelectableModel]:
    """Catalog entries whose provider is actually constructable, so the dropdown
    never offers a model whose gateway isn't wired (its calls would just fail)."""
    return [m for m in SELECTABLE_MODELS if m.provider in providers]
