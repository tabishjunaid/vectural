"""Answer synthesis (§5.4): persona-templated, citations mandatory.

Renders the synthesis prompt from the question, the persona instruction, and the
reranked evidence chunks — each listed with its ``chunk_id`` so the model can
(and must) cite by id. The router sends this to Sonnet in prose mode. Nothing
here decides whether the answer is releasable; the two gates downstream do (R1).
"""

from __future__ import annotations

from backend.answer.personas import persona_instruction
from backend.domain.models import Persona, TaskType
from backend.llm.base import RoutedResponse
from backend.llm.router import LLMRouter
from backend.retrieval.base import SearchHit

SYNTHESIS_PROMPT_VERSION = "synth-v1"


def render_synthesis_prompt(question: str, persona: Persona, chunks: list[SearchHit]) -> str:
    evidence_lines = [
        f"- [{c.chunk_id}] {c.path}:{c.span} ({c.symbol or c.kind.value})\n"
        f"  {_first_line(c.content)}"
        for c in chunks
    ]
    evidence = "\n".join(evidence_lines) if evidence_lines else "(no evidence retrieved)"
    return (
        f"{persona_instruction(persona)}\n\n"
        "Answer the question using ONLY the evidence below. Every claim-bearing "
        "sentence must cite the specific evidence it rests on by putting the "
        "evidence id in square brackets, e.g. [id]. Do not cite ids that are not "
        "listed. If the evidence does not support an answer, say so.\n\n"
        f"# QUESTION\n{question}\n\n# EVIDENCE (cite by id)\n{evidence}"
    )


def synthesise(
    router: LLMRouter,
    *,
    question: str,
    persona: Persona,
    chunks: list[SearchHit],
    prompt_version: str = SYNTHESIS_PROMPT_VERSION,
) -> RoutedResponse:
    prompt = render_synthesis_prompt(question, persona, chunks)
    return router.route(TaskType.SYNTHESIS, prompt_version, {"prompt": prompt}, persona)


def _first_line(content: str) -> str:
    line = content.strip().splitlines()[0] if content.strip() else ""
    return line[:120]
