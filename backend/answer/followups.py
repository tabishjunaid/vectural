"""Follow-up question suggestions (§5.5 drill-down).

An answer is otherwise a dead end: a reader who does not already know the estate
has no idea what to ask next, and no way to discover that the graph can tell them
who calls what.

Suggestions are **derived from what the answer cited plus the graph and summaries
already gathered for it** — not from a model call. That is a deliberate trade:

- free, and adds no latency to a question the user is already waiting on;
- **backed by evidence that exists by construction** — every suggestion is keyed
  on a module the answer cited or a relationship the graph actually holds.

Ordering is about relevance. The most useful drill-down is into a concept the
answer *just discussed but did not expand* — so questions about the modules the
answer cited come first, ahead of the graph-topology questions ("how does A
interact with B"). Keying only on service edges made the suggestions blind to the
answer's content: a "how does vectural work" answer would offer "how does vectural
interact with java-core" — structurally valid, but not what the reader, having
just read about retrieval and orchestration, wants to ask next.

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
from backend.summarise.tiers import module_key

MAX_FOLLOW_UPS = 4


def _normalise(text: str) -> str:
    return "".join(ch for ch in text.lower() if ch.isalnum() or ch.isspace()).strip()


def _cited_modules(citations: list[Citation]) -> list[str]:
    """The distinct modules the answer actually cited, in first-cited order.

    A cited file has evidence by construction and is exactly what the reader just
    read about, so a question about its module is both grounded and relevant —
    unlike a graph edge picked purely from service topology. Files at the service
    root (``module_key == service``) are skipped: "what is <service> responsible
    for" is the question just answered, not a drill-down.
    """
    out: list[str] = []
    for c in citations:
        key = module_key(c.service, c.path)
        if key != c.service and key not in out:
            out.append(key)
    return out


def suggest_followups(
    ctx: AnswerContext,
    citations: list[Citation],
    question: str,
    *,
    likely_services: list[str] | None = None,
    limit: int = MAX_FOLLOW_UPS,
) -> list[str]:
    """Grounded questions to explore next, most relevant first.

    Ordering is deliberate: questions about the modules the answer *cited* come
    first — they are the concepts the reader just saw and most wants to expand —
    then the graph-topology questions ("how does A interact with B") that open up
    the wider estate.
    """
    asked = _normalise(question)
    out: list[str] = []

    def add(candidate: str) -> None:
        # Never suggest the question just asked, and never repeat ourselves.
        if _normalise(candidate) == asked or candidate in out:
            return
        out.append(candidate)

    # Most relevant: drill into a module the answer actually cited. This is the
    # fix for content-blind suggestions — it points the reader at what the answer
    # discussed (e.g. the reranker it cited but glossed over) rather than at
    # service edges they never read about.
    for module in _cited_modules(citations):
        add(f"What is {module} responsible for?")

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
