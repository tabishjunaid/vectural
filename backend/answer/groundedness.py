"""Groundedness check — the second R1 gate (§5.4).

A **separate Haiku call**, claim by claim: does every claim in the answer follow
from the retrieved evidence? It runs only after citation resolution has passed
(cheaper, stricter first). Any unsupported claim means the answer is withheld —
fail closed (R1). Being a distinct call on a cheaper model is deliberate: the gate
is independent of the synthesis that produced the answer.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from backend.answer.citations import strip_fenced_blocks
from backend.domain.models import Persona, TaskType
from backend.llm.router import LLMRouter
from backend.retrieval.base import SearchHit

GROUNDEDNESS_PROMPT_VERSION = "grounded-v1"


class GroundednessResult(BaseModel):
    grounded: bool
    unsupported_claims: list[str] = Field(default_factory=list)


def check_groundedness(
    router: LLMRouter,
    *,
    answer_text: str,
    chunks: list[SearchHit],
    persona: Persona | None = None,
    prompt_version: str = GROUNDEDNESS_PROMPT_VERSION,
) -> GroundednessResult:
    """Route a claim-by-claim groundedness check and parse the verdict.

    A malformed verdict is treated conservatively as *not grounded* — the gate
    fails closed even on its own uncertainty."""
    prompt = _render_prompt(answer_text, chunks)
    response = router.route(TaskType.GROUNDEDNESS, prompt_version, {"prompt": prompt}, persona)
    try:
        return GroundednessResult.model_validate(response.parsed or {})
    except (ValueError, TypeError):
        return GroundednessResult(grounded=False, unsupported_claims=["unparseable verdict"])


def _render_prompt(answer_text: str, chunks: list[SearchHit]) -> str:
    evidence = "\n".join(f"- [{c.chunk_id}] {c.path}:{c.span}\n{c.content}" for c in chunks)
    # Judge the prose claims, not the illustrations. A fenced code/Mermaid block
    # restates cited prose visually; its markup is not an independent claim, and
    # feeding raw diagram syntax to the judge invites spurious "unsupported" flags.
    # The prompt requires every claim to appear (and be cited) in prose, so nothing
    # load-bearing hides in a diagram.
    prose = strip_fenced_blocks(answer_text)
    return (
        "Check every claim in the ANSWER against the EVIDENCE. Return JSON "
        '{"grounded": bool, "unsupported_claims": [string]}. A claim is grounded '
        "only if the evidence directly supports it.\n\n"
        f"# ANSWER\n{prose}\n\n# EVIDENCE\n{evidence}"
    )
