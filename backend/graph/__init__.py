"""Graph construction and querying (implementation-plan §Phase 3).

Phase 3 is still **no LLM**: it builds the Neo4j graph deterministically from the
AST + OpenAPI + infra manifests, ships the Cypher validation layer that later
guards LLM-generated queries (§5.2), and answers structural questions ("what
depends on service X, three hops out") directly from the graph (§5.5).

Everything here runs in-memory and is fully tested without a database; the Neo4j
adapter is an optional, clearly-marked seam behind the ``neo4j`` extra.
"""

from backend.graph.builder import build_graph
from backend.graph.cypher_validator import (
    CypherValidationError,
    ValidationResult,
    validate_cypher,
)
from backend.graph.queries import StructuralQueries
from backend.graph.schema import (
    ALLOWED_LABELS,
    ALLOWED_REL_TYPES,
    schema_statements,
)
from backend.graph.store import GraphStore, InMemoryGraphStore

__all__ = [
    "ALLOWED_LABELS",
    "ALLOWED_REL_TYPES",
    "CypherValidationError",
    "GraphStore",
    "InMemoryGraphStore",
    "StructuralQueries",
    "ValidationResult",
    "build_graph",
    "schema_statements",
    "validate_cypher",
]
