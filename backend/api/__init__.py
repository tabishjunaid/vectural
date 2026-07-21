"""FastAPI serving layer (implementation-plan §Phase 2).

Phase 2 exposes retrieval only: ``/search`` returns ranked chunks with no LLM
synthesis, so retrieval quality can be measured (via the eval harness) before a
single gateway token is spent on answers. The answer path, SSE streaming, and
persona-specific synthesis (§5.4, R6) layer on top in Phase 6.
"""

from backend.api.app import create_app

__all__ = ["create_app"]
