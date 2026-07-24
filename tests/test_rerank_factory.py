"""Rerank-stage selection (§5.3 step 6)."""

from __future__ import annotations

import pytest

from backend.config import Settings
from backend.retrieval.base import SearchHit
from backend.retrieval.rerank import NoopReranker, TokenOverlapReranker
from backend.retrieval.rerank_factory import build_reranker


def test_default_is_the_offline_stand_in() -> None:
    # Assert the declared default, not `Settings()` — that reads the developer's
    # .env, so a local VECTURAL_RERANKER would silently decide this test.
    assert Settings.model_fields["reranker"].default == "token-overlap"
    assert isinstance(build_reranker(None), TokenOverlapReranker)
    assert isinstance(build_reranker(Settings(reranker="token-overlap")), TokenOverlapReranker)


def test_noop_selectable_as_the_eval_control_arm() -> None:
    assert isinstance(build_reranker(Settings(reranker="noop")), NoopReranker)


def test_name_is_case_and_space_tolerant() -> None:
    assert isinstance(build_reranker(Settings(reranker="  NoOp ")), NoopReranker)


def test_unknown_name_degrades_rather_than_raising() -> None:
    """Unlike the gateway, a wrong reranker yields worse ordering, never a wrong
    answer — so it must not take the serving path down."""
    assert isinstance(build_reranker(Settings(reranker="bge-v9-typo")), TokenOverlapReranker)


