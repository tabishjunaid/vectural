"""OpenSearch indexing (§4.3).

The index template — crucially the ``code_analyzer`` — must exist **before** any
bulk load (Phase 1 exit criterion): the analyzer cannot be retrofitted onto a
populated index without a full reindex. This package owns the template and the
Chunk→bulk-action translation; the network client lives behind the optional
``opensearch`` extra so nothing here forces an infra dependency.
"""

from backend.index.bulk import bulk_actions, bulk_ndjson, chunk_to_doc
from backend.index.opensearch_template import (
    EMBEDDING_DIMS,
    index_mappings,
    index_settings,
    index_template,
)

__all__ = [
    "EMBEDDING_DIMS",
    "bulk_actions",
    "bulk_ndjson",
    "chunk_to_doc",
    "index_mappings",
    "index_settings",
    "index_template",
]
