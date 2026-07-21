"""Cypher validation layer (§5.2). The security-critical component of Phase 3."""

from __future__ import annotations

import pytest

from backend.graph.cypher_validator import (
    CypherValidationError,
    ensure_valid,
    validate_cypher,
)

VALID = [
    "MATCH (s:Service {key: $k})-[:CALLS*1..3]->(d:Service) RETURN d.key",
    "MATCH (s:Service)-[:CALLS]->(d:Service) RETURN d",
    "MATCH (f:File)-[:DEFINES]->(fn:Function {symbol: $s}) RETURN fn",
    "MATCH (a:Service)-->(b:Service)-->(c:Service) RETURN c",  # 2 fixed hops <= cap
    # A write keyword appearing only inside a string literal is fine.
    'MATCH (t:Topic {name: "please-delete-me"}) RETURN t',
]

INVALID = [
    ("", "empty"),
    ("MATCH (s:Service)-[:CALLS*1..9]->(d:Service) RETURN d", "hop cap"),
    ("MATCH (s:Service)-[:CALLS*]->(d:Service) RETURN d", "unbounded"),
    ("MATCH (s:Service)-[:CALLS*2..]->(d:Service) RETURN d", "unbounded"),
    ("MATCH (s:Service) CREATE (x:Service) RETURN s", "CREATE"),
    ("MATCH (s:Service) MERGE (s)-[:CALLS]->(s) RETURN s", "MERGE"),
    ("MATCH (s:Service) DETACH DELETE s", "DELETE"),
    ("MATCH (s:Service) SET s.hacked = true RETURN s", "SET"),
    ("MATCH (s:Service) REMOVE s.name RETURN s", "REMOVE"),
    ("MATCH (s:Secret)-[:CALLS]->(d:Service) RETURN d", "not in schema"),
    ("MATCH (s:Service)-[:PWNS]->(d:Service) RETURN d", "not in schema"),
    ("MATCH (s:Service) WHERE s:Secret RETURN s", "not in schema"),
    ("MATCH (s:Service)-[:CALLS]->(d:Service RETURN d", "unclosed"),
    ("MATCH (s:Service))-[:CALLS]->(d:Service) RETURN d", "unbalanced"),
]


@pytest.mark.parametrize("query", VALID)
def test_valid_queries_pass(query: str) -> None:
    result = validate_cypher(query)
    assert result.ok, result.reason
    assert result.query == query.strip()


@pytest.mark.parametrize(("query", "needle"), INVALID)
def test_invalid_queries_rejected(query: str, needle: str) -> None:
    result = validate_cypher(query)
    assert not result.ok
    assert needle.lower() in (result.reason or "").lower()


def test_read_only_check_precedes_label_check() -> None:
    # Ordering matters: the reason should be the first failure (write), which is
    # what gets appended to the prompt on the single retry (§5.2).
    result = validate_cypher("MATCH (s:Secret) DELETE s")
    assert "DELETE" in (result.reason or "")


def test_hop_cap_is_configurable() -> None:
    q = "MATCH (s:Service)-[:CALLS*1..3]->(d:Service) RETURN d"
    assert validate_cypher(q, hop_cap=3).ok
    assert not validate_cypher(q, hop_cap=2).ok


def test_ensure_valid_raises_on_invalid() -> None:
    with pytest.raises(CypherValidationError, match="CREATE"):
        ensure_valid("MATCH (s:Service) CREATE (x:Service) RETURN s")
    assert ensure_valid("MATCH (s:Service) RETURN s").startswith("MATCH")
