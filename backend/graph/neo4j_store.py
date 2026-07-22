"""Neo4j-backed graph store (optional ``neo4j`` extra) — a marked infra seam.

This is the production adapter. It is import-guarded so the rest of the graph
package (schema DDL, validator, extraction, in-memory queries) needs no database
and stays fully testable. Applying the schema is idempotent (§6 rebuild), and
every read query routed through here is expected to have passed
:func:`validate_cypher` first — this class does not itself relax that gate.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from backend.domain.models import Edge, EdgeKind, Node, NodeKind
from backend.graph.schema import schema_statements
from backend.graph.store import NodeRef

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

    def load(self, nodes: list[Node], edges: list[Edge]) -> None:
        """Apply schema, then upsert the whole graph (rebuild-from-git, §6)."""
        self.apply_schema()
        self.upsert_nodes(nodes)
        self.upsert_edges(edges)

    # -- GraphStore protocol (via Cypher) ----------------------------------- #

    def has_node(self, kind: NodeKind, key: str) -> bool:
        rows = self.run_read(
            f"MATCH (n:{kind.value} {{key: $key}}) RETURN count(n) AS c", key=key
        )
        return bool(rows and rows[0]["c"] > 0)

    def nodes(self, kind: NodeKind) -> list[Node]:
        rows = self.run_read(f"MATCH (n:{kind.value}) RETURN properties(n) AS p")
        return [_to_node(kind, row["p"]) for row in rows]

    def out_neighbors(self, kind: NodeKind, key: str, rel: EdgeKind) -> list[NodeRef]:
        rows = self.run_read(
            f"MATCH (n:{kind.value} {{key: $key}})-[:{rel.value}]->(m) "
            "RETURN labels(m) AS labels, m.key AS key",
            key=key,
        )
        return [_ref(row) for row in rows]

    def in_neighbors(self, kind: NodeKind, key: str, rel: EdgeKind) -> list[NodeRef]:
        rows = self.run_read(
            f"MATCH (n:{kind.value} {{key: $key}})<-[:{rel.value}]-(m) "
            "RETURN labels(m) AS labels, m.key AS key",
            key=key,
        )
        return [_ref(row) for row in rows]

    # -- cascade deletes (§5.9) --------------------------------------------- #

    def delete_node(self, kind: NodeKind, key: str) -> bool:
        rows = self.run_read(
            f"MATCH (n:{kind.value} {{key: $key}}) DETACH DELETE n RETURN count(n) AS c", key=key
        )
        return bool(rows and rows[0]["c"] > 0)

    def delete_file(self, path: str) -> int:
        """Delete a File and its Functions with all referencing edges (detach)."""
        rows = self.run_read(
            "MATCH (f:File {key: $path}) "
            "OPTIONAL MATCH (f)-[:DEFINES]->(fn:Function) "
            "WITH collect(DISTINCT fn) AS fns, f "
            "DETACH DELETE f "
            "WITH fns UNWIND fns AS fn DETACH DELETE fn RETURN count(fn) AS c",
            path=path,
        )
        deleted_functions = rows[0]["c"] if rows else 0
        return int(deleted_functions) + 1

    def file_keys(self) -> set[str]:
        rows = self.run_read("MATCH (f:File) RETURN f.key AS key")
        return {str(row["key"]) for row in rows}

    def close(self) -> None:
        self._driver.close()


def _ref(row: dict[str, Any]) -> NodeRef:
    labels = row.get("labels") or []
    kind = NodeKind(labels[0]) if labels else NodeKind.SERVICE
    return (kind, str(row["key"]))


def _to_node(kind: NodeKind, props: dict[str, Any]) -> Node:
    reserved = {"key", "commit_sha", "indexed_at", "prompt_version"}
    return Node(
        kind=kind,
        key=str(props.get("key", "")),
        properties={k: v for k, v in props.items() if k not in reserved},
        commit_sha=str(props.get("commit_sha", "")),
        indexed_at=_parse_dt(props.get("indexed_at")),
        prompt_version=props.get("prompt_version"),
    )


def _parse_dt(value: Any) -> datetime:
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return datetime.now(UTC)
    return datetime.now(UTC)
