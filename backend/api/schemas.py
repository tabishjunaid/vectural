"""Request/response contracts for the retrieval API.

These are the Pydantic models the TypeScript client mirrors (ui-development-plan
§2, "typed API client mirroring the FastAPI/Pydantic models"). Keeping the
returned hit shape identical to :class:`SearchHit` means a citation resolves the
same way on both sides: ``chunk_id -> path + lines``.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from backend.domain.models import Persona
from backend.retrieval.base import SearchHit


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    # Persona is a first-class, required control (R6). In Phase 2 it does not yet
    # change retrieval altitude (that is Phase 6) — it is threaded and echoed so
    # the contract is stable before synthesis exists.
    persona: Persona = Persona.ENGINEER
    services: list[str] | None = Field(
        default=None, description="Optional hard scope; omit to search the whole indexed estate"
    )
    top_n: int = Field(default=5, ge=1, le=50)
    candidate_k: int = Field(default=20, ge=1, le=200)


class SearchResponse(BaseModel):
    query: str
    persona: Persona
    scoped_services: list[str] | None
    count: int
    hits: list[SearchHit]


class HealthResponse(BaseModel):
    status: str = "ok"
    service: str = "vectural"
    phase: str = "2-retrieval"
