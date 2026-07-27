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
from backend.llm.base import UsageRecord
from backend.llm.router import LLMRouter
from backend.retrieval.base import SearchHit

GROUNDEDNESS_PROMPT_VERSION = "grounded-v1"


class GroundednessResult(BaseModel):
    grounded: bool
    unsupported_claims: list[str] = Field(default_factory=list)
    # Token usage for this gate's own LLM call — set by check_groundedness after
    # parsing, not part of the judged JSON. Exposed for per-query analytics.
    usage: UsageRecord | None = Field(default=None, exclude=True)

    @property
    def should_withhold(self) -> bool:
        """Withhold the answer only when a *specific* unsupported claim is named.

        A bare ``grounded: false`` with an empty ``unsupported_claims`` is a
        self-contradictory verdict — the judge declared the answer ungrounded but
        could not point to anything. Smaller judge models emit this on long
        answers, and it was refusing well-grounded answers on a non-actionable
        signal. Fail-closed is preserved where it matters: a genuinely
        unsupported claim is named (→ withhold), and a malformed/unparseable
        verdict is recorded as an explicit ``"unparseable verdict"`` claim below
        (→ withhold). The citation gate has already verified every claim cites a
        real chunk before this gate runs."""
        return bool(self.unsupported_claims)


def check_groundedness(
    router: LLMRouter,
    *,
    answer_text: str,
    chunks: list[SearchHit],
    context_block: str = "",
    persona: Persona | None = None,
    prompt_version: str = GROUNDEDNESS_PROMPT_VERSION,
) -> GroundednessResult:
    """Route a claim-by-claim groundedness check and parse the verdict.

    ``context_block`` must be the *same* architectural context synthesis was given.
    The gate has to judge against the material the model actually saw: when
    synthesis gained service/module summaries and the judge did not, it correctly
    but uselessly rejected summary-level claims ("the quota governor approves
    budgets before indexing") that no single code chunk states — turning a better
    answer into a refusal. Summaries are themselves derived from the indexed code
    by the tier pipeline, so supporting a claim is legitimate grounding; the
    citation gate separately still requires every claim to cite a real chunk.

    A malformed verdict is treated conservatively as *not grounded* — the gate
    fails closed even on its own uncertainty."""
    prompt = _render_prompt(answer_text, chunks, context_block)
    response = router.route(TaskType.GROUNDEDNESS, prompt_version, {"prompt": prompt}, persona)
    try:
        result = GroundednessResult.model_validate(response.parsed or {})
    except (ValueError, TypeError):
        result = GroundednessResult(grounded=False, unsupported_claims=["unparseable verdict"])
    result.usage = response.usage
    return result


def _render_prompt(answer_text: str, chunks: list[SearchHit], context_block: str = "") -> str:
    evidence = "\n".join(f"- [{c.chunk_id}] {c.path}:{c.span}\n{c.content}" for c in chunks)
    context = (
        f"\n# ARCHITECTURAL CONTEXT (also supports claims)\n{context_block}\n"
        if context_block
        else ""
    )
    # Judge the prose claims, not the illustrations. A fenced code/Mermaid block
    # restates cited prose visually; its markup is not an independent claim, and
    # feeding raw diagram syntax to the judge invites spurious "unsupported" flags.
    # The prompt requires every claim to appear (and be cited) in prose, so nothing
    # load-bearing hides in a diagram.
    prose = strip_fenced_blocks(answer_text)
    # Only point the judge at a section that exists — naming an absent
    # ARCHITECTURAL CONTEXT would have it look for material that is not there.
    sources = "the EVIDENCE or the ARCHITECTURAL CONTEXT" if context_block else "the EVIDENCE"
    return (
        "Check every claim in the ANSWER against the material below. Return JSON "
        '{"grounded": bool, "unsupported_claims": [string]}. A claim is grounded '
        f"if {sources} supports it. Flag a claim only when it is unsupported — not "
        "merely because it paraphrases or combines several sources.\n\n"
        f"# ANSWER\n{prose}\n{context}\n# EVIDENCE\n{evidence}"
    )
