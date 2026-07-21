"""OpenSearch hybrid query construction (§4.3, §5.3 step 5).

Pure query-DSL builders — no network — so the query shape is unit-tested and
reviewable independently of a running cluster. The lexical sub-query boosts the
``identifiers`` field (function/class names carry disproportionate signal) and
exposes an exact keyword match; the dense sub-query is a k-NN over the BGE-M3
vector. RRF fusion of the two is applied by an OpenSearch search pipeline
(``hybrid`` query), not client-side.
"""

from __future__ import annotations

from typing import Any

from backend.embedding.base import Vector

# The identifiers field is boosted so a name hit outranks an incidental prose hit.
_IDENTIFIER_BOOST = 3.0


def _service_filter(services: set[str] | None) -> list[dict[str, Any]]:
    if not services:
        return []
    return [{"terms": {"service": sorted(services)}}]


def lexical_query(query_text: str, services: set[str] | None = None) -> dict[str, Any]:
    """BM25 half: multi-field match over content + boosted identifiers, scoped."""
    return {
        "bool": {
            "filter": _service_filter(services),
            "should": [
                {
                    "multi_match": {
                        "query": query_text,
                        "fields": ["content", f"identifiers^{_IDENTIFIER_BOOST}"],
                        "type": "best_fields",
                    }
                },
                {"term": {"content.exact": {"value": query_text, "boost": 2.0}}},
            ],
            "minimum_should_match": 1,
        }
    }


def knn_query(
    query_vector: Vector, k: int, services: set[str] | None = None
) -> dict[str, Any]:
    """Dense half: k-NN over the ``embedding`` field, scoped by the same filter."""
    clause: dict[str, Any] = {"vector": query_vector, "k": k}
    filt = _service_filter(services)
    if filt:
        clause["filter"] = {"bool": {"filter": filt}}
    return {"knn": {"embedding": clause}}


def hybrid_query(
    query_text: str,
    query_vector: Vector | None,
    *,
    k: int,
    services: set[str] | None = None,
) -> dict[str, Any]:
    """The full request body. Degrades to a pure lexical query when no vector is
    supplied (the embedder being unavailable must not break retrieval)."""
    lexical = lexical_query(query_text, services)
    if query_vector is None:
        return {"size": k, "query": lexical}

    return {
        "size": k,
        "query": {"hybrid": {"queries": [lexical, knn_query(query_vector, k, services)]}},
    }
