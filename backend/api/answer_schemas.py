"""Request/response contracts for the answer endpoint (§Phase 6)."""

from __future__ import annotations

from pydantic import BaseModel, Field

from backend.answer.models import Answer
from backend.domain.models import Depth, Persona


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    persona: Persona = Persona.ENGINEER
    # Defaults to STANDARD so existing callers are unaffected; DEEP costs
    # materially more per question and is therefore opt-in (backend/answer/depth.py).
    depth: Depth = Depth.STANDARD


# The answer response is the Answer model itself — the four terminal states
# (synthesized / instant / refusal) are distinguished by its `mode` field, which
# is exactly what the UI switches on (ui-plan §2: refusal is a render state).
AskResponse = Answer
