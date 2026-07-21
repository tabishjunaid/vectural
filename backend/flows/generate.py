"""Tier-4 flow narrative generation (§5.2 tier 4, Sonnet).

Generation is an indexing-side spend (it decrements the shared pool via the
router's accounting like any tier). It is keyed by ``content_hash`` (structural
signature + prompt version) so an unchanged flow is never re-generated — and,
critically, generation is only ever triggered explicitly (initial run or an
architect re-request), **never** as a silent side effect of a code change (§4.4).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from backend.domain.models import Persona, TaskType
from backend.flows.identify import FlowCandidate
from backend.llm.router import LLMRouter

FLOW_PROMPT_VERSION = "flow-v1"


@dataclass
class GeneratedNarrative:
    text: str
    summary: str
    content_hash: str


def content_hash(signature: str, prompt_version: str) -> str:
    return hashlib.sha256(f"{signature}::{prompt_version}".encode()).hexdigest()[:16]


def generate_narrative(
    router: LLMRouter,
    candidate: FlowCandidate,
    *,
    service_summaries: dict[str, str] | None = None,
    prompt_version: str = FLOW_PROMPT_VERSION,
) -> GeneratedNarrative:
    prompt = _render_prompt(candidate, service_summaries or {})
    response = router.route(
        TaskType.FLOW_NARRATIVE,
        prompt_version,
        {"prompt": prompt, "max_tokens": 1500},
        Persona.ARCHITECT,
    )
    parsed = response.parsed or {}
    text = str(parsed.get("narrative", "")).strip() or _fallback_text(candidate)
    summary = str(parsed.get("summary", "")).strip()
    return GeneratedNarrative(
        text=text, summary=summary, content_hash=content_hash(candidate.signature, prompt_version)
    )


def _render_prompt(candidate: FlowCandidate, service_summaries: dict[str, str]) -> str:
    summaries = "\n".join(
        f"- {svc}: {service_summaries.get(svc, '(no service summary yet)')}"
        for svc in candidate.services
    )
    return (
        "Write a concise cross-service business narrative for this flow as JSON "
        '{"narrative": string, "summary": string}. Describe what happens, in order, '
        "in business terms.\n"
        f"# TRIGGER\n{candidate.trigger}\n"
        f"# PATH\n{' -> '.join(candidate.services)}\n"
        f"# SERVICE SUMMARIES\n{summaries}"
    )


def _fallback_text(candidate: FlowCandidate) -> str:
    return f"Flow across {', '.join(candidate.services)} ({candidate.trigger})."
