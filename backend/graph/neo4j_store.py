"""Neo4j-backed graph store (optional ``neo4j`` extra) — a marked infra seam.

This is the production adapter. It is import-guarded so the rest of the graph
package (schema DDL, validator, extraction, in-memory queries) needs no database
and stays fully testable. Applying the schema is idempotent (§6 rebuild), and
every read query routed through here is expected to have passed
:func:`validate_cypher` first — this class does not itself relax that gate.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from backend.domain.models import Edge, Node
from backend.graph.schema import schema_statements

if TYPE_CHECKING:
    from neo4j import Driver


class Neo4jGraphStore:
    """Thin wrapper over the official driver. Batches node/edge upserts by label
    and relationship type using ``UNWIND`` for throughput on the initial load."""

    def __init__(self, driver: Driver, database: str = "neo4j") -> None:
        self._driver = driver
        self._database = database

    @classmethod
    def connect(
        cls, uri: str, user: str, password: str, database: str = "neo4j"
    ) -> Neo4jGraphStore:
        from neo4j import GraphDatabase

        driver = GraphDatabase.driver(uri, auth=(user, password))
        return cls(driver, database=database)

    def apply_schema(self) -> None:
        with self._driver.session(database=self._database) as session:
            for statement in schema_statements():
                session.run(statement)

    def upsert_nodes(self, nodes: list[Node]) -> None:
        by_label: dict[str, list[dict[str, Any]]] = {}
        for node in nodes:
            by_label.setdefault(node.kind.value, []).append(
                {
                    "key": node.key,
                    "commit_sha": node.commit_sha,
                    "indexed_at": node.indexed_at.isoformat(),
                    "prompt_version": node.prompt_version,
                    **node.properties,
                }
            )
        with self._driver.session(database=self._database) as session:
            for label, rows in by_label.items():
                session.run(
                    f"UNWIND $rows AS row MERGE (n:{label} {{key: row.key}}) SET n += row",
                    rows=rows,
                )

    def upsert_edges(self, edges: list[Edge]) -> None:
        grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
        for edge in edges:
            grouped.setdefault(
                (edge.src_kind.value, edge.kind.value, edge.dst_kind.value), []
            ).append({"src": edge.src_key, "dst": edge.dst_key, "props": edge.properties})
        with self._driver.session(database=self._database) as session:
            for (src_label, rel, dst_label), rows in grouped.items():
                session.run(
                    f"UNWIND $rows AS row "
                    f"MATCH (a:{src_label} {{key: row.src}}), (b:{dst_label} {{key: row.dst}}) "
                    f"MERGE (a)-[r:{rel}]->(b) SET r += row.props",
                    rows=rows,
                )

    def run_read(self, cypher: str, **params: Any) -> list[dict[str, Any]]:
        """Run a (validated) read query and materialise the records."""
        with self._driver.session(database=self._database) as session:
            return [record.data() for record in session.run(cypher, **params)]

    def close(self) -> None:
        self._driver.close()