def test_bge_falls_back_to_noop_not_token_overlap(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the cross-encoder cannot load, keep the fused order. Falling back to
    token-overlap would be worse than doing nothing: it re-sorts a good dense
    ranking by literal overlap, which is the bug this whole change fixes."""
    import backend.retrieval.bge_rerank as bge

    def boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("model not in offline cache")

    monkeypatch.setattr(bge, "BgeReranker", boom)
    assert isinstance(build_reranker(Settings(reranker="bge")), NoopReranker)


def _hit(chunk_id: str, content: str) -> SearchHit:
    from backend.domain.models import ChunkKind, Language, Span

    return SearchHit(
        chunk_id=chunk_id,
        service="svc",
        path=f"{chunk_id}.py",
        span=Span(start=1, end=2),
        language=Language.PYTHON,
        kind=ChunkKind.MODULE,
        symbol=None,
        content=content,
        commit_sha="abc",
        score=1.0,
    )


def test_noop_preserves_fused_order() -> None:
    hits = [_hit("a", "alpha"), _hit("b", "beta"), _hit("c", "gamma")]
    out = NoopReranker().rerank("anything", hits, top_n=2)
    assert [h.chunk_id for h in out] == ["a", "b"]


def test_token_overlap_overrides_fused_order() -> None:
    """Pins the behaviour that motivated the change: the stand-in ignores the
    retrieval score entirely, so a last-placed literal match wins."""
    hits = [_hit("a", "unrelated text"), _hit("b", "unrelated"), _hit("c", "main modules")]
    out = TokenOverlapReranker().rerank("main modules", hits, top_n=1)
    assert out[0].chunk_id == "c"  # arrived last, promoted to first


def test_synthesis_prompt_carries_actual_chunk_content() -> None:
    """Regression: the prompt used to send only each chunk's first line capped at
    120 chars. The model then had no evidence to reason over, cited nothing, and
    the mandatory-citation gate turned every question into a refusal."""
    from backend.answer.synthesis import render_synthesis_prompt
    from backend.domain.models import Persona

    body = "def build_graph():\n    # walks the estate\n    return GraphBuildResult()"
    prompt = render_synthesis_prompt("what builds the graph?", Persona.ENGINEER, [_hit("a", body)])

    # Every line of the chunk reaches the model, not just the first.
    assert "walks the estate" in prompt
    assert "return GraphBuildResult()" in prompt
    assert "[a]" in prompt  # still cited by id


def test_synthesis_prompt_truncates_and_says_so() -> None:
    from backend.answer.synthesis import EVIDENCE_CHARS_PER_CHUNK, render_synthesis_prompt
    from backend.domain.models import Persona

    huge = "x" * (EVIDENCE_CHARS_PER_CHUNK * 3)
    prompt = render_synthesis_prompt("q", Persona.ENGINEER, [_hit("a", huge)])
    assert "… (truncated)" in prompt  # marked, so a cut chunk is not read as complete
    assert len(prompt) < len(huge)  # and actually bounded


def _hit_id(chunk_id: str) -> SearchHit:
    h = _hit("x", "body")
    return h.model_copy(update={"chunk_id": chunk_id}) if hasattr(h, "model_copy") else h


def test_citation_resolves_the_hash_models_actually_cite() -> None:
    """Regression: gpt-4o-mini cited [ef64c105] for the chunk
    'vectural:vectural/plan/design-document.md:1-200:ef64c105'. Exact matching
    rejected it, refusing a well-grounded answer over id formatting."""
    from backend.answer.citations import resolve_citations

    hits = [_hit_id("vectural:vectural/plan/design-document.md:1-200:ef64c105")]
    res = resolve_citations("Vectural ingests from git [ef64c105].", hits)
    assert res.ok
    assert res.unresolved == []
    assert res.resolved[0].chunk_id.endswith("ef64c105")


def test_exact_chunk_id_still_resolves() -> None:
    from backend.answer.citations import resolve_citations

    cid = "svc:path/a.py:1-2:deadbeef"
    res = resolve_citations(f"claim [{cid}].", [_hit_id(cid)])
    assert res.ok


def test_ambiguous_abbreviation_stays_unresolved() -> None:
    """Fail-closed: if a short marker matches two chunks, resolving it would be a
    guess about which source a claim rests on."""
    from backend.answer.citations import resolve_citations

    hits = [_hit_id("svc:a.py:1-2:beef"), _hit_id("svc:b.py:1-2:beef")]
    res = resolve_citations("claim [beef].", hits)
    assert not res.ok
    assert res.unresolved == ["beef"]


def test_hallucinated_marker_still_refuses() -> None:
    from backend.answer.citations import resolve_citations

    res = resolve_citations("claim [totallymadeup].", [_hit_id("svc:a.py:1-2:abc123")])
    assert not res.ok
    assert res.unresolved == ["totallymadeup"]


def test_ungrounded_reason_names_the_rejected_claim() -> None:
    """The gate identifies which claim failed; discarding it left an opaque
    refusal that gave the reader nothing to act on."""
    from backend.answer.service import _ungrounded_reason

    r = _ungrounded_reason(["Neo4j stores payment events"])
    assert "not grounded" in r
    assert "Neo4j stores payment events" in r


def test_ungrounded_reason_is_bounded() -> None:
    from backend.answer.service import _ungrounded_reason

    r = _ungrounded_reason([f"claim number {i}" for i in range(7)])
    assert "(+5 more)" in r  # 2 shown, 5 summarised
    long = _ungrounded_reason(["x" * 500])
    assert len(long) < 300 and "…" in long


def test_ungrounded_reason_falls_back_when_gate_names_nothing() -> None:
    from backend.answer.service import _ungrounded_reason

    assert _ungrounded_reason([]) == "a claim was not grounded in the retrieved evidence"
    assert _ungrounded_reason(["  "]) == "a claim was not grounded in the retrieved evidence"


def test_mermaid_brackets_are_not_citations() -> None:
    """A Mermaid diagram uses [labels] and {decisions}. Without excluding fenced
    blocks those parse as citation markers, fail to resolve, and refuse EVERY
    diagram answer — the bug that blocks holistic answers."""
    from backend.answer.citations import extract_markers, resolve_citations

    text = (
        "The gateway calls the ledger [svc:a.py:1-2:abc123].\n\n"
        "```mermaid\nflowchart TD\n  A[Gateway] --> B[Ledger]\n  B --> C{Approved?}\n```\n"
    )
    # Only the real prose citation is seen — not 'Gateway'/'Ledger'/'Approved?'.
    assert extract_markers(text) == ["svc:a.py:1-2:abc123"]
    res = resolve_citations(text, [_hit_id("svc:a.py:1-2:abc123")])
    assert res.ok


def test_code_block_index_is_not_a_citation() -> None:
    """Latent bug the same fix closes: `arr[i]` in a code block used to be read as
    a citation marker '[i]' and fail the gate."""
    from backend.answer.citations import extract_markers

    text = "See the loop [svc:x.py:1-2:def456].\n\n```python\nfor i in x:\n    y = arr[i]\n```\n"
    assert extract_markers(text) == ["svc:x.py:1-2:def456"]
