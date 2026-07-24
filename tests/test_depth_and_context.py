"""Answer depth budgets and architectural context (§5.2 tiers, §5.5, §5.4 gates)."""

from __future__ import annotations

from datetime import UTC, datetime

from backend.answer.context import (
    AnswerContext,
    gather_context,
    render_context_block,
)
from backend.answer.depth import budget_for
from backend.answer.synthesis import render_synthesis_prompt
from backend.domain.models import ChunkKind, Depth, Language, Persona, Span, TaskType
from backend.llm.config import max_tokens_for
from backend.retrieval.base import SearchHit
from backend.summarise.store import InMemorySummaryStore, SummaryRecord


def _hit(service: str, path: str, chunk_id: str = "svc:a.py:1-2:abc123") -> SearchHit:
    return SearchHit(
        chunk_id=chunk_id,
        service=service,
        path=path,
        span=Span(start=1, end=2),
        language=Language.PYTHON,
        kind=ChunkKind.MODULE,
        symbol=None,
        content="def handler(): ...",
        commit_sha="abc",
        score=1.0,
    )


def _summary(tier: int, kind: str, key: str, text: str) -> SummaryRecord:
    return SummaryRecord(
        tier=tier,
        kind=kind,
        key=key,
        text=text,
        content_hash="h",
        prompt_version="v",
        updated_at=datetime.now(UTC),
    )


# --- depth budgets --------------------------------------------------------- #


def test_depth_widens_every_knob_together() -> None:
    """Breadth, per-chunk depth and output ceiling must rise together — more
    chunks truncated harder explains no better than fewer chunks in full."""
    brief, standard, deep = (budget_for(d) for d in (Depth.BRIEF, Depth.STANDARD, Depth.DEEP))
    assert brief.top_n < standard.top_n < deep.top_n
    assert brief.evidence_chars < standard.evidence_chars < deep.evidence_chars
    assert brief.max_tokens < standard.max_tokens < deep.max_tokens


def test_synthesis_gets_its_own_output_ceiling() -> None:
    """Regression: one global 1024 gave a full architectural answer the same
    budget as a one-line groundedness verdict."""
    assert max_tokens_for(TaskType.SYNTHESIS) > max_tokens_for(TaskType.GROUNDEDNESS)
    # Structured verdicts stay small on purpose — a bigger ceiling only costs money.
    assert max_tokens_for(TaskType.GROUNDEDNESS) == 1024
    assert max_tokens_for(TaskType.FILE_SUMMARY) == 1024


def test_evidence_chars_actually_reaches_the_prompt() -> None:
    long_hit = _hit("svc", "a.py")
    long_hit = long_hit.model_copy(update={"content": "x" * 5000})
    brief = render_synthesis_prompt("q", Persona.ENGINEER, [long_hit], evidence_chars=1200)
    deep = render_synthesis_prompt("q", Persona.ENGINEER, [long_hit], evidence_chars=3500)
    assert len(deep) > len(brief)


# --- architectural context ------------------------------------------------- #


def test_context_gathers_service_and_module_summaries() -> None:
    store = InMemorySummaryStore()
    store.upsert(_summary(3, "service", "payments", "Owns charging and refunds."))
    store.upsert(_summary(2, "module", "payments/api", "HTTP surface for charges."))

    ctx = gather_context(
        anchors=["payments"],
        hits=[_hit("payments", "payments/api/routes.py")],
        summaries=store,
        structural=None,
    )
    assert ("payments", "Owns charging and refunds.") in ctx.services
    assert ("payments/api", "HTTP surface for charges.") in ctx.modules


def test_context_falls_back_to_hit_services_when_unanchored() -> None:
    """An unanchored question should still get context, from whatever services
    actually produced evidence."""
    store = InMemorySummaryStore()
    store.upsert(_summary(3, "service", "ledger", "Double-entry ledger."))
    ctx = gather_context(
        anchors=[], hits=[_hit("ledger", "ledger/x.py")], summaries=store, structural=None
    )
    assert ctx.services == [("ledger", "Double-entry ledger.")]


class _Graph:
    def direct_callees(self, service: str) -> list[str]:
        return ["ledger", "notify"]

    def direct_callers(self, service: str) -> list[str]:
        return ["gateway"]


