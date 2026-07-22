"""Integration tests for the real datastore adapters (§3.3, §4.3, §5.3).

Skipped unless the datastore is reachable, so the offline suite stays green with
no infrastructure. Run against the docker-compose datastores:

    docker compose --profile datastores up -d postgres opensearch neo4j
    VECTURAL_RUN_INTEGRATION=1 uv run pytest tests/test_real_adapters.py
"""

from __future__ import annotations

import os
import socket
from datetime import UTC, datetime

import pytest

from backend.domain.models import (
    Chunk,
    ChunkKind,
    Edge,
    EdgeKind,
    Language,
    Node,
    NodeKind,
    Span,
)
from backend.embedding import HashingEmbedder
from backend.graph.queries import StructuralQueries
from backend.persistence.file_ledger import FileLedgerEntry, FileStatus

INTEGRATION = os.environ.get("VECTURAL_RUN_INTEGRATION") == "1"
pytestmark = pytest.mark.skipif(not INTEGRATION, reason="set VECTURAL_RUN_INTEGRATION=1 to run")

PG_DSN = os.environ.get("VECTURAL_POSTGRES_DSN", "postgresql://vectural:vectural@localhost:5432/vectural")
OS_URL = os.environ.get("VECTURAL_OPENSEARCH_URL", "http://localhost:9200")
NEO4J_URI = os.environ.get("VECTURAL_NEO4J_URI", "bolt://localhost:7687")
NEO4J_PW = os.environ.get("VECTURAL_NEO4J_PASSWORD", "vecturalpw")

NOW = datetime(2026, 7, 1, tzinfo=UTC)


