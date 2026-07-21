"""Neo4j schema: constraints, indexes, and the allowed label/relationship sets.

The allowed sets are derived from :class:`NodeKind`/:class:`EdgeKind` so the
Cypher validator (§5.2) and the graph schema can never disagree about what a
valid label is — the schema-in-prompt (§3.1) and the validator share one source.

DDL is generated as Cypher strings rather than executed here; the Neo4j adapter
runs them. Every statement is ``IF NOT EXISTS`` so applying the schema is
idempotent — important for the rehearsed rebuild-from-git (design-doc §6).
"""

from __future__ import annotations

from backend.domain.models import EdgeKind, NodeKind
from backend.index.opensearch_template import EMBEDDING_DIMS

# The canonical allow-lists. Cypher generation (§5.3 step 2) is constrained to
# these, and the validator (§5.2) rejects anything outside them.
ALLOWED_LABELS: frozenset[str] = frozenset(k.value for k in NodeKind)
ALLOWED_REL_TYPES: frozenset[str] = frozenset(k.value for k in EdgeKind)

# Vector indexes (§3.1): all BGE-M3, 1024-dim, cosine — used by entity linking.
_VECTOR_INDEXES: tuple[tuple[str, str, str], ...] = (
    ("capability_embedding", NodeKind.CAPABILITY.value, "embedding"),
    ("service_summary_embedding", NodeKind.SERVICE.value, "summary_embedding"),
    ("flow_embedding", NodeKind.FLOW.value, "embedding"),
)


def constraint_statements() -> list[str]:
    """One uniqueness constraint per label on ``key`` (node identity)."""
    return [
        f"CREATE CONSTRAINT {label.lower()}_key IF NOT EXISTS "
        f"FOR (n:{label}) REQUIRE n.key IS UNIQUE"
        for label in sorted(ALLOWED_LABELS)
    ]


def property_index_statements() -> list[str]:
    """Indexes on the staleness/reconciliation properties every node carries."""
    statements: list[str] = []
    for label in sorted(ALLOWED_LABELS):
        for prop in ("commit_sha", "indexed_at"):
            statements.append(
                f"CREATE INDEX {label.lower()}_{prop} IF NOT EXISTS "
                f"FOR (n:{label}) ON (n.{prop})"
            )
    return statements


def vector_index_statements(dims: int = EMBEDDING_DIMS) -> list[str]:
    return [
        f"CREATE VECTOR INDEX {name} IF NOT EXISTS "
        f"FOR (n:{label}) ON (n.{prop}) "
        f"OPTIONS {{indexConfig: {{`vector.dimensions`: {dims}, "
        f"`vector.similarity_function`: 'cosine'}}}}"
        for name, label, prop in _VECTOR_INDEXES
    ]


def schema_statements() -> list[str]:
    """The full idempotent schema: constraints, then property, then vector indexes."""
    return constraint_statements() + property_index_statements() + vector_index_statements()
