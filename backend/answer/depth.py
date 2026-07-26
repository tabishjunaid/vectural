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
opt-in: it is roughly 8x a ``BRIEF`` answer per question. ``STANDARD`` (the
default) and ``DEEP`` both feed the model enough evidence to write a full,
explanatory answer rather than a terse list — richer prose only stays grounded
if there is more evidence behind it, so breadth and the output ceiling rise
together. ``BRIEF`` is left small on purpose: the fast, one-glance summary.
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.domain.models import Depth


@dataclass(frozen=True)
class DepthBudget:
    top_n: int
    evidence_chars: int
    max_tokens: int


_BUDGETS: dict[Depth, DepthBudget] = {
    Depth.BRIEF: DepthBudget(top_n=4, evidence_chars=1200, max_tokens=800),
    Depth.STANDARD: DepthBudget(top_n=12, evidence_chars=2800, max_tokens=3500),
    Depth.DEEP: DepthBudget(top_n=18, evidence_chars=4500, max_tokens=6500),
}


def budget_for(depth: Depth) -> DepthBudget:
    return _BUDGETS[depth]
