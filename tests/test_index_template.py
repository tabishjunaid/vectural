"""OpenSearch index template + bulk indexer (§4.3)."""

from __future__ import annotations

import json

from backend.domain.models import Chunk, ChunkKind, Language, Span
from backend.index import (
    EMBEDDING_DIMS,
    bulk_ndjson,
    chunk_to_doc,
    index_mappings,
    index_settings,
    index_template,
)


def _chunk(embedding: list[float] | None = None) -> Chunk:
    return Chunk(
        chunk_id="svc:svc/a.py:1-3:deadbeef",
        service="svc",
        path="svc/a.py",
        language=Language.PYTHON,
        kind=ChunkKind.FUNCTION,
        span=Span(start=1, end=3),
        content="def f(): pass",
        identifiers=["f"],
        symbol="f",
        commit_sha="c0ffee",
        content_hash="deadbeef00000000",
        embedding=embedding,
    )


def test_code_analyzer_present_and_configured() -> None:
    settings = index_settings()
    analyzer = settings["analysis"]["analyzer"]["code_analyzer"]
    assert analyzer["filter"][0] == "code_delimiter"
    delim = settings["analysis"]["filter"]["code_delimiter"]
    assert delim["type"] == "word_delimiter_graph"
    assert delim["split_on_case_change"] is True
    assert settings["index"]["knn"] is True


def test_mapping_has_exact_subfield_and_knn_vector() -> None:
    props = index_mappings()["properties"]
    assert props["content"]["fields"]["exact"]["type"] == "keyword"
    assert props["embedding"]["type"] == "knn_vector"
    assert props["embedding"]["dimension"] == EMBEDDING_DIMS == 1024
    assert props["service"]["type"] == "keyword"  # scoping filter field


def test_index_template_binds_pattern() -> None:
    tmpl = index_template()
    assert tmpl["index_patterns"] == ["vectural-chunks-*"]
    assert "code_analyzer" in tmpl["template"]["settings"]["analysis"]["analyzer"]


def test_chunk_to_doc_omits_absent_embedding() -> None:
    assert "embedding" not in chunk_to_doc(_chunk(embedding=None))
    doc = chunk_to_doc(_chunk(embedding=[0.1] * 4))
    assert doc["embedding"] == [0.1] * 4
    assert doc["lines"] == "1-3"


def test_bulk_ndjson_is_action_source_pairs() -> None:
    ndjson = bulk_ndjson([_chunk()], index="vectural-chunks-code")
    lines = ndjson.strip().split("\n")
    assert len(lines) == 2  # action line + source line
    action = json.loads(lines[0])
    assert action["index"]["_index"] == "vectural-chunks-code"
    assert action["index"]["_id"] == "svc:svc/a.py:1-3:deadbeef"
    source = json.loads(lines[1])
    assert source["chunk_id"] == "svc:svc/a.py:1-3:deadbeef"


def test_bulk_ndjson_empty() -> None:
    assert bulk_ndjson([], index="x") == ""
