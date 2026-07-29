"""Depth → retrieval/synthesis budget (§5.3, §5.4).

One table, so "how thorough is an answer" is a single reviewable decision rather
than magic numbers spread across the retrieval service, the prompt renderer and
the router. Three knobs move together because they are one question — how much
material the model gets and how much it may write:

- ``top_n``          — how many evidence chunks reach synthesis
- ``evidence_chars`` — how much of each chunk (a truncated function body explains
  nothing, so depth and breadth have to rise together)
- ``max_tokens``     — the answer's output ceiling

The costs are real and superlinear-ish in combination, which is why ``DEEP`` is
opt-in. The three tiers are deliberately distinct in *kind*, not just size:

- ``BRIEF``    — the fast, one-glance summary.
- ``STANDARD`` — a full, well-sectioned explanatory answer (what ``DEEP`` used
  to be). The everyday default.
- ``DEEP``     — leave nothing out. Far more evidence, almost no per-chunk
  truncation, the model's maximum output, and an *exhaustive* synthesis prompt
  that asks for every concept explained in full rather than a terse list. Cost
  is not the constraint here; the serving model's output ceiling is.
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.domain.models import Depth


@dataclass(frozen=True)
class DepthBudget:
    top_n: int
    evidence_chars: int
    max_tokens: int
    exhaustive: bool = False  # DEEP: switch the prompt to "explain everything, in full"


# STANDARD is what DEEP used to be — a full, well-sectioned answer. DEEP is now the
# "leave nothing out" tier: it feeds far more evidence with almost no per-chunk
# truncation, writes to the model's output ceiling, and switches the synthesis
# prompt to an exhaustive voice (``exhaustive=True``). BRIEF stays the fast summary.
#
# The output ceiling is bounded by the serving model, NOT by our willingness to
# spend: gpt-4o (the current OpenAI synthesis model) caps a completion at 16,384
# tokens, so 16,000 is the practical maximum here. A single call cannot exceed
# that — the lever for a genuinely larger answer is the Anthropic gateway
# (claude-sonnet-5, 128K output, needs streaming) or multi-pass synthesis.
# ``evidence_chars`` is high enough that real source files are passed whole; the
# residual truncation guard only trips on pathologically large chunks, so the
# whole prompt stays inside the model's input window.
_BUDGETS: dict[Depth, DepthBudget] = {
    Depth.BRIEF: DepthBudget(top_n=4, evidence_chars=1200, max_tokens=800),
    Depth.STANDARD: DepthBudget(top_n=18, evidence_chars=4500, max_tokens=6500),
    Depth.DEEP: DepthBudget(top_n=24, evidence_chars=10000, max_tokens=16000, exhaustive=True),
}


def budget_for(depth: Depth) -> DepthBudget:
    return _BUDGETS[depth]
