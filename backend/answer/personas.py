"""Persona answer templates (R6, §5.1: persona threaded through synthesis).

Persona sets the *altitude* of the answer — the same evidence is described at
different levels for an engineer vs. a business owner. The instruction is
prepended to the synthesis prompt; the router already threads the persona for
accounting, this threads it for content.
"""

from __future__ import annotations

from backend.domain.models import Persona

_INSTRUCTIONS: dict[Persona, str] = {
    Persona.ENGINEER: (
        "Answer for a software engineer. Be precise and technical: name the "
        "functions, files, and events involved, and describe the mechanism. "
        "Include code paths where they clarify."
    ),
    Persona.PRODUCT_OWNER: (
        "Answer for a product owner. Describe the capabilities and the flow "
        "between them in plain language. Avoid code and low-level detail; focus "
        "on what happens and in what order."
    ),
    Persona.BUSINESS_OWNER: (
        "Answer for a business owner. Explain the outcome and its impact in plain "
        "language, in two or three sentences. No code, no service names unless "
        "essential."
    ),
    Persona.ARCHITECT: (
        "Answer for an architect. Emphasise cross-service boundaries, data flow, "
        "coupling, and any review or quality caveats. Note where a claim rests on "
        "a reviewed flow narrative versus live reconstruction."
    ),
}


def persona_instruction(persona: Persona) -> str:
    return _INSTRUCTIONS[persona]
