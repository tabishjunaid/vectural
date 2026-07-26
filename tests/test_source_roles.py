"""Production-vs-test/demo evidence preference (§5.3 step 6)."""

from __future__ import annotations

import pytest

from backend.domain.models import ChunkKind, Language, Span
from backend.retrieval.base import SearchHit
from backend.retrieval.roles import SourceRole, classify, prefer_production, query_wants_tests


def _hit(path: str) -> SearchHit:
    return SearchHit(
        chunk_id=f"svc:{path}:1-2:abc",
        service="svc",
        path=path,
        span=Span(start=1, end=2),
        language=Language.PYTHON,
        kind=ChunkKind.MODULE,
        symbol=None,
        content="x",
        commit_sha="abc",
        score=1.0,
    )


@pytest.mark.parametrize(
    "path",
    [
        "vectural/tests/conftest.py",
        "vectural/tests/test_answer.py",
        "svc/internal/thing_test.go",
        "web/src/components/Button.test.tsx",
        "web/src/components/Button.spec.ts",
        "svc/__tests__/helper.js",
        "svc/e2e/checkout.py",
    ],
)
def test_classifies_test_sources(path: str) -> None:
    assert classify(path) is SourceRole.TEST


@pytest.mark.parametrize(
    "path",
    ["vectural/backend/demo.py", "svc/examples/quickstart.py", "vectural/sample-estate/a.py"],
)
def test_classifies_demo_sources(path: str) -> None:
    assert classify(path) is SourceRole.DEMO


@pytest.mark.parametrize(
    "path",
    [
        "vectural/backend/llm/router.py",
        "vectural/backend/answer/service.py",
        # 'contest'/'latest' must not trip the conftest/test patterns.
        "svc/contest/rules.py",
        "svc/latest/handler.py",
    ],
)
def test_classifies_production(path: str) -> None:
    assert classify(path) is SourceRole.PRODUCTION


def test_production_fills_the_budget_first() -> None:
    """The real case: conftest.py and demo.py out-ranked the implementation for
    'data flow for LLM usage', and the answer described the FakeGatewayClient
    test rig as if it were production."""
    hits = [
        _hit("vectural/tests/conftest.py"),
        _hit("vectural/backend/demo.py"),
        _hit("vectural/backend/llm/router.py"),
        _hit("vectural/backend/llm/factory.py"),
    ]
    out = prefer_production(hits, top_n=2, query="data flow for LLM usage in vectural")
    assert [h.path for h in out] == [
        "vectural/backend/llm/router.py",
        "vectural/backend/llm/factory.py",
    ]


def test_relevance_order_is_preserved_within_each_group() -> None:
    hits = [_hit("a/tests/t.py"), _hit("a/z.py"), _hit("a/b.py")]
    out = prefer_production(hits, top_n=3, query="how does it work")
    # z before b (their relevance order), tests last — not re-sorted alphabetically.
    assert [h.path for h in out] == ["a/z.py", "a/b.py", "a/tests/t.py"]


def test_tests_top_up_when_production_evidence_is_thin() -> None:
    """A preference, not a filter — a thin production result set must not yield a
    short answer when supporting evidence exists."""
    hits = [_hit("a/tests/t1.py"), _hit("a/only.py"), _hit("a/tests/t2.py")]
    out = prefer_production(hits, top_n=3, query="how does it work")
    assert len(out) == 3
    assert out[0].path == "a/only.py"


@pytest.mark.parametrize(
    "query",
    [
        "how is this tested?",
        "show me the conftest fixtures",
        "what does the demo do",
        "which mocks are used for the gateway",
    ],
)
def test_question_about_tests_disables_the_preference(query: str) -> None:
    """When the asker wants the test rig, demoting it is not unhelpful — it is
    wrong."""
    assert query_wants_tests(query)
    hits = [_hit("a/tests/conftest.py"), _hit("a/prod.py")]
    out = prefer_production(hits, top_n=2, query=query)
    assert out[0].path == "a/tests/conftest.py"  # untouched


def test_ordinary_question_does_not_trip_the_escape_hatch() -> None:
    assert not query_wants_tests("how does the quota governor decide the budget")
    assert not query_wants_tests("explain the indexing flow")


@pytest.mark.parametrize(
    "query",
    [
        "what are the specific settings for the gateway",
        "give me the specification of the answer path",
        "how does the estimator specify token counts",
    ],
)
def test_specific_and_specification_do_not_trip_the_hatch(query: str) -> None:
    """`spec\\w*` would swallow these and silently turn the preference off on
    ordinary questions — so the spec stem is matched exactly, not as a prefix."""
    assert not query_wants_tests(query)
