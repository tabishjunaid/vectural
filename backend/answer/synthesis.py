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

SYNTHESIS_PROMPT_VERSION = "synth-v5"

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
        "Write a thorough, explanatory answer grounded ONLY in the evidence below. "
        "Aim for the clear, generous explanation a good engineer gives a new "
        "teammate: don't just list what exists — explain how the pieces fit "
        "together and why it is built the way it is. Use the depth the evidence "
        "supports; a well-covered question deserves a full walkthrough, not a "
        "summary.\n\n"
        "FORMAT — write each section as a level-2 markdown heading, exactly "
        "`## <Name>`, using these names verbatim and in this order (the UI splits "
        "the answer on these headings, so the `## ` prefix and the names are a "
        "hard contract):\n"
        "## Summary — a short orienting paragraph (2-4 sentences) that answers the "
        "question directly and frames what the rest of the answer covers.\n"
        "## Diagram — IF the question is about structure, flow, relationships, "
        "architecture, or how components connect, include ONE Mermaid diagram in a "
        "```mermaid fenced block (use `flowchart TD` for structure/dependencies or "
        "`sequenceDiagram` for a flow over time). Draw ONLY relationships the "
        "evidence supports and that you also state in prose. Keep node labels short "
        "and put NO citation markers inside the diagram. Omit this whole section "
        "for a purely factual or definitional question — never invent a diagram to "
        "fill space.\n"
        "## How it works — the mechanism as an ordered walkthrough. Use a numbered "
        "list, one step per thing that actually happens, naming the function or "
        "file that does it AND explaining, in a sentence or two per step, what that "
        "step accomplishes and how it hands off to the next. Do not collapse the "
        "sequence into a single alluding sentence.\n"
        "## Key components — one bullet per component that matters: what it is, "
        "what it is responsible for, where it lives, and how it relates to the "
        "others. A bullet may run to several sentences when the component warrants "
        "it.\n"
        "## Configuration & wiring — where the behaviour is set or assembled: "
        "settings, environment variables, factories, defaults and their values, "
        "and what each one changes.\n"
        "## Caveats & failure modes — limits, error paths, what happens when a "
        "step fails, and anything deliberately not handled.\n\n"
        "Each section is its own `## ` heading with its own content. Omit a section "
        "the evidence genuinely cannot support — but never merge two sections, and "
        "never replace one with a single summarising sentence. Prefer specifics "
        "(function names, file paths, setting names, numbers) over vague statements, "
        "and where you compare options or map one thing to another, a markdown "
        "table is welcome.\n"
        "Be as thorough as the evidence allows: do not invent detail the evidence "
        "does not contain, but do not hold back either — if the evidence supports "
        "six steps and their rationale, write all six and explain them.\n\n"
        "Citations: every claim-bearing sentence in the prose sections must cite "
        "the evidence it rests on with the evidence id in square brackets, e.g. "
        "[id]. Do not cite ids that are not listed. Connective or framing sentences "
        "that only explain how things relate may lean on the ARCHITECTURAL CONTEXT, "
        "but every concrete factual claim rests on an EVIDENCE id. The diagram is "
        "illustration, so it carries no citations and must not be the only place a "
        "relationship appears — state it in prose too. If the evidence does not "
        "support an answer, say so plainly.\n"
        "The ARCHITECTURAL CONTEXT section, when present, is background to help you "
        "frame and connect the answer — it has no ids and must never be cited.\n"
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
