"""Follow-up question suggestions (§5.5 drill-down).

An answer is otherwise a dead end: a reader who does not already know the estate
has no idea what to ask next, and no way to discover that the graph can tell them
who calls what.

Suggestions are **derived from the graph and summaries already gathered for this
answer** — not from a model call. That is a deliberate trade:

- free, and adds no latency to a question the user is already waiting on;
- **backed by evidence that exists by construction** — every suggestion is keyed
  on a relationship the graph actually holds.

That last point is about evidence, not outcomes. A suggestion is *not* guaranteed
to produce a released answer: the R1 gates are fail-closed and synthesis is
non-deterministic, so a particular draft can still be withheld and the same
question succeed on a retry. What is guaranteed is that the reader is never sent
after something the estate has no material for.

Asking the LLM would phrase them more naturally, but it costs tokens on every
question and can propose things the estate cannot answer at all — a strictly worse
failure than a gate occasionally withholding a draft.
"""

from __future__ import annotations

from backend.answer.context import AnswerContext
from backend.answer.models import Citation

MAX_FOLLOW_UPS = 4


def _normalise(text: str) -> str:
    return "".join(ch for ch in text.lower() if ch.isalnum() or ch.isspace()).strip()


def suggest_followups(
    ctx: AnswerContext,
    citations: list[Citation],
    question: str,
    *,
    likely_services: list[str] | None = None,
    limit: int = MAX_FOLLOW_UPS,
) -> list[str]:
    """Grounded questions to explore next, most specific first.

    Ordering is deliberate: relationship questions ("how does A interact with B")
    open up the estate, while "what is X responsible for" is a narrowing move, so
    the broadening ones come first.
    """
    asked = _normalise(question)
    out: list[str] = []

    def add(candidate: str) -> None:
        # Never suggest the question just asked, and never repeat ourselves.
        if _normalise(candidate) == asked or candidate in out:
            return
        out.append(candidate)

    services = [name for name, _ in ctx.services]
    # A refusal has no context to speak of, but it does name the services it
    # believes own the question — which is exactly what the reader needs next.
    if not services and likely_services:
        services = list(likely_services)

    for service in services:
        for neighbour in ctx.callees.get(service, [])[:2]:
            add(f"How does {service} interact with {neighbour}?")
    for service in services:
        if ctx.callees.get(service):
            add(f"What does {service} depend on?")
        if ctx.callers.get(service):
            add(f"What calls into {service}?")
        # A service we know exists but have no edges for — the empty-retrieval
        # refusal, where the planner named an anchor but nothing came back. Still
        # answerable: the service is in the manifest and has summaries.
        if not ctx.callees.get(service) and not ctx.callers.get(service):
            add(f"What does {service} depend on?")

    if len(services) >= 2:
        add(f"How do {services[0]} and {services[1]} work together?")

    for key, _ in ctx.modules[:2]:
        add(f"What is {key} responsible for?")

    # Fall back to the services that actually produced the evidence, so an answer
    # with thin context still offers somewhere to go.
    if not out:
        for service in dict.fromkeys(c.service for c in citations):
            add(f"What does {service} depend on?")

    return out[:limit]
