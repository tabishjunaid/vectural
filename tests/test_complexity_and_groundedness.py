"""Query-complexity assessment + the proportional groundedness gate.

Two cost/quality fixes for the answer path:
- ``assess_complexity`` shapes the explanation from cheap, deterministic signals.
- the groundedness gate reads the *cited*, *truncated* evidence — the same
  material synthesis saw — not the full retrieved set at full length.
"""

from __future__ import annotations

from backend.answer.complexity import assess_complexity
from backend.answer.groundedness import _render_prompt
from backend.answer.plan import RetrievalPlan
from backend.domain.models import ChunkKind, Complexity, Language, Span
from backend.retrieval.base import SearchHit


def _plan(anchors: list[str], *, scope: set[str] | None = None, used_fallback: bool = False,
          cypher_attempts: int = 1) -> RetrievalPlan:
    return RetrievalPlan(
        anchors=anchors,
        scope=scope if scope is not None else (set(anchors) or None),
        cypher=None,
        used_fallback=used_fallback,
        cypher_attempts=cypher_attempts,
    )


def _hit(chunk_id: str, content: str) -> SearchHit:
    return SearchHit(
        chunk_id=chunk_id,
        service="svc",
        path="svc/a.py",
        span=Span(start=1, end=2),
        language=Language.PYTHON,
        kind=ChunkKind.MODULE,
        symbol=None,
        content=content,
        commit_sha="abc",
        score=1.0,
    )


def test_complexity_simple_lookup() -> None:
    # A terse factual lookup naming no service — the concise-answer case.
    assert assess_complexity("which project uses Neo4J", _plan([]), []) is Complexity.SIMPLE


def test_complexity_complex_cross_service_flow() -> None:
    # Relational phrasing ("how … via … and") spanning three services.
    c = assess_complexity(
        "how does the gateway charge via payments and ledger",
        _plan(["gateway", "payments", "ledger"]),
        [],
    )
    assert c is Complexity.COMPLEX


def test_complexity_moderate_single_mechanism() -> None:
    # One relational word, single anchor, mid-length — neither trivial nor broad.
    c = assess_complexity(
        "how does the retrieval service rerank candidates",
        _plan(["retrieval"]),
        [],
    )
    assert c is Complexity.MODERATE


def test_complexity_is_pure() -> None:
    q, p, h = "how do payments and ledger interact", _plan(["payments", "ledger"]), []
    assert assess_complexity(q, p, h) is assess_complexity(q, p, h)


def test_groundedness_prompt_truncates_to_budget() -> None:
    # The gate must not read a chunk at full length when synthesis truncated it —
    # feed a long body with a small budget and assert it was cut (marker present).
    long_body = "X" * 5000
    prompt = _render_prompt("The answer. [svc:a.py:1-2:aaa]", [_hit("svc:a.py:1-2:aaa", long_body)],
                            "", 100)
    assert "… (truncated)" in prompt
    assert prompt.count("X") <= 200  # ~budget, nowhere near the full 5000


def test_groundedness_prompt_contains_only_the_chunks_it_is_given() -> None:
    # Caller scopes to cited chunks; the gate renders exactly those, no others.
    cited = _hit("svc:a.py:1-2:cited", "def a(): ...")
    prompt = _render_prompt("Ans [svc:a.py:1-2:cited]", [cited], "", 1500)
    assert "svc:a.py:1-2:cited" in prompt
    assert "uncited" not in prompt  # an unrelated retrieved id never leaks in
