"""Citation markers survive to the client, and follow-up suggestions (§5.5)."""

from __future__ import annotations

from backend.answer.citations import resolve_citations
from backend.answer.context import AnswerContext
from backend.answer.followups import MAX_FOLLOW_UPS, suggest_followups
from backend.answer.models import Answer, Citation
from backend.domain.models import ChunkKind, Language, Persona, Span
from backend.retrieval.base import SearchHit

CID = "vectural:vectural/backend/orchestration/starter.py:1-20:ad1ca595"


def _hit(chunk_id: str = CID, service: str = "vectural") -> SearchHit:
    return SearchHit(
        chunk_id=chunk_id,
        service=service,
        path=chunk_id.split(":")[1] if ":" in chunk_id else "a.py",
        span=Span(start=1, end=2),
        language=Language.PYTHON,
        kind=ChunkKind.MODULE,
        symbol=None,
        content="x",
        commit_sha="abc",
        score=1.0,
    )


# --- citation markers ------------------------------------------------------ #


def test_marker_records_the_abbreviation_the_model_wrote() -> None:
    """The regression: the model cites [ad1ca595], the resolver accepts it, but
    the client rewrites `[chunk_id]` → `[n]` and so finds nothing to replace.
    Without the marker recorded, the citation renders as dead text."""
    res = resolve_citations("The starter computes the partition [ad1ca595].", [_hit()])
    assert res.ok
    assert res.resolved[0].marker == "ad1ca595"
    assert res.resolved[0].chunk_id == CID


def test_marker_records_the_full_id_when_that_is_what_was_written() -> None:
    res = resolve_citations(f"claim [{CID}].", [_hit()])
    assert res.ok
    assert res.resolved[0].marker == CID


def test_every_resolved_marker_appears_verbatim_in_the_text() -> None:
    """The client substitutes on `[marker]`, so a marker that is not literally in
    the text would leave the citation unrendered — exactly the original bug."""
    text = f"One [ad1ca595] and two [{CID}]."
    hit_b = _hit("vectural:vectural/backend/llm/router.py:1-9:b387fa49")
    res = resolve_citations(text, [_hit(), hit_b])
    for c in res.resolved:
        assert f"[{c.marker}]" in text


# --- follow-up suggestions ------------------------------------------------- #


def _ctx() -> AnswerContext:
    return AnswerContext(
        services=[("payments", "Owns charging."), ("ledger", "Double-entry ledger.")],
        modules=[("payments/api", "HTTP surface.")],
        callees={"payments": ["ledger", "notify"]},
        callers={"ledger": ["payments"]},
    )


def test_suggestions_are_grounded_in_real_graph_edges() -> None:
    out = suggest_followups(_ctx(), [], "what does payments do")
    assert "How does payments interact with ledger?" in out
    assert all(isinstance(q, str) and q.endswith("?") for q in out)


def test_no_context_yields_no_invented_suggestions() -> None:
    """Better nothing than a suggestion the estate cannot answer."""
    assert suggest_followups(AnswerContext(), [], "anything") == []


def test_capped_and_deduped() -> None:
    ctx = AnswerContext(
        services=[(f"svc{i}", "d") for i in range(6)],
        callees={f"svc{i}": ["other"] for i in range(6)},
        callers={f"svc{i}": ["caller"] for i in range(6)},
    )
    out = suggest_followups(ctx, [], "q")
    assert len(out) <= MAX_FOLLOW_UPS
    assert len(out) == len(set(out))


def test_does_not_suggest_the_question_just_asked() -> None:
    out = suggest_followups(_ctx(), [], "What does payments depend on?")
    assert "What does payments depend on?" not in out
    assert out, "still offers other directions"


def test_falls_back_to_cited_services_when_context_is_thin() -> None:
    citations = [
        Citation(index=1, chunk_id=CID, marker="ad1ca595", service="vectural",
                 path="a.py", span=Span(start=1, end=2))
    ]
    out = suggest_followups(AnswerContext(), citations, "q")
    assert out == ["What does vectural depend on?"]


def test_refusal_carries_follow_ups() -> None:
    """A refusal is where a reader most needs to know what they *can* ask."""
    answer = Answer.refusal(
        persona=Persona.ENGINEER,
        question="q",
        reason="no coverage",
        likely_services=["payments"],
        follow_ups=["What does payments depend on?"],
    )
    assert answer.follow_ups == ["What does payments depend on?"]


def test_likely_services_seed_suggestions_when_nothing_was_retrieved() -> None:
    """The empty-retrieval refusal has no context at all, but the planner's
    anchors still say where to look."""
    out = suggest_followups(AnswerContext(), [], "q", likely_services=["payments"])
    assert out == ["What does payments depend on?"]
