"""Citation resolution — the first R1 gate (§5.3 citation contract, §5.4).

**Deterministic code, not a model call.** It verifies that every citation marker
in the answer resolves to a chunk that was actually passed to synthesis. It runs
*before* the groundedness check because it is cheaper and stricter — a malformed
citation never spends a second gateway call. An answer with no citations, or with
any unresolved citation, does not pass: the platform has no code path that shows
an unverifiable claim as fact.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from backend.answer.models import Citation
from backend.retrieval.base import SearchHit

# Citation markers are the chunk_id in square brackets, e.g. "[payments:…:a1b2]".
_MARKER = re.compile(r"\[([^\[\]]+)\]")


@dataclass
class CitationResolution:
    resolved: list[Citation]
    unresolved: list[str]
    ok: bool


def extract_markers(text: str) -> list[str]:
    """Citation markers in first-seen order, de-duplicated."""
    seen: set[str] = set()
    out: list[str] = []
    for match in _MARKER.finditer(text):
        marker = match.group(1).strip()
        if marker and marker not in seen:
            seen.add(marker)
            out.append(marker)
    return out


def resolve_citations(text: str, retrieved: list[SearchHit]) -> CitationResolution:
    """Resolve every citation marker against the retrieved chunk set.

    ``ok`` is true only if there is at least one citation and **all** citations
    resolve — mandatory citations (§5.4), fail-closed."""
    by_id = {hit.chunk_id: hit for hit in retrieved}
    resolved: list[Citation] = []
    unresolved: list[str] = []

    for marker in extract_markers(text):
        hit = by_id.get(marker)
        if hit is None:
            unresolved.append(marker)
            continue
        resolved.append(
            Citation(
                index=len(resolved) + 1,
                chunk_id=hit.chunk_id,
                service=hit.service,
                path=hit.path,
                span=hit.span,
            )
        )

    ok = bool(resolved) and not unresolved
    return CitationResolution(resolved=resolved, unresolved=unresolved, ok=ok)
