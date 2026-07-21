"""Graph extraction: call graph, topics, endpoints (§Phase 3)."""

from __future__ import annotations

from backend.domain.models import EdgeKind, Language, NodeKind
from backend.graph.analyze import analyze_file
from backend.graph.builder import GraphBuildResult
from backend.graph.openapi import endpoints, looks_like_openapi, parse_openapi


def _edges(build: GraphBuildResult, kind: EdgeKind) -> set[tuple[str, str]]:
    return {(e.src_key, e.dst_key) for e in build.edges if e.kind is kind}


def test_cross_service_calls_resolved(graph_build: GraphBuildResult) -> None:
    calls = _edges(graph_build, EdgeKind.CALLS)
    assert ("gateway", "payments") in calls  # handle_request -> charge
    assert ("payments", "ledger") in calls  # charge -> applyCharge


def test_no_self_service_call_edges(graph_build: GraphBuildResult) -> None:
    calls = _edges(graph_build, EdgeKind.CALLS)
    assert all(src != dst for src, dst in calls)


def test_library_calls_are_not_invented(graph_build: GraphBuildResult) -> None:
    # `_save` is intra-ledger; `publish`/`subscribe` are messaging, not CALLS.
    calls = _edges(graph_build, EdgeKind.CALLS)
    assert ("ledger", "ledger") not in calls
    assert len(calls) == 2


def test_topic_pub_sub_edges(graph_build: GraphBuildResult) -> None:
    assert ("payments", "payments.events") in _edges(graph_build, EdgeKind.PUBLISHES)
    assert ("notifications", "payments.events") in _edges(graph_build, EdgeKind.CONSUMES)
    topics = {n.key for n in graph_build.nodes if n.kind is NodeKind.TOPIC}
    assert topics == {"payments.events"}


def test_endpoints_from_openapi(graph_build: GraphBuildResult) -> None:
    exposes = _edges(graph_build, EdgeKind.EXPOSES)
    assert ("payments", "POST /charge") in exposes
    assert ("payments", "POST /refunds/{id}/reverse") in exposes


def test_analyze_file_extracts_calls_and_topics() -> None:
    src = b'def f():\n    charge(1)\n    publish("t.events", 1)\n'
    facts = analyze_file(service="s", path="s/f.py", source=src, language=Language.PYTHON)
    assert "f" in facts.defined_symbols
    assert ("f", "charge") in facts.calls
    assert facts.publishes == {"t.events"}


def test_openapi_sniff_and_parse() -> None:
    spec = b"openapi: 3.0.0\npaths:\n  /x:\n    get:\n      summary: x\n"
    assert looks_like_openapi("payments/openapi.yaml", spec)
    assert not looks_like_openapi("payments/pay.py", b"def f(): pass")
    doc = parse_openapi(spec)
    assert doc is not None
    assert endpoints(doc) == [("GET", "/x")]
