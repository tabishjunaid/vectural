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
    res = resolve_citations("a claim [ghost]", [_hit("id1")])
    assert not res.ok
    assert res.unresolved == ["ghost"]


def test_no_citation_fails_closed() -> None:
    # An answer with no citations is not releasable (mandatory citations, §5.4).
    res = resolve_citations("an unsupported claim with no citation", [_hit("id1")])
    assert not res.ok
    assert res.resolved == []


def test_partial_resolution_fails() -> None:
    res = resolve_citations("[id1] good but [ghost] bad", [_hit("id1")])
    assert not res.ok  # one unresolved -> whole answer withheld
