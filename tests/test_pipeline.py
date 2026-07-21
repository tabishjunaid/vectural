"""Ingestion orchestration: chunks + graph deltas (§5.1)."""

from __future__ import annotations

from pathlib import Path

from backend.domain.manifest import Manifest
from backend.domain.models import EdgeKind, NodeKind
from backend.ingestion.pipeline import ingest_file, ingest_tree

PY = b"""def reverse_refund(refund_id):
    return publish(refund_id)


class RefundRepo:
    def get(self, refund_id):
        return refund_id
"""


def test_ingest_file_emits_skeleton_graph() -> None:
    result = ingest_file(
        source=PY, service="payments-api", path="payments-api/refund.py", commit_sha="c0ffee"
    )
    node_kinds = {n.kind for n in result.delta.nodes}
    assert {NodeKind.SERVICE, NodeKind.MODULE, NodeKind.FILE, NodeKind.FUNCTION} <= node_kinds

    edge_kinds = {e.kind for e in result.delta.edges}
    assert EdgeKind.CONTAINS in edge_kinds  # Service->Module, Module->File
    assert EdgeKind.DEFINES in edge_kinds  # File->Function

    # A Function node exists for the top-level def and carries its chunk_id so a
    # citation can resolve graph node -> evidence chunk.
    fn = next(n for n in result.delta.nodes if n.kind is NodeKind.FUNCTION)
    assert fn.properties["symbol"] == "reverse_refund"
    assert fn.properties["chunk_id"]


def test_deterministic_nodes_have_no_prompt_version() -> None:
    result = ingest_file(source=PY, service="s", path="s/refund.py", commit_sha="x")
    # Phase 1 nodes carry no LLM-derived content, so prompt_version stays None.
    assert all(n.prompt_version is None for n in result.delta.nodes)


def test_parse_error_flagged_but_still_chunked() -> None:
    broken = b"def broken(:\n    x = \n"
    result = ingest_file(source=broken, service="s", path="s/bad.py", commit_sha="x")
    assert result.parse_error is True
    assert result.chunks  # dead-letter accounting, not a hard failure (§5.8)


def test_ingest_tree_dedupes_and_registers_services(estate: Path, manifest: Manifest) -> None:
    result = ingest_tree(estate, manifest, commit_sha="c0ffee")

    # All three services present as nodes, even before counting files.
    service_keys = {n.key for n in result.nodes if n.kind is NodeKind.SERVICE}
    assert service_keys == {"payments-api", "ledger-svc", "booking-api"}

    # Service nodes are de-duplicated (one per service, not one per file).
    service_nodes = [n for n in result.nodes if n.kind is NodeKind.SERVICE]
    assert len(service_nodes) == 3

    # Chunks and function nodes were produced across languages.
    assert result.chunks
    assert result.counts_by_kind().get("Function", 0) >= 3
    assert result.parse_error_count == 0


def test_ingest_tree_all_chunks_carry_commit_sha(estate: Path, manifest: Manifest) -> None:
    result = ingest_tree(estate, manifest, commit_sha="deadbeef")
    assert result.chunks
    assert all(c.commit_sha == "deadbeef" for c in result.chunks)
