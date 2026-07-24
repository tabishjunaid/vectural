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
# Fenced blocks (```…```) — code and Mermaid diagrams — are illustration, not prose.
# Their bracket syntax (`arr[i]`, Mermaid's `A[Gateway]`, `B{Decision}`) is NOT a
# citation and must never be parsed as one; otherwise every answer containing a
# diagram or a code snippet with an index fails the citation gate and is refused.
_FENCE = re.compile(r"```.*?```", re.DOTALL)


def strip_fenced_blocks(text: str) -> str:
    """Drop ```-fenced code/diagram blocks, leaving the prose the gates act on.

    Claims live in prose and must be cited there; a fenced block only illustrates
    what the prose already states. Removing them keeps their bracket/brace syntax
    from being mistaken for citations and keeps the groundedness judge focused on
    the claims rather than diagram markup."""
    return _FENCE.sub(" ", text)


@dataclass
class CitationResolution:
    resolved: list[Citation]
    unresolved: list[str]
    ok: bool


def extract_markers(text: str) -> list[str]:
    """Citation markers in first-seen order, de-duplicated. Fenced blocks are
    excluded so diagram/code brackets are never read as citations."""
    seen: set[str] = set()
    out: list[str] = []
    for match in _MARKER.finditer(strip_fenced_blocks(text)):
        marker = match.group(1).strip()
        if marker and marker not in seen:
            seen.add(marker)
            out.append(marker)
    return out


def _match_unambiguously(marker: str, retrieved: list[SearchHit]) -> SearchHit | None:
    """Resolve a marker that is an abbreviation of exactly one retrieved chunk_id.

    A chunk_id is ``service:path:lines:hash`` — long, punctuation-heavy, and
    awkward to reproduce verbatim mid-sentence. Models reliably shorten it to the
    distinctive trailing hash, citing ``[ef64c105]`` for
    ``vectural:vectural/plan/design-document.md:1-200:ef64c105``. Exact matching
    alone rejected those, so well-grounded answers were refused for a formatting
    mismatch rather than a truthfulness one.

    Fail-closed is preserved: a marker matching zero or **more than one** chunk
    stays unresolved. Only an unambiguous reference resolves, so this never
    invents an attribution or silently picks between candidates.
    """
    hits = [h for h in retrieved if h.chunk_id.rsplit(":", 1)[-1] == marker]
    if not hits:
        hits = [h for h in retrieved if h.chunk_id.endswith(marker)]
    return hits[0] if len(hits) == 1 else None


def resolve_citations(text: str, retrieved: list[SearchHit]) -> CitationResolution:
    """Resolve every citation marker against the retrieved chunk set.

    ``ok`` is true only if there is at least one citation and **all** citations
    resolve — mandatory citations (§5.4), fail-closed."""
    by_id = {hit.chunk_id: hit for hit in retrieved}
    resolved: list[Citation] = []
    unresolved: list[str] = []

    for marker in extract_markers(text):
        hit = by_id.get(marker) or _match_unambiguously(marker, retrieved)
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
