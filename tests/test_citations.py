"""Deterministic citation resolution — R1 gate 1 (§5.3)."""

from __future__ import annotations

from backend.answer.citations import extract_markers, resolve_citations
from backend.domain.models import ChunkKind, Language, Span
from backend.retrieval.base import SearchHit


def _hit(chunk_id: str, service: str = "s", path: str = "s/f.py") -> SearchHit:
    return SearchHit(
        chunk_id=chunk_id, service=service, path=path, span=Span(start=1, end=3),
        language=Language.PYTHON, kind=ChunkKind.FUNCTION, symbol="f", content="x",
        commit_sha="c", score=1.0,
    )


def test_extract_markers_dedup_in_order() -> None:
    assert extract_markers("a [x] b [y] c [x]") == ["x", "y"]


def test_all_citations_resolve() -> None:
    hits = [_hit("id1"), _hit("id2")]
    res = resolve_citations("claim one [id1] and claim two [id2]", hits)
    assert res.ok
    assert [c.chunk_id for c in res.resolved] == ["id1", "id2"]
    assert res.resolved[0].index == 1 and res.resolved[1].index == 2


def test_unresolved_citation_fails_closed() -> None:
    # A citation-shaped marker (hex hash) that resolves to nothing fails closed.
    res = resolve_citations("a claim [deadbeef]", [_hit("id1")])
    assert not res.ok
    assert res.unresolved == ["deadbeef"]


def test_bracketed_prose_word_is_not_treated_as_a_citation() -> None:
    """Regression (gpt-5, 'how does Vectural work'): the answer *described* the
    citation mechanism and wrote the literal `[chunk_id]`. That plain word is not
    a citation attempt (no `:` / `/` / hex hash), so it must not fail an answer
    whose real citations all resolve."""
    hits = [_hit("svc:f.py:1-3:ef64c105")]
    res = resolve_citations(
        "Markers look like [chunk_id]; this claim rests on [ef64c105].", hits
    )
    assert res.ok
    assert res.unresolved == []
    assert [c.marker for c in res.resolved] == ["ef64c105"]


def test_bracketed_prose_with_a_colon_is_not_a_citation() -> None:
    """Regression (gpt-5 at DEEP): the model uses square brackets for prose
    emphasis/labels — `[finalize: upsert shared nodes + cross-service edges]`,
    `[(Postgres: file_ledger, quota, etc.)]`. Those contain a colon but are NOT
    citations (a chunk_id/hash never contains whitespace). They must not fail an
    answer whose real hash citations all resolve — else a verbose model that cites
    21 chunks correctly is refused over two bracketed phrases."""
    hits = [_hit("svc:f.py:1-3:b55297ee")]
    text = (
        "The worker runs [finalize: upsert shared nodes + cross-service edges; "
        "generate flows] and writes ledgers [(Postgres: file_ledger, quota, etc.)]. "
        "This is grounded in [b55297ee]."
    )
    res = resolve_citations(text, hits)
    assert res.ok
    assert res.unresolved == []
    assert [c.marker for c in res.resolved] == ["b55297ee"]


def test_reconstructed_full_id_resolves_on_its_trailing_hash() -> None:
    """Regression (gpt-5-mini): the model rebuilt the full id from memory with the
    path/line-range wrong but the distinctive trailing hash right. It must resolve
    to the retrieved chunk via that hash rather than being refused."""
    hits = [_hit("vectural:vectural/backend/orchestration/starter.py:1-20:ad1ca595")]
    # Wrong path (no /backend/) and wrong lines (1-18), correct hash.
    wrong = "vectural:vectural/orchestration/starter.py:1-18:ad1ca595"
    res = resolve_citations(f"The starter computes the partition [{wrong}].", hits)
    assert res.ok
    assert res.resolved[0].chunk_id == hits[0].chunk_id


def test_placeholder_id_resolves_on_its_path_segment() -> None:
    """Regression (gpt-5-mini): the model cited a real file with a `...` placeholder
    for the lines/hash (`[vectural:vectural/RUNBOOK.md:...]`). Resolve it to the one
    retrieved chunk from that file rather than refusing a good answer."""
    hits = [_hit("vectural:vectural/RUNBOOK.md:1-40:aa11bb22", path="vectural/RUNBOOK.md")]
    res = resolve_citations("The runbook says so [vectural:vectural/RUNBOOK.md:...].", hits)
    assert res.ok
    assert res.resolved[0].chunk_id == hits[0].chunk_id


def test_placeholder_id_for_unretrieved_file_still_fails() -> None:
    # A file that was not retrieved matches nothing → fail closed.
    hits = [_hit("vectural:vectural/other.py:1-2:aa11bb22", path="vectural/other.py")]
    res = resolve_citations("claim [vectural:vectural/RUNBOOK.md:...].", hits)
    assert not res.ok


def test_fully_hallucinated_id_still_fails() -> None:
    # Neither the hash nor the path matches any retrieved chunk → fail closed.
    hits = [_hit("svc:svc/real.py:1-2:abc123", path="svc/real.py")]
    res = resolve_citations("claim [svc:svc/ghost.py:9-9:deadbeef].", hits)
    assert not res.ok
    assert res.unresolved == ["svc:svc/ghost.py:9-9:deadbeef"]


def test_no_citation_fails_closed() -> None:
    # An answer with no citations is not releasable (mandatory citations, §5.4).
    res = resolve_citations("an unsupported claim with no citation", [_hit("id1")])
    assert not res.ok
    assert res.resolved == []


def test_partial_resolution_fails() -> None:
    # id1 resolves, but the hex-shaped [deadbeef] is a citation attempt that does
    # not — one unresolved citation withholds the whole answer.
    res = resolve_citations("[id1] good but [deadbeef] bad", [_hit("id1")])
    assert not res.ok
