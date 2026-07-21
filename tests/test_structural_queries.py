"""Structural fast-path queries + validated Cypher builders (§5.5, §Phase 3)."""

from __future__ import annotations

from backend.graph.cypher_validator import validate_cypher
from backend.graph.queries import (
    StructuralQueries,
    cypher_definitions_of,
    cypher_service_dependencies,
    cypher_service_dependents,
)


def test_exit_criterion_what_depends_on_service_three_hops(structural: StructuralQueries) -> None:
    # The Phase 3 exit criterion: "what depends on service X, three hops out".
    dependents = structural.service_dependents("ledger", max_hops=3)
    assert dependents == {1: ["payments"], 2: ["gateway"]}


def test_dependencies_outward(structural: StructuralQueries) -> None:
    assert structural.service_dependencies("gateway", max_hops=3) == {
        1: ["payments"],
        2: ["ledger"],
    }


def test_hop_limit_truncates(structural: StructuralQueries) -> None:
    assert structural.service_dependents("ledger", max_hops=1) == {1: ["payments"]}


def test_direct_callers_and_callees(structural: StructuralQueries) -> None:
    assert structural.direct_callees("gateway") == ["payments"]
    assert structural.direct_callers("ledger") == ["payments"]


def test_definitions_and_topics(structural: StructuralQueries) -> None:
    defs = structural.definitions_of("applyCharge")
    assert len(defs) == 1
    assert defs[0].service == "ledger"
    assert structural.topic_publishers("payments.events") == ["payments"]
    assert structural.topic_consumers("payments.events") == ["notifications"]


def test_unknown_service_returns_empty(structural: StructuralQueries) -> None:
    assert structural.service_dependents("does-not-exist") == {}


def test_cypher_builders_emit_validated_queries() -> None:
    # The fast path must never emit an unsafe query — builders self-validate.
    assert validate_cypher(cypher_service_dependents(3)).ok
    assert validate_cypher(cypher_service_dependencies(3)).ok
    assert validate_cypher(cypher_definitions_of()).ok


def test_cypher_builder_clamps_hops_to_cap() -> None:
    # Asking for more hops than the cap must not produce an invalid query.
    q = cypher_service_dependents(hops=99, hop_cap=5)
    assert "*1..5" in q
    assert validate_cypher(q, hop_cap=5).ok
