"""Answer service — orchestrates the Phase 6 pipeline (§4.2 sequence, §5.4).

The order is the enforcement order of the design's answer path:

    cache → plan → scoped retrieval → synthesis → citation gate → groundedness gate

Both gates are fail-closed: on any failure the user gets a refusal that names the
likely owning services, never a fabricated answer (R1). The cache and an empty
retrieval are the two no-gateway-synthesis short circuits (§5.5, §5.4).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from backend.answer.cache import SemanticAnswerCache
from backend.answer.citations import resolve_citations
from backend.answer.groundedness import check_groundedness
from backend.answer.models import Answer, AnswerMode
from backend.answer.plan import RetrievalPlanner
from backend.answer.synthesis import synthesise
from backend.domain.models import Persona
from backend.freshness.state import FreshnessState
from backend.llm.router import LLMRouter
from backend.retrieval.base import SearchHit
from backend.retrieval.service import RetrievalService


class FlowEvidenceProvider(Protocol):
    """Supplies approved flow narratives as evidence (§Phase 7). Satisfied by
    ``FlowNarrativeService.evidence_for`` — only approved (authoritative)
    narratives are returned, so a multi-hop answer can cite a reviewed narrative
    rather than reconstruct it live."""

    def evidence_for(self, anchors: set[str]) -> list[SearchHit]: ...


@dataclass
class AnswerService:
    retrieval: RetrievalService
    planner: RetrievalPlanner
    router: LLMRouter
    cache: SemanticAnswerCache | None = None
    flows: FlowEvidenceProvider | None = None
    freshness: FreshnessState | None = None
    commit_sha: str = "WORKING"
    top_n: int = 5

    def answer(self, question: str, persona: Persona = Persona.ENGINEER) -> Answer:
        # Fast path: semantic cache hit — no gateway call at all (§5.5).
        if self.cache is not None:
            cached = self.cache.get(question, self.commit_sha, persona)
            if cached is not None:
                return cached.model_copy(
                    update={
                        "mode": AnswerMode.INSTANT,
                        "from_cache": True,
                        "reason": "semantic cache hit",
                    }
                )

        plan = self.planner.plan(question, persona)
        hits = self.retrieval.search(question, services=plan.scope, top_n=self.top_n)

        # Prefer reviewed cross-service narratives over live reconstruction
        # (§Phase 7 exit criterion): prepend approved flow evidence so synthesis
        # can cite it. Only authoritative narratives are returned by the provider.
        if self.flows is not None and plan.anchors:
            flow_hits = self.flows.evidence_for(set(plan.anchors))
            hits = flow_hits + hits

        if not hits:
            return Answer.refusal(
                persona=persona,
                question=question,
                reason="no reliable coverage — no evidence retrieved",
                likely_services=plan.anchors,
            )

        response = synthesise(self.router, question=question, persona=persona, chunks=hits)
        answer_text = response.text

        # Gate 1 — deterministic citation resolution (§5.3), cheaper + stricter first.
        resolution = resolve_citations(answer_text, hits)
        if not resolution.ok:
            return Answer.refusal(
                persona=persona,
                question=question,
                reason="citation could not be resolved to retrieved evidence",
                likely_services=_likely(plan.anchors, hits),
            )

        # Gate 2 — groundedness, a separate Haiku call (§5.4).
        grounded = check_groundedness(
            self.router, answer_text=answer_text, chunks=hits, persona=persona
        )
        if not grounded.grounded:
            return Answer.refusal(
                persona=persona,
                question=question,
                reason="a claim was not grounded in the retrieved evidence",
                likely_services=_likely(plan.anchors, hits),
            )

        answer = Answer.synthesized(
            persona=persona, question=question, text=answer_text, citations=resolution.resolved
        )
        # Visible staleness: if a contributing service is mid-reindex, serve the
        # answer but flag it (§4.4). Stale answers are not cached.
        if self.freshness is not None and self.freshness.any_stale(set(plan.anchors)):
            return answer.model_copy(update={"stale": True})

        if self.cache is not None:
            self.cache.put(question, self.commit_sha, persona, answer)
        return answer


def _likely(anchors: list[str], hits: list[SearchHit]) -> list[str]:
    if anchors:
        return anchors
    seen: list[str] = []
    for hit in hits:
        if hit.service not in seen:
            seen.append(hit.service)
    return seen