def _reachable(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except OSError:
        return False


def _chunk(cid: str, service: str, path: str, content: str, symbol: str) -> Chunk:
    return Chunk(
        chunk_id=cid, service=service, path=path, language=Language.PYTHON,
        kind=ChunkKind.FUNCTION, span=Span(start=1, end=3), content=content,
        identifiers=[symbol], symbol=symbol, commit_sha="c", content_hash=cid.ljust(16, "0"),
    )


# --------------------------------------------------------------------------- #
# Postgres
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(not _reachable("localhost", 5432), reason="postgres not reachable")
def test_postgres_file_ledger_roundtrip() -> None:
    from backend.persistence.postgres import PgFileLedger, apply_schema, open_connection

    conn = open_connection(PG_DSN)
    apply_schema(conn)
    ledger = PgFileLedger(conn)
    ledger.delete("it-svc", "it-svc/f.py")

    entry = FileLedgerEntry(
        service="it-svc", path="it-svc/f.py", content_hash="h1", prompt_version="file-v1",
        tier=1, status=FileStatus.SUMMARISED, updated_at=NOW,
    )
    ledger.upsert(entry)
    got = ledger.get("it-svc", "it-svc/f.py")
    assert got is not None and got.matches("h1", "file-v1")

    # Upsert overwrites in place; delete removes.
    ledger.upsert(entry.model_copy(update={"content_hash": "h2"}))
    assert ledger.get("it-svc", "it-svc/f.py").content_hash == "h2"
    assert ledger.delete("it-svc", "it-svc/f.py")
    assert ledger.get("it-svc", "it-svc/f.py") is None
    conn.close()


# --------------------------------------------------------------------------- #
# Neo4j
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(not _reachable("localhost", 7687), reason="neo4j not reachable")
def test_neo4j_graph_store_and_structural_queries() -> None:
    from backend.graph.neo4j_store import Neo4jGraphStore

    store = Neo4jGraphStore.connect(NEO4J_URI, "neo4j", NEO4J_PW)
    # Clean slate for the test's services.
    store.run_read("MATCH (n) WHERE n.key STARTS WITH 'itg-' DETACH DELETE n")

    nodes = [
        Node(kind=NodeKind.SERVICE, key="itg-a", properties={}, commit_sha="c", indexed_at=NOW),
        Node(kind=NodeKind.SERVICE, key="itg-b", properties={}, commit_sha="c", indexed_at=NOW),
        Node(kind=NodeKind.SERVICE, key="itg-c", properties={}, commit_sha="c", indexed_at=NOW),
    ]
    edges = [
        Edge(kind=EdgeKind.CALLS, src_kind=NodeKind.SERVICE, src_key="itg-a",
             dst_kind=NodeKind.SERVICE, dst_key="itg-b"),
        Edge(kind=EdgeKind.CALLS, src_kind=NodeKind.SERVICE, src_key="itg-b",
             dst_kind=NodeKind.SERVICE, dst_key="itg-c"),
    ]
    store.load(nodes, edges)

    assert store.has_node(NodeKind.SERVICE, "itg-a")
    queries = StructuralQueries(store)
    # a → b → c: dependents of c are b (1 hop) and a (2 hops).
    assert queries.service_dependents("itg-c", max_hops=3) == {1: ["itg-b"], 2: ["itg-a"]}
    assert queries.service_dependencies("itg-a", max_hops=3) == {1: ["itg-b"], 2: ["itg-c"]}

    store.run_read("MATCH (n) WHERE n.key STARTS WITH 'itg-' DETACH DELETE n")
    store.close()


@pytest.mark.skipif(not _reachable("localhost", 7687), reason="neo4j not reachable")
def test_neo4j_delete_file_cascade() -> None:
    from backend.graph.neo4j_store import Neo4jGraphStore

    store = Neo4jGraphStore.connect(NEO4J_URI, "neo4j", NEO4J_PW)
    store.run_read("MATCH (n) WHERE n.key STARTS WITH 'itf-' DETACH DELETE n")
    nodes = [
        Node(
            kind=NodeKind.FILE, key="itf-svc/f.py", properties={}, commit_sha="c", indexed_at=NOW
        ),
        Node(
            kind=NodeKind.FUNCTION, key="itf-svc/f.py#foo", properties={}, commit_sha="c",
            indexed_at=NOW,
        ),
    ]
    edges = [
        Edge(kind=EdgeKind.DEFINES, src_kind=NodeKind.FILE, src_key="itf-svc/f.py",
             dst_kind=NodeKind.FUNCTION, dst_key="itf-svc/f.py#foo"),
    ]
    store.load(nodes, edges)
    assert store.delete_file("itf-svc/f.py") == 2  # file + its function
    assert not store.has_node(NodeKind.FILE, "itf-svc/f.py")
    assert not store.has_node(NodeKind.FUNCTION, "itf-svc/f.py#foo")
    store.close()


# --------------------------------------------------------------------------- #
# OpenSearch
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(not _reachable("localhost", 9200), reason="opensearch not reachable")
def test_opensearch_index_and_hybrid_search() -> None:
    from backend.retrieval.opensearch_backend import OpenSearchBackend

    embedder = HashingEmbedder()
    backend = OpenSearchBackend.connect(OS_URL, "it-vectural-chunks", embedder)

    chunks = [
        _chunk(
            "os1", "payments", "payments/refund.py",
            "def reverse_refund(id): publish(id)", "reverse_refund",
        ),
        _chunk("os2", "ledger", "ledger/ledger.py", "def apply_charge(a): save(a)", "apply_charge"),
    ]
    backend.delete_by_file("payments", "payments/refund.py")
    backend.delete_by_file("ledger", "ledger/ledger.py")
    backend.index(chunks)

    hits = backend.hybrid_search(
        "refund reversal", embedder.embed_one("refund reversal"), k=5
    )
    assert hits
    assert hits[0].chunk_id == "os1"  # identifier tokenisation matches (§4.3)

    # Service scope is a hard filter.
    scoped = backend.hybrid_search("charge", embedder.embed_one("charge"), k=5, services={"ledger"})
    assert all(h.service == "ledger" for h in scoped)

    # Cascade delete removes the file's chunks.
    assert backend.delete_by_file("payments", "payments/refund.py") >= 1
    assert ("payments", "payments/refund.py") not in backend.indexed_files()


@pytest.mark.skipif(not _reachable("localhost", 5432), reason="postgres not reachable")
def test_pg_summary_store_roundtrip() -> None:
    from datetime import datetime as _dt

    from backend.persistence.postgres import apply_schema, open_connection
    from backend.persistence.summary_store import PgSummaryStore
    from backend.summarise.store import SummaryRecord

    conn = open_connection(PG_DSN)
    apply_schema(conn)
    with conn.cursor() as cur:
        cur.execute("DELETE FROM summaries WHERE key LIKE 'it-svc%'")

    store = PgSummaryStore(conn)
    rec = SummaryRecord(
        tier=2, kind="module", key="it-svc/mod", text="handles refunds",
        data={"responsibility": "handles refunds", "key_files": ["a.py"]},
        content_hash="h1", prompt_version="module-v1", updated_at=_dt(2026, 7, 1, tzinfo=UTC),
    )
    store.upsert(rec)
    got = store.get(2, "it-svc/mod")
    assert got is not None
    assert got.text == "handles refunds"
    assert got.data["key_files"] == ["a.py"]  # JSONB round-trips
    assert any(r.key == "it-svc/mod" for r in store.all(2))


@pytest.mark.skipif(not _reachable("localhost", 5432), reason="postgres not reachable")
def test_pg_flow_store_roundtrip() -> None:
    from datetime import datetime as _dt

    from backend.flows.models import FlowNarrative, ReviewStatus
    from backend.persistence.flow_store import PgFlowStore
    from backend.persistence.postgres import apply_schema, open_connection

    conn = open_connection(PG_DSN)
    apply_schema(conn)
    with conn.cursor() as cur:
        cur.execute("DELETE FROM flow_narratives WHERE id LIKE 'it-flow%'")

    store = PgFlowStore(conn)
    narrative = FlowNarrative(
        id="it-flow-1", title="Refund", services=["payments", "ledger"], trigger="call graph",
        signature="sig", text="payments calls ledger", prompt_version="flow-v1",
        content_hash="h1", status=ReviewStatus.PENDING, updated_at=_dt(2026, 7, 1, tzinfo=UTC),
    )
    store.upsert(narrative)
    got = store.get("it-flow-1")
    assert got is not None
    assert got.services == ["payments", "ledger"]  # JSONB model round-trips
    assert got.status is ReviewStatus.PENDING
    assert any(n.id == "it-flow-1" for n in store.all())
