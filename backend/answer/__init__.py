"""The answer path — Phase 6 full serving pipeline (§5.3 steps 1-4, §5.4, §5.5).

This is the enforcement point for **R1 (accuracy cannot be compromised)**. Two
independent gates sit between synthesis and release, and neither is advisory:

1. **citation resolution** — deterministic code, not a model call: every citation
   in the answer must resolve to a chunk that was actually retrieved (§5.3)
2. **groundedness** — a separate Haiku call, claim by claim (§5.4)

Either gate withholds the answer. A refusal is a first-class result, not an
error: it names the likely owning services and fails closed.
"""

from backend.answer.cache import SemanticAnswerCache
from backend.answer.citations import CitationResolution, resolve_citations
from backend.answer.models import Answer, AnswerMode, Citation
from backend.answer.plan import RetrievalPlan, RetrievalPlanner
from backend.answer.service import AnswerService

__all__ = [
    "Answer",
    "AnswerMode",
    "AnswerService",
    "Citation",
    "CitationResolution",
    "RetrievalPlan",
    "RetrievalPlanner",
    "SemanticAnswerCache",
    "resolve_citations",
]
