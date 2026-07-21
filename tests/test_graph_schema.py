"""Neo4j schema DDL + allowed sets (§4.2/§3.1)."""

from __future__ import annotations

from backend.domain.models import EdgeKind, NodeKind
from backend.graph.schema import (
    ALLOWED_LABELS,
    ALLOWED_REL_TYPES,
    constraint_statements,
    schema_statements,
    vector_index_statements,
)


def test_allowed_sets_track_the_enums() -> None:
    assert {k.value for k in NodeKind} == ALLOWED_LABELS
    assert {k.value for k in EdgeKind} == ALLOWED_REL_TYPES


def test_one_unique_constraint_per_label() -> None:
    stmts = constraint_statements()
    assert len(stmts) == len(ALLOWED_LABELS)
    assert all("IS UNIQUE" in s and "IF NOT EXISTS" in s for s in stmts)


def test_vector_indexes_are_1024_cosine() -> None:
    stmts = vector_index_statements()
    assert any("Capability" in s for s in stmts)
    assert all("`vector.dimensions`: 1024" in s for s in stmts)
    assert all("'cosine'" in s for s in stmts)


def test_schema_is_idempotent_ddl() -> None:
    # Every statement is guarded so applying the schema twice is safe (§6 rebuild).
    assert all("IF NOT EXISTS" in s for s in schema_statements())
