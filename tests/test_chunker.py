"""Function/class boundary chunking (§5.1)."""

from __future__ import annotations

from itertools import pairwise

from backend.domain.models import ChunkKind, Language
from backend.ingestion.chunker import chunk_source

PY = b"""import os

@route("/refunds")
def reverse_refund(refund_id):
    return publish("payments.events", refund_id)


class RefundRepo:
    def get(self, refund_id):
        return self.db.find(refund_id)

    def save(self, refund):
        self.db.write(refund)
"""


def _by_symbol(chunks, symbol):
    return next(c for c in chunks if c.symbol == symbol)


def test_python_structural_chunks() -> None:
    chunks = chunk_source(
        PY, language=Language.PYTHON, service="payments-api", path="payments-api/refund.py",
        commit_sha="c0ffee",
    )
    kinds = {(c.kind, c.symbol) for c in chunks}
    assert (ChunkKind.FUNCTION, "reverse_refund") in kinds
    assert (ChunkKind.CLASS, "RefundRepo") in kinds
    assert (ChunkKind.METHOD, "get") in kinds
    assert (ChunkKind.METHOD, "save") in kinds
    # The import line becomes a module chunk.
    assert any(c.kind is ChunkKind.MODULE for c in chunks)


def test_decorator_included_in_span() -> None:
    chunks = chunk_source(
        PY, language=Language.PYTHON, service="s", path="s/refund.py", commit_sha="x"
    )
    fn = _by_symbol(chunks, "reverse_refund")
    assert '@route("/refunds")' in fn.content
    assert fn.span.start == 3  # the decorator line, not the def line


def test_identifiers_extracted_and_boostable() -> None:
    chunks = chunk_source(
        PY, language=Language.PYTHON, service="s", path="s/refund.py", commit_sha="x"
    )
    fn = _by_symbol(chunks, "reverse_refund")
    assert "reverse_refund" in fn.identifiers
    assert "publish" in fn.identifiers  # call target is searchable


def test_chunk_id_and_hash_are_deterministic() -> None:
    a = chunk_source(PY, language=Language.PYTHON, service="s", path="s/f.py", commit_sha="x")
    b = chunk_source(
        PY, language=Language.PYTHON, service="s", path="s/f.py", commit_sha="DIFFERENT"
    )
    # Same content -> same ids and hashes regardless of commit (stable citations).
    assert [c.chunk_id for c in a] == [c.chunk_id for c in b]
    assert [c.content_hash for c in a] == [c.content_hash for c in b]


def test_no_overlapping_spans() -> None:
    chunks = sorted(
        chunk_source(PY, language=Language.PYTHON, service="s", path="s/f.py", commit_sha="x"),
        key=lambda c: c.span.start,
    )
    for prev, nxt in pairwise(chunks):
        assert prev.span.end < nxt.span.start, f"{prev.symbol} overlaps {nxt.symbol}"


def test_unknown_language_gets_whole_file_module_chunk() -> None:
    text = b"# just some markdown\n\nHello world, not source.\n"
    chunks = chunk_source(
        text, language=Language.UNKNOWN, service="s", path="s/README.md", commit_sha="x"
    )
    assert len(chunks) == 1
    assert chunks[0].kind is ChunkKind.MODULE
    assert "Hello world" in chunks[0].content


def test_empty_file_yields_no_chunks() -> None:
    chunks = chunk_source(
        b"\n\n  \n", language=Language.PYTHON, service="s", path="s/e.py", commit_sha="x"
    )
    assert chunks == []


def test_module_window_splits_large_unstructured_file() -> None:
    body = b"\n".join(f"line_{i} = {i}".encode() for i in range(450))
    chunks = chunk_source(
        body, language=Language.UNKNOWN, service="s", path="s/data.txt", commit_sha="x",
        module_window=200,
    )
    assert len(chunks) == 3  # 450 lines / 200 window
    assert all(c.kind is ChunkKind.MODULE for c in chunks)
