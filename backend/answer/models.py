"""Answer result types — the four terminal states the UI renders (§5.4, ui-plan §2).

``streaming`` is a transport state, not a distinct result; the three *terminal*
results are ``SYNTHESIZED`` (answer + resolvable citations), ``INSTANT``
(cache/structural hit, no gateway call), and ``REFUSAL`` (fail-closed, names
likely owning services). Refusal is a considered answer, not a failure.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from backend.domain.models import Persona, Span


class AnswerMode(StrEnum):
    SYNTHESIZED = "synthesized"  # answer + citations, passed both R1 gates
    INSTANT = "instant"  # cache hit or structural result, no gateway call (§5.5)
    REFUSAL = "refusal"  # fail-closed: no reliable coverage (R1)


class Citation(BaseModel):
    """A resolved citation: the marker in the answer maps to a retrieved chunk."""

    index: int  # 1-based display order
    chunk_id: str
    service: str
    path: str
    span: Span


class Answer(BaseModel):
    mode: AnswerMode
    persona: Persona
    question: str
    text: str
    citations: list[Citation] = Field(default_factory=list)
    # Refusal / instant metadata.
    reason: str | None = None
    likely_services: list[str] = Field(default_factory=list)
    from_cache: bool = False
    # Serving continues during a reindex with a *visible* staleness flag (§4.4, R3).
    stale: bool = False

    @classmethod
    def synthesized(
        cls, *, persona: Persona, question: str, text: str, citations: list[Citation]
    ) -> Answer:
        return cls(
            mode=AnswerMode.SYNTHESIZED,
            persona=persona,
            question=question,
            text=text,
            citations=citations,
        )

    @classmethod
    def refusal(
        cls, *, persona: Persona, question: str, reason: str, likely_services: list[str]
    ) -> Answer:
        # The refusal wording is the same source of truth the coverage surface uses
        # (§5.4), so the two never contradict each other about a service.
        names = ", ".join(likely_services) if likely_services else "the relevant services"
        text = (
            f"No reliable coverage for this yet — the answer could not be verified. "
            f"This likely belongs to {names}."
        )
        return cls(
            mode=AnswerMode.REFUSAL,
            persona=persona,
            question=question,
            text=text,
            reason=reason,
            likely_services=likely_services,
        )

    @classmethod
    def instant(
        cls,
        *,
        persona: Persona,
        question: str,
        text: str,
        citations: list[Citation] | None = None,
        reason: str = "cache hit",
        from_cache: bool = True,
    ) -> Answer:
        return cls(
            mode=AnswerMode.INSTANT,
            persona=persona,
            question=question,
            text=text,
            citations=citations or [],
            reason=reason,
            from_cache=from_cache,
        )
