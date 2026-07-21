"""Flow narrative service — generation, review lifecycle, invalidation, serving.

The lifecycle enforces the §4.4 contract:

    generate → PENDING → (architect) APPROVED → (code change) NEEDS_REVIEW → …

- generation is content-hash keyed (no re-spend on unchanged flows) and only
  ever explicit — a code change does **not** regenerate
- only ``APPROVED`` narratives are served as evidence (``evidence_for``)
- ``invalidate_on_change`` flips affected approved narratives to ``NEEDS_REVIEW``
  and drops them from authoritative serving until an architect re-approves —
  fail-closed: a stale cross-service claim is withheld, not silently served
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from backend.domain.models import ChunkKind, Language, Span
from backend.flows.generate import FLOW_PROMPT_VERSION, content_hash, generate_narrative
from backend.flows.identify import FlowCandidate
from backend.flows.models import FlowNarrative, ReviewStatus
from backend.flows.review import FlowStore
from backend.llm.router import LLMRouter
from backend.retrieval.base import SearchHit


@dataclass
class GenerateReport:
    created: int = 0
    regenerated: int = 0
    skipped: int = 0
    ids: list[str] = field(default_factory=list)


class FlowNotFoundError(KeyError):
    pass


@dataclass
class FlowNarrativeService:
    store: FlowStore
    router: LLMRouter
    prompt_version: str = FLOW_PROMPT_VERSION
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)

    # -- generation --------------------------------------------------------- #

    def generate(
        self,
        candidates: list[FlowCandidate],
        *,
        service_summaries: dict[str, str] | None = None,
    ) -> GenerateReport:
        report = GenerateReport()
        for cand in candidates:
            existing = self.store.get(cand.id)
            new_hash = content_hash(cand.signature, self.prompt_version)
            if existing is not None and existing.content_hash == new_hash:
                report.skipped += 1  # unchanged structure + prompt -> no re-spend
                continue

            generated = generate_narrative(
                self.router, cand, service_summaries=service_summaries,
                prompt_version=self.prompt_version,
            )
            now = self.clock()
            if existing is None:
                narrative = FlowNarrative(
                    id=cand.id, title=cand.title, services=list(cand.services),
                    trigger=cand.trigger, signature=cand.signature, text=generated.text,
                    summary=generated.summary, prompt_version=self.prompt_version,
                    content_hash=generated.content_hash, status=ReviewStatus.PENDING,
                    updated_at=now,
                )
                report.created += 1
            else:
                narrative = existing.model_copy(
                    update={
                        "text": generated.text,
                        "summary": generated.summary,
                        "content_hash": generated.content_hash,
                        "status": ReviewStatus.PENDING,  # re-generation needs re-approval
                        "review_reason": None,
                        "updated_at": now,
                    }
                )
                report.regenerated += 1
            self.store.upsert(narrative)
            report.ids.append(cand.id)
        return report

    # -- review transitions ------------------------------------------------- #

    def approve(self, flow_id: str, architect: str) -> FlowNarrative:
        narrative = self._require(flow_id)
        now = self.clock()
        updated = narrative.model_copy(
            update={
                "status": ReviewStatus.APPROVED,
                "review_reason": None,
                "last_approved_text": narrative.text,
                "last_approved_by": architect,
                "last_approved_at": now,
                "updated_at": now,
            }
        )
        self.store.upsert(updated)
        return updated

    def request_changes(self, flow_id: str, architect: str, reason: str) -> FlowNarrative:
        return self._set_status(flow_id, ReviewStatus.CHANGES_REQUESTED, reason=reason)

    def reject(self, flow_id: str, architect: str, reason: str | None = None) -> FlowNarrative:
        return self._set_status(flow_id, ReviewStatus.REJECTED, reason=reason)

    # -- freshness (§4.4) --------------------------------------------------- #

    def invalidate_on_change(self, changed_services: set[str]) -> list[str]:
        """Flip approved narratives touching a changed service to needs_review.

        Never regenerates. Returns the affected flow ids."""
        affected: list[str] = []
        for narrative in self.store.all():
            if not narrative.is_authoritative:
                continue
            overlap = changed_services.intersection(narrative.services)
            if overlap:
                reason = f"code changed in {', '.join(sorted(overlap))} since last approval"
                self._set_status(narrative.id, ReviewStatus.NEEDS_REVIEW, reason=reason)
                affected.append(narrative.id)
        return affected

    # -- serving ------------------------------------------------------------ #

    def evidence_for(self, anchors: set[str]) -> list[SearchHit]:
        """Approved flow narratives overlapping the anchors, as citable evidence.

        A ``needs_review`` narrative is intentionally excluded — an unreviewed or
        stale cross-service claim is never served as fact (R1/R3)."""
        hits: list[SearchHit] = []
        for narrative in self.store.all():
            if narrative.is_authoritative and anchors.intersection(narrative.services):
                hits.append(_to_hit(narrative))
        return hits

    def queue(self) -> list[FlowNarrative]:
        return [n for n in self.store.all() if n.in_review_queue]

    def get(self, flow_id: str) -> FlowNarrative | None:
        return self.store.get(flow_id)

    # -- internals ---------------------------------------------------------- #

    def _require(self, flow_id: str) -> FlowNarrative:
        narrative = self.store.get(flow_id)
        if narrative is None:
            raise FlowNotFoundError(flow_id)
        return narrative

    def _set_status(
        self, flow_id: str, status: ReviewStatus, *, reason: str | None
    ) -> FlowNarrative:
        narrative = self._require(flow_id)
        updated = narrative.model_copy(
            update={"status": status, "review_reason": reason, "updated_at": self.clock()}
        )
        self.store.upsert(updated)
        return updated


def _to_hit(narrative: FlowNarrative) -> SearchHit:
    line_count = max(1, narrative.text.count("\n") + 1)
    return SearchHit(
        chunk_id=f"flow:{narrative.id}",
        service=narrative.services[0] if narrative.services else "",
        path=f"flow/{narrative.id}",
        span=Span(start=1, end=line_count),
        language=Language.UNKNOWN,
        kind=ChunkKind.MODULE,
        symbol=narrative.title,
        content=narrative.text,
        commit_sha="approved",
        score=1.0,
    )
