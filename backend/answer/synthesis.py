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

SYNTHESIS_PROMPT_VERSION = "synth-v3"

# How much of each retrieved chunk to put in front of the model.
#
# This was previously the chunk's *first line*, capped at 120 chars. That is not
# evidence: the model saw a filename and one truncated line, correctly reported
# that it could not answer, cited nothing, and the mandatory-citation gate (§5.4)
# turned every question into a refusal. The retrieval stack was working the whole
# time — its output simply never reached the model.
#
# Bounded per chunk, because the synthesis prompt is billed on every question and
# a chunk can be arbitrarily long. ~1500 chars ≈ 400 tokens; at the default top_n
# that is a few thousand tokens of evidence, which is what an answer needs.
EVIDENCE_CHARS_PER_CHUNK = 1500


def render_synthesis_prompt(
    question: str,
    persona: Persona,
    chunks: list[SearchHit],
    *,
    context_block: str = "",
    evidence_chars: int = EVIDENCE_CHARS_PER_CHUNK,
) -> str:
    evidence_lines = [
        f"- [{c.chunk_id}] {c.path}:{c.span} ({c.symbol or c.kind.value})\n"
        f"{_evidence_body(c.content, limit=evidence_chars)}"
        for c in chunks
    ]
    evidence = "\n".join(evidence_lines) if evidence_lines else "(no evidence retrieved)"
    # Context (service/module summaries + call-graph edges) orients the model
    # before it reads fragments. It is NOT citable — it has no chunk ids — so the
    # instruction below is load-bearing, not decoration.
    context = (
        f"\n# ARCHITECTURAL CONTEXT (background only — NOT citable)\n{context_block}\n"
        if context_block
        else ""
    )
    return (
        f"{persona_instruction(persona)}\n\n"
        "Answer the question using ONLY the evidence below, in this structure:\n"
        "1. **Summary** — one or two sentences answering the question directly.\n"
        "2. **Diagram** — IF the question is about structure, flow, relationships, "
        "architecture, or how components connect, include ONE Mermaid diagram in a "
        "```mermaid fenced block (use `flowchart TD` for structure/dependencies or "
        "`sequenceDiagram` for a flow over time). Draw ONLY relationships that the "
        "evidence supports and that you also state in prose. Keep node labels short "
        "and put NO citation markers inside the diagram. Omit the diagram entirely "
        "for a purely factual or definitional question — never invent one to fill "
        "space.\n"
        "3. **Details** — a thorough explanation. Cover, where the evidence "
        "supports it: the mechanism (what actually happens, in order); the "
        "components involved and their responsibilities; where the behaviour is "
        "configured or wired; and any caveats, limits or failure modes. Prefer "
        "specifics — names of functions, files, settings — over general statements. "
        "Do not pad: if the evidence only supports a short answer, give a short "
        "answer.\n\n"
        "Citations: every claim-bearing sentence in the Summary and Details must "
        "cite the evidence it rests on with the evidence id in square brackets, e.g. "
        "[id]. Do not cite ids that are not listed. The diagram is illustration, so "
        "it carries no citations and must not be the only place a relationship "
        "appears — state it in prose too. If the evidence does not support an "
        "answer, say so plainly.\n"
        "The ARCHITECTURAL CONTEXT section, when present, is background to help you "
        "frame the answer — it has no ids and must never be cited. Every claim still "
        "rests on an EVIDENCE id.\n"
        f"{context}\n"
        f"# QUESTION\n{question}\n\n# EVIDENCE (cite by id)\n{evidence}"
    )


def synthesise(
    router: LLMRouter,
    *,
    question: str,
    persona: Persona,
    chunks: list[SearchHit],
    context_block: str = "",
    evidence_chars: int = EVIDENCE_CHARS_PER_CHUNK,
    max_tokens: int | None = None,
    prompt_version: str = SYNTHESIS_PROMPT_VERSION,
) -> RoutedResponse:
    prompt = render_synthesis_prompt(
        question, persona, chunks, context_block=context_block, evidence_chars=evidence_chars
    )
    payload: dict[str, object] = {"prompt": prompt}
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    return router.route(TaskType.SYNTHESIS, prompt_version, payload, persona)


def _evidence_body(content: str, *, limit: int = EVIDENCE_CHARS_PER_CHUNK) -> str:
    """The chunk's text, indented under its id and truncated to a token budget.

    Indented so the model can tell where one piece of evidence ends and the next
    id begins; truncation is marked so it never reads a cut-off chunk as the
    complete picture and cites it for something that is not there."""
    body = content.strip()
    if not body:
        return "    (empty)"
    truncated = body[:limit]
    if len(body) > limit:
        truncated += "\n… (truncated)"
    return "\n".join(f"    {line}" for line in truncated.splitlines())
