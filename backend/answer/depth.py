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
opt-in: it is roughly 5x a ``BRIEF`` answer per question.
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
    Depth.STANDARD: DepthBudget(top_n=8, evidence_chars=2000, max_tokens=2000),
    Depth.DEEP: DepthBudget(top_n=14, evidence_chars=3500, max_tokens=4000),
}


def budget_for(depth: Depth) -> DepthBudget:
    return _BUDGETS[depth]
