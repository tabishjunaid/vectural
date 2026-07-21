"""Request contracts for the architect review endpoints (§Phase 7).

Mirrors the review.html surface: a queue, a detail view, and approve /
request-changes / reject actions. The review surface is **architect-only** — the
only write surface in the product — so every action carries the acting persona,
which the endpoint gates on.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from backend.domain.models import Persona


class ReviewAction(BaseModel):
    architect: str = Field(min_length=1)
    persona: Persona = Persona.ARCHITECT  # gated: must be architect
    reason: str | None = None