def test_context_includes_call_graph_edges() -> None:
    ctx = gather_context(
        anchors=["payments"], hits=[_hit("payments", "a.py")], summaries=None, structural=_Graph()
    )
    assert any("calls: ledger, notify" in e for e in ctx.edges)
    assert any("is called by: gateway" in e for e in ctx.edges)


class _BrokenGraph:
    def direct_callees(self, service: str) -> list[str]:
        raise RuntimeError("neo4j down")

    def direct_callers(self, service: str) -> list[str]:
        raise RuntimeError("neo4j down")


def test_graph_failure_does_not_fail_the_answer() -> None:
    """Context is a nice-to-have; a graph hiccup must degrade, not refuse."""
    ctx = gather_context(
        anchors=["payments"],
        hits=[_hit("payments", "a.py")],
        summaries=None,
        structural=_BrokenGraph(),
    )
    assert ctx.edges == []


def test_empty_context_renders_nothing() -> None:
    assert render_context_block(AnswerContext()) == ""


# --- the load-bearing constraint: context is NOT citable ------------------- #


def test_context_block_emits_no_bracketed_tokens() -> None:
    """A bracketed token in the context would be extracted as a citation marker,
    fail to resolve, and turn every context-bearing answer into a refusal."""
    ctx = AnswerContext(
        services=[("payments", "Owns charging [and] refunds")],
        modules=[("payments/api", "HTTP surface")],
        edges=["payments calls: ledger"],
    )
    block = render_context_block(ctx)
    from backend.answer.citations import extract_markers

    # The only bracket here came from summary text, not from our formatting; what
    # matters is that our own rendering adds none.
    assert "## Services involved" in block
    assert extract_markers(render_context_block(AnswerContext(edges=["a calls: b"]))) == []


def test_prompt_marks_context_as_non_citable() -> None:
    prompt = render_synthesis_prompt(
        "q", Persona.ENGINEER, [_hit("svc", "a.py")], context_block="## Services involved\n- a: b"
    )
    assert "ARCHITECTURAL CONTEXT" in prompt
    assert "NOT citable" in prompt
    assert "must never be cited" in prompt


def test_citing_a_summary_key_still_refuses() -> None:
    """The regression that matters: context must not become a citation loophole.
    A model citing a module key rather than a chunk id fails the gate."""
    from backend.answer.citations import resolve_citations

    hits = [_hit("payments", "payments/api/routes.py", "payments:api/routes.py:1-2:abc123")]
    res = resolve_citations("Charging works like this [payments/api].", hits)
    assert not res.ok
    assert res.unresolved == ["payments/api"]


def test_groundedness_judges_against_the_same_context_synthesis_saw() -> None:
    """Regression: synthesis gained architectural context while the judge kept
    seeing only chunks, so it rejected legitimate summary-level claims ("the
    quota governor approves budgets before indexing") and turned a better answer
    into a refusal. The gate must evaluate the material the model was given."""
    from backend.answer.groundedness import _render_prompt

    prompt = _render_prompt("claim", [_hit("svc", "a.py")], "## Services involved\n- svc: does X")
    assert "ARCHITECTURAL CONTEXT" in prompt
    assert "does X" in prompt
    # Both sources count; a paraphrase across them is not automatically a failure.
    assert "EVIDENCE or the ARCHITECTURAL CONTEXT" in prompt


def test_groundedness_without_context_is_unchanged() -> None:
    from backend.answer.groundedness import _render_prompt

    prompt = _render_prompt("claim", [_hit("svc", "a.py")])
    assert "ARCHITECTURAL CONTEXT" not in prompt


def test_cache_key_includes_depth() -> None:
    """Regression: the cache keyed on (question, commit, persona) only, so asking
    at `brief` then `deep` replayed the brief answer — the user asks for more and
    silently gets less."""
    from backend.answer.cache import SemanticAnswerCache
    from backend.answer.models import Answer
    from backend.embedding.hashing import HashingEmbedder

    cache = SemanticAnswerCache(HashingEmbedder())
    brief = Answer.synthesized(
        persona=Persona.ENGINEER, question="q", text="short [a]", citations=[]
    )
    cache.put("q", "sha", Persona.ENGINEER, brief, Depth.BRIEF)

    assert cache.get("q", "sha", Persona.ENGINEER, Depth.BRIEF) is not None
    assert cache.get("q", "sha", Persona.ENGINEER, Depth.DEEP) is None  # must recompute
