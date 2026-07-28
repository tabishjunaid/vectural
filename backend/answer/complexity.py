"""Query-complexity assessment — a free, deterministic signal (no model call).

Answering a question the same way regardless of how involved it is wastes tokens
on the trivial ones and starves the hard ones. Depth (BRIEF/STANDARD/DEEP) is the
budget the *user* picks; complexity is how much of that budget the *question*
actually warrants, and the synthesis prompt uses it to shape the explanation:
a narrow lookup gets a concise answer even at STANDARD, a cross-service question
gets the full sectioned walkthrough.

The score is built only from signals already computed before synthesis runs — the
planner's anchors/scope/fallback and the retrieved-hit count, plus the shape of
the question text itself — so it costs nothing extra. Thresholds are deliberately
conservative and tunable; the goal is "obviously simple" vs "obviously involved",
not a precise gradient.
"""

from __future__ import annotations

import re

from backend.answer.plan import RetrievalPlan
from backend.domain.models import Complexity
from backend.retrieval.base import SearchHit

# Words that signal a *relational* / mechanism question ("how does X reach Y")
# rather than a factual lookup ("which project uses X"). Matched whole-word,
# case-insensitively. Kept curated — common filler ("and", "the") is excluded so
# it does not fire on every sentence.
_RELATIONAL_WORDS: frozenset[str] = frozenset(
    {
        "how", "why", "interact", "interacts", "depend", "depends", "dependency",
        "flow", "flows", "across", "between", "via", "propagate", "propagates",
        "consume", "consumes", "trigger", "triggers", "affect", "affects",
        "connect", "connects", "integrate", "integrates", "chain", "pipeline",
        "downstream", "upstream", "call", "calls", "invoke", "invokes",
        "orchestrate", "orchestrates", "sequence", "lifecycle",
    }
)

_WORD_RE = re.compile(r"[a-z0-9][a-z0-9\-]*")


def assess_complexity(question: str, plan: RetrievalPlan, hits: list[SearchHit]) -> Complexity:
    """Classify a question as SIMPLE / MODERATE / COMPLEX from cheap signals.

    Pure and side-effect-free: given the same inputs it always returns the same
    class, so it is trivially unit-testable and adds no latency or token cost."""
    words = _WORD_RE.findall(question.lower())
    word_count = len(words)
    relational = sum(1 for w in set(words) if w in _RELATIONAL_WORDS)

    score = 0

    # Question shape: a terse question is almost always a lookup; a long one is
    # usually asking for a walkthrough.
    if word_count <= 4:
        score -= 1
    elif word_count >= 14:
        score += 1

    # Mechanism/relationship phrasing — the strongest "this needs explaining" tell.
    if relational >= 1:
        score += 1
    if relational >= 2:
        score += 1

    # Graph breadth: how many services the question actually touches, and how much
    # of the estate the plan pulled into scope.
    anchors = len(plan.anchors)
    if anchors >= 2:
        score += 1
    if anchors >= 3:
        score += 1
    if len(plan.scope or ()) >= 6:
        score += 1

    # Retrieval breadth: a lot of evidence matched means a broad question. Bounded
    # by top_n (BRIEF caps at 4), so only a genuinely high count counts.
    if len(hits) >= 14:
        score += 1

    # Planning friction: the Cypher fell back after failing to plan cleanly — the
    # question was hard to pin to the graph (often broad or under-specified).
    if plan.used_fallback:
        score += 1

    if score <= 0:
        return Complexity.SIMPLE
    if score <= 2:
        return Complexity.MODERATE
    return Complexity.COMPLEX
