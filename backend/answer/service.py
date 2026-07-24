"""Answer service — orchestrates the Phase 6 pipeline (§4.2 sequence, §5.4).

The order is the enforcement order of the design's answer path:

    cache → plan → scoped retrieval → synthesis → citation gate → groundedness gate

Both gates are fail-closed: on any failure the user gets a refusal that names the
likely owning services, never a fabricated answer (R1). The cache and an empty
retrieval are the two no-gateway-synthesis short circuits (§5.5, §5.4).
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel

from backend.answer.cache import SemanticAnswerCache
from backend.answer.citations import resolve_citations
from backend.answer.context import (
    StructuralContext,
    gather_context,
    render_context_block,
)
from backend.answer.depth import budget_for
from backend.answer.groundedness import check_groundedness
from backend.answer.models import Answer, AnswerMode
from backend.answer.plan import RetrievalPlanner
from backend.answer.synthesis import synthesise
from backend.domain.models import Depth, Persona
from backend.freshness.state import FreshnessState
from backend.llm.router import LLMRouter
from backend.retrieval.base import SearchHit
from backend.retrieval.service import RetrievalService
from backend.summarise.store import SummaryStore


class FlowEvidenceProvider(Protocol):
    """Supplies approved flow narratives as evidence (§Phase 7). Satisfied by
    ``FlowNarrativeService.evidence_for`` — only approved (authoritative)
    narratives are returned, so a multi-hop answer can cite a reviewed narrative
    rather than reconstruct it live."""

    def evidence_for(self, anchors: set[str]) -> list[SearchHit]: ...


class AnswerMetrics(Protocol):
    """Records answer-path metrics (§7.1). Satisfied by ``MetricsCollector``."""

    def record_answer(self, mode: AnswerMode) -> None: ...
    def record_latency_ms(self, latency_ms: float) -> None: ...


class AnswerStage(BaseModel):
    """One progress event on the answer path, for live UX (not persisted).

    ``stage`` is a stable machine key the UI maps to an icon/label; ``status`` is
    ``start`` before the work and a terminal marker after (``ok``/``hit``/``miss``/
    ``empty``/``fail``); ``detail`` is a short human sentence. Deliberately not an
    :class:`Answer` field — it is ephemeral narration, gone once the answer lands.
    """

    stage: str
    status: str
    detail: str = ""

    def __init__(self, stage: str, status: str, detail: str = "") -> None:
        super().__init__(stage=stage, status=status, detail=detail)


@dataclass
class AnswerService:
    retrieval: RetrievalService
    planner: RetrievalPlanner
    router: LLMRouter
    cache: SemanticAnswerCache | None = None
    flows: FlowEvidenceProvider | None = None
    freshness: FreshnessState | None = None
    metrics: AnswerMetrics | None = None
    # Tier-2/3 summaries and the call graph — background that turns a pile of
    # fragments into an explanation. Optional so the in-memory demo still runs.
    summaries: SummaryStore | None = None
    structural: StructuralContext | None = None
    commit_sha: str = "WORKING"
    top_n: int = 5

    def answer(
        self,
        question: str,
        persona: Persona = Persona.ENGINEER,
        depth: Depth = Depth.STANDARD,
    ) -> Answer:
        """Answer a question, timing it and recording answer-path metrics (§7.1)."""
        start = time.perf_counter()
        result = self._compute(question, persona, depth)
        if self.metrics is not None:
            self.metrics.record_latency_ms((time.perf_counter() - start) * 1000.0)
            self.metrics.record_answer(result.mode)
        return result

    def _compute(self, question: str, persona: Persona, depth: Depth) -> Answer:
        """The terminal answer, draining the staged pipeline. The single Answer is
        always the last item :meth:`stream` yields."""
        result: Answer | None = None
        for item in self.stream(question, persona, depth):
            if isinstance(item, Answer):
                result = item
        assert result is not None  # the pipeline always yields exactly one Answer
        return result

    def stream(
        self,
        question: str,
        persona: Persona = Persona.ENGINEER,
        depth: Depth = Depth.STANDARD,
    ) -> Iterator[AnswerStage | Answer]:
        """Walk the answer path, emitting an :class:`AnswerStage` as each stage
        starts/finishes and the terminal :class:`Answer` last.

        This is the *same* pipeline :meth:`answer` runs — there is one code path,
        not a fast one and a narrated one — so a streamed run and a plain one can
        never diverge. Each stage is yielded before its work and its result after,
        so a caller (the SSE endpoint) can show the user what is happening while
        the slow gateway calls (synthesis, groundedness) are in flight instead of
        a single opaque "thinking…".
        """
        # Fast path: semantic cache hit — no gateway call at all (§5.5).
        if self.cache is not None:
            yield AnswerStage("cache", "start", "Checking the answer cache")
            cached = self.cache.get(question, self.commit_sha, persona, depth)
            if cached is not None:
                yield AnswerStage("cache", "hit", "Answered from cache — no model call")
                yield cached.model_copy(
                    update={
                        "mode": AnswerMode.INSTANT,
                        "from_cache": True,
                        "reason": "semantic cache hit",
                    }
                )
                return
            yield AnswerStage("cache", "miss", "Not cached — computing a fresh answer")

        yield AnswerStage("plan", "start", "Planning which services to search")
        plan = self.planner.plan(question, persona)
        scope_label = ", ".join(sorted(plan.scope)) if plan.scope else "the whole estate"
        yield AnswerStage("plan", "ok", f"Scope: {scope_label}")

        budget = budget_for(depth)
        yield AnswerStage("retrieve", "start", "Searching the indexed code and docs")
        hits = self.retrieval.search(question, services=plan.scope, top_n=budget.top_n)

        # Prefer reviewed cross-service narratives over live reconstruction
        # (§Phase 7 exit criterion): prepend approved flow evidence so synthesis
        # can cite it. Only authoritative narratives are returned by the provider.
        if self.flows is not None and plan.anchors:
            flow_hits = self.flows.evidence_for(set(plan.anchors))
            hits = flow_hits + hits

        if not hits:
            yield AnswerStage("retrieve", "empty", "No matching evidence found")
            yield Answer.refusal(
                persona=persona,
                question=question,
                reason="no reliable coverage — no evidence retrieved",
                likely_services=plan.anchors,
            )
            return
        yield AnswerStage("retrieve", "ok", f"Found {len(hits)} passages of evidence")

        # Background that fragments cannot supply: what the touched services and
        # modules are responsible for, and how they depend on each other.
        ctx = gather_context(
            anchors=plan.anchors,
            hits=hits,
            summaries=self.summaries,
            structural=self.structural,
        )
        context_block = render_context_block(ctx)
        if context_block:
            yield AnswerStage(
                "context",
                "ok",
                f"Added {len(ctx.services)} service and {len(ctx.modules)} module summaries",
            )

        yield AnswerStage("synthesize", "start", "Drafting an answer from the evidence")
        response = synthesise(
            self.router,
            question=question,
            persona=persona,
            chunks=hits,
            context_block=context_block,
            evidence_chars=budget.evidence_chars,
            max_tokens=budget.max_tokens,
        )
        answer_text = response.text
        yield AnswerStage("synthesize", "ok", "Draft written")

        # Gate 1 — deterministic citation resolution (§5.3), cheaper + stricter first.
        yield AnswerStage("cite", "start", "Resolving every citation to real evidence")
        resolution = resolve_citations(answer_text, hits)
        if not resolution.ok:
            yield AnswerStage("cite", "fail", "A citation did not resolve — withholding")
            yield Answer.refusal(
                persona=persona,
                question=question,
                reason="citation could not be resolved to retrieved evidence",
                likely_services=_likely(plan.anchors, hits),
            )
            return
        yield AnswerStage("cite", "ok", f"{len(resolution.resolved)} citations resolved")

        # Gate 2 — groundedness, a separate Haiku call (§5.4).
        yield AnswerStage("ground", "start", "Verifying each claim against the evidence")
        grounded = check_groundedness(
            self.router,
            answer_text=answer_text,
            chunks=hits,
            # The judge must see what synthesis saw, or context-derived claims are
            # rejected for being unverifiable against chunks alone.
            context_block=context_block,
            persona=persona,
        )
        if not grounded.grounded:
            # Name the offending claim. The gate already identifies it, and
            # discarding that left an opaque refusal: identical questions succeed
            # or fail depending on whether *that* synthesis overreached, with no
            # way for a reader to tell which claim was the problem.
            yield AnswerStage("ground", "fail", "A claim was not supported — withholding")
            yield Answer.refusal(
                persona=persona,
                question=question,
                reason=_ungrounded_reason(grounded.unsupported_claims),
                likely_services=_likely(plan.anchors, hits),
            )
            return
        yield AnswerStage("ground", "ok", "All claims grounded")

        answer = Answer.synthesized(
            persona=persona, question=question, text=answer_text, citations=resolution.resolved
        )
        # Visible staleness: if a contributing service is mid-reindex, serve the
        # answer but flag it (§4.4). Stale answers are not cached.
        if self.freshness is not None and self.freshness.any_stale(set(plan.anchors)):
            yield answer.model_copy(update={"stale": True})
            return

        if self.cache is not None:
            self.cache.put(question, self.commit_sha, persona, answer, depth)
        yield answer


_UNGROUNDED = "a claim was not grounded in the retrieved evidence"


def _ungrounded_reason(unsupported: list[str], *, limit: int = 2, chars: int = 140) -> str:
    """The refusal reason, naming the claim(s) the gate rejected.

    Bounded: the reason is UI chrome, not the answer. Two claims is enough to see
    the pattern without turning a refusal card into a wall of text."""
    claims = [c.strip() for c in unsupported if c.strip()]
    if not claims:
        return _UNGROUNDED
    shown = [c if len(c) <= chars else c[:chars].rstrip() + "…" for c in claims[:limit]]
    more = len(claims) - len(shown)
    suffix = f" (+{more} more)" if more > 0 else ""
    return f"{_UNGROUNDED}: " + "; ".join(f"“{c}”" for c in shown) + suffix


def _likely(anchors: list[str], hits: list[SearchHit]) -> list[str]:
    if anchors:
        return anchors
    seen: list[str] = []
    for hit in hits:
        if hit.service not in seen:
            seen.append(hit.service)
    return seen
