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


# A chunk_id hash is hex; the full id is `service:path:lines:hash` (has colons).
_HASHLIKE = re.compile(r"[0-9a-fA-F]{6,}")


def _looks_like_citation(marker: str) -> bool:
    """Whether a bracketed token is plausibly a citation *attempt*.

    The answer prose can legitimately contain bracketed words that are NOT
    citations — most sharply when the answer *describes the citation mechanism
    itself* and writes a literal ``[chunk_id]`` or ``[id]``. Treating those as
    failed citations refuses an otherwise well-cited answer (gpt-5 hit exactly
    this on "how does Vectural work"). A real citation is either the full id
    (contains ``:``) or the distinctive trailing hash (hex); a plain word is
    neither. Only *attempts* that fail to resolve fail the gate — this never lets
    an id/path/hash-shaped hallucination through, and the groundedness gate still
    runs. A chunk_id fragment carries a ``:`` (full id) or ``/`` (a path/module
    key) or is the bare hex hash; a plain word carries none of those.

    A chunk_id and its hash never contain WHITESPACE — but bracketed prose does.
    Verbose models (gpt-5 at DEEP) write emphasis/labels in brackets like
    ``[finalize: upsert shared nodes + cross-service edges]`` or
    ``[(Postgres: file_ledger, quota, etc.)]``; the colon there is punctuation, not
    a citation, so a marker containing whitespace is never a citation attempt. This
    stays fail-closed for real ids (a fabricated ``svc:path:1-2:hash`` has no
    spaces and is still caught)."""
    if any(ch.isspace() for ch in marker):
        return False
    return ":" in marker or "/" in marker or bool(_HASHLIKE.fullmatch(marker))


def _match_unambiguously(marker: str, retrieved: list[SearchHit]) -> SearchHit | None:
    """Resolve a marker that is an abbreviation of exactly one retrieved chunk_id.

    A chunk_id is ``service:path:lines:hash`` — long, punctuation-heavy, and
    awkward to reproduce verbatim mid-sentence. Models reliably shorten it to the
    distinctive trailing hash, citing ``[ef64c105]`` for
    ``vectural:vectural/plan/design-document.md:1-200:ef64c105``. Exact matching
    alone rejected those, so well-grounded answers were refused for a formatting
    mismatch rather than a truthfulness one.

    Models also reconstruct the *full* id from memory and get the path or line
    range slightly wrong (``vectural/orchestration/starter.py:1-18`` for the real
    ``vectural/backend/orchestration/starter.py:1-20``) while reproducing the
    distinctive trailing hash correctly. So a marker that is itself a full/partial
    id falls back to matching on its own trailing hash — the content-addressed
    part that actually identifies the chunk.

    Hash-based resolution stays strict — a hash is specific, so exactly one chunk
    matches or the reference is genuinely ambiguous. Path/file-level resolution is
    deliberately not: see below.
    """
    hits = [h for h in retrieved if h.chunk_id.rsplit(":", 1)[-1] == marker]
    if not hits:
        hits = [h for h in retrieved if h.chunk_id.endswith(marker)]
    if not hits and ":" in marker:
        # A full/partial id whose path/lines drifted: match on its trailing hash.
        tail = marker.rsplit(":", 1)[-1]
        hits = [h for h in retrieved if h.chunk_id.rsplit(":", 1)[-1] == tail]
    if hits:
        return hits[0] if len(hits) == 1 else None
    # File/module-level citation: models routinely cite a file by path — a bare
    # `backend/graph/schema.py` (gpt-5) or a `service:path:symbol` like
    # `vectural:…/opensearch_backend.py:connect` (qwen) — rather than reproducing
    # the exact chunk id. A *relevant* file usually has SEVERAL retrieved chunks, so
    # requiring exactly one match refused these as "ambiguous" even though the file's
    # evidence was retrieved. If the cited path matches one or more retrieved chunks,
    # the claim rests on that file's retrieved evidence — resolve to the best-scored
    # one (groundedness still re-checks the claim against it). Fail-closed intact: a
    # path matching NO retrieved chunk (an un-retrieved or hallucinated file) resolves
    # to nothing and the gate refuses.
    if "/" in marker:
        path = next((seg for seg in marker.split(":") if "/" in seg), marker)
        candidates = [h for h in retrieved if path in h.chunk_id]
        if candidates:
            return max(candidates, key=lambda h: h.score)
    return None


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
            # Only a genuine citation attempt (id/hash shape) that fails to resolve
            # fails the gate; a bracketed plain word in prose (e.g. the answer
            # describing the "[chunk_id]" mechanism) is not a citation — leave it be.
            if _looks_like_citation(marker):
                unresolved.append(marker)
            continue
        resolved.append(
            Citation(
                index=len(resolved) + 1,
                chunk_id=hit.chunk_id,
                # Record the marker as written, not the id it resolved to: the
                # client rewrites `[marker]` → `[n]` to render a chip, and the two
                # differ whenever the model abbreviated.
                marker=marker,
                service=hit.service,
                path=hit.path,
                span=hit.span,
            )
        )

    ok = bool(resolved) and not unresolved
    return CitationResolution(resolved=resolved, unresolved=unresolved, ok=ok)
