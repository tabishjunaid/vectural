"""OpenSearch index template and mapping (implementation-plan §4.3, design §3.2).

The analysis block is the one from the plan verbatim in intent: a
``word_delimiter_graph`` filter so ``processRefundReversal`` is findable as
"refund reversal", flattened back into a single token stream, lowercased.

BGE-M3 produces 1024-dim dense vectors; ``embedding`` is a ``knn_vector`` so the
hybrid BM25 + k-NN retrieval (§5.3 step 5) can fuse lexical and dense scores.
"""

from __future__ import annotations

from typing import Any

# BGE-M3 embedding dimensionality (implementation-plan §3, §4.3).
EMBEDDING_DIMS = 1024


def index_settings() -> dict[str, Any]:
    """Index settings, including the mandatory ``code_analyzer`` (§4.3)."""
    return {
        "index": {
            # Enable approximate k-NN so the dense half of hybrid search works.
            "knn": True,
        },
        "analysis": {
            "filter": {
                "code_delimiter": {
                    "type": "word_delimiter_graph",
                    "preserve_original": True,
                    "catenate_words": True,
                    "split_on_case_change": True,
                    "split_on_numerics": True,
                }
            },
            "analyzer": {
                "code_analyzer": {
                    "tokenizer": "whitespace",
                    "filter": ["code_delimiter", "flatten_graph", "lowercase"],
                }
            },
        },
    }


def index_mappings() -> dict[str, Any]:
    """Chunk field mapping (§3.2). ``content.exact`` is a keyword sub-field for
    exact matches; ``identifiers`` is boosted at query time, not in the mapping."""
    return {
        "properties": {
            "content": {
                "type": "text",
                "analyzer": "code_analyzer",
                "fields": {"exact": {"type": "keyword", "ignore_above": 32766}},
            },
            "identifiers": {"type": "text", "analyzer": "code_analyzer"},
            "symbol": {"type": "keyword"},
            "embedding": {
                "type": "knn_vector",
                "dimension": EMBEDDING_DIMS,
                "method": {
                    "name": "hnsw",
                    "space_type": "cosinesimil",
                    "engine": "lucene",
                },
            },
            # Metadata / scoping filters applied post graph-plan (§5.3 step 5).
            "service": {"type": "keyword"},
            "path": {"type": "keyword"},
            "lines": {"type": "keyword"},
            "commit_sha": {"type": "keyword"},
            "language": {"type": "keyword"},
            "kind": {"type": "keyword"},
            "doc_type": {"type": "keyword"},
            "chunk_id": {"type": "keyword"},
            "content_hash": {"type": "keyword"},
        }
    }


def index_template(
    name: str = "vectural-chunks", pattern: str = "vectural-chunks-*"
) -> dict[str, Any]:
    """A composable index template binding settings + mappings to an index pattern.

    Reused for code, ADR, and doc chunks (§3.2) — they share this mapping and
    differ only by the ``doc_type`` metadata value.
    """
    return {
        "index_patterns": [pattern],
        "template": {
            "settings": index_settings(),
            "mappings": index_mappings(),
        },
        "_meta": {"description": f"Vectural chunk index ({name})", "managed_by": "vectural"},
    }
