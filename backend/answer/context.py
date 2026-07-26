"""Architectural context for synthesis (§5.2 tiers, §5.5 structural queries).

Retrieval returns *fragments* — the five-to-fourteen chunks that best match the
question. Fragments are what a claim must cite, but they are a poor basis for
explaining a system: they carry no notion of what a module is responsible for or
which services depend on which.

Vectural already computes both of those and, until now, threw them away at answer
time: the tier-2/3 summary pyramid was written to Postgres and read only by the
coverage screen, and the call graph was queried only to *narrow* retrieval. This
module gathers them into a context block that orients the model before it reads
the fragments.

**Context is not evidence.** Summaries and graph edges have no ``chunk_id``, so a
citation to them could never resolve and would trip the R1 citation gate. They
frame the answer; every claim still cites a retrieved chunk. The prompt says so
explicitly, and :func:`render_context_block` deliberately emits no bracketed ids
that could be mistaken for citation markers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from backend.retrieval.base import SearchHit
from backend.summarise.store import SummaryStore
from backend.summarise.tiers import module_key

# Bounded so context never crowds out the evidence it is meant to frame.
MAX_MODULES = 8
MAX_SERVICES = 6
MAX_RELATED = 8


class StructuralContext(Protocol):
    """The slice of ``StructuralQueries`` (backend/graph/queries.py) used here."""

    def direct_callees(self, service: str) -> list[str]: ...
    def direct_callers(self, service: str) -> list[str]: ...


@dataclass
class AnswerContext:
    services: list[tuple[str, str]] = field(default_factory=list)  # (name, description)
    modules: list[tuple[str, str]] = field(default_factory=list)  # (key, responsibility)
    edges: list[str] = field(default_factory=list)  # "a → b, c" / "a ← d"
    # The same call-graph facts as `edges`, kept structured. `edges` is prose for
    # the prompt; follow-up suggestions need to key on the actual neighbours, and
    # re-parsing rendered strings to get them back would be silly.
    callees: dict[str, list[str]] = field(default_factory=dict)
    callers: dict[str, list[str]] = field(default_factory=dict)

    def is_empty(self) -> bool:
        return not (self.services or self.modules or self.edges)


def gather_context(
    *,
    anchors: list[str],
    hits: list[SearchHit],
    summaries: SummaryStore | None,
    structural: StructuralContext | None,
) -> AnswerContext:
    """Collect service/module summaries and dependency edges for this question.

    Scoped to what the question actually touched — the anchor services and the
    modules the retrieved hits came from — rather than dumping the whole estate,
    which would bury the evidence and cost tokens for no gain.
    """
    ctx = AnswerContext()

    # Services: prefer the planner's anchors; fall back to the services that
    # actually produced evidence, so an unanchored question still gets context.
    services = list(dict.fromkeys(anchors or [h.service for h in hits]))[:MAX_SERVICES]

    if summaries is not None:
        for service in services:
            record = summaries.get(3, service)
            if record is not None and record.text:
                ctx.services.append((service, record.text))

        seen: set[str] = set()
        for hit in hits:
            key = module_key(hit.service, hit.path)
            if key in seen:
                continue
            seen.add(key)
            record = summaries.get(2, key)
            if record is not None and record.text:
                ctx.modules.append((key, record.text))
            if len(ctx.modules) >= MAX_MODULES:
                break

    if structural is not None:
        for service in services:
            try:
                callees = structural.direct_callees(service)[:MAX_RELATED]
                callers = structural.direct_callers(service)[:MAX_RELATED]
            except Exception:  # a graph hiccup must not fail the answer
                continue
            if callees:
                ctx.edges.append(f"{service} calls: {', '.join(callees)}")
                ctx.callees[service] = callees
            if callers:
                ctx.edges.append(f"{service} is called by: {', '.join(callers)}")
                ctx.callers[service] = callers

    return ctx


def render_context_block(ctx: AnswerContext) -> str:
    """Render the context for the prompt, or "" when there is nothing to add.

    Uses no square brackets anywhere: a bracketed token here would be picked up by
    the citation extractor and fail to resolve, turning context into a refusal."""
    if ctx.is_empty():
        return ""
    lines: list[str] = []
    if ctx.services:
        lines.append("## Services involved")
        lines += [f"- {name}: {text}" for name, text in ctx.services]
    if ctx.modules:
        lines.append("## Modules involved")
        lines += [f"- {key}: {text}" for key, text in ctx.modules]
    if ctx.edges:
        lines.append("## Dependencies (from the call graph)")
        lines += [f"- {edge}" for edge in ctx.edges]
    return "\n".join(lines)
