"""Translate :class:`Chunk` objects into OpenSearch bulk actions (§4.3).

Documents are keyed by ``chunk_id`` — which is deterministic (service + path +
span + content hash) — so re-indexing unchanged content overwrites in place
rather than duplicating, and a stale chunk removed by the freshness pipeline
(§5.9) has a stable id to delete.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from typing import Any

from backend.domain.models import Chunk


def chunk_to_doc(chunk: Chunk) -> dict[str, Any]:
    """The indexed document body for a chunk (mapping in ``index_mappings``)."""
    doc: dict[str, Any] = {
        "chunk_id": chunk.chunk_id,
        "content": chunk.content,
        "identifiers": chunk.identifiers,
        "symbol": chunk.symbol,
        "service": chunk.service,
        "path": chunk.path,
        "lines": str(chunk.span),
        "commit_sha": chunk.commit_sha,
        "language": chunk.language.value,
        "kind": chunk.kind.value,
        "doc_type": chunk.doc_type,
        "content_hash": chunk.content_hash,
    }
    # Only include the vector once it has been computed; an un-embedded chunk is
    # still lexically searchable (BM25 half of hybrid).
    if chunk.embedding is not None:
        doc["embedding"] = chunk.embedding
    return doc


def bulk_actions(chunks: Iterable[Chunk], index: str) -> Iterator[dict[str, Any]]:
    """Yield the alternating action/source dicts for the ``_bulk`` API."""
    for chunk in chunks:
        yield {"index": {"_index": index, "_id": chunk.chunk_id}}
        yield chunk_to_doc(chunk)


def bulk_ndjson(chunks: Iterable[Chunk], index: str) -> str:
    """Serialise chunks to the newline-delimited JSON the ``_bulk`` API expects
    (trailing newline included)."""
    lines = [json.dumps(action, ensure_ascii=False) for action in bulk_actions(chunks, index)]
    return "\n".join(lines) + "\n" if lines else ""
