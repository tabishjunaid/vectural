"""Phase 1 deterministic ingestion pipeline (implementation-plan §5.1).

Walk → classify → parse (tree-sitter) → chunk at function/class boundaries →
extract identifiers → emit chunks and graph deltas. **Zero LLM calls** live in
this package; that is the load-bearing property below the "zero LLM calls" line
in the design-document §4.1 sequence.
"""

from backend.ingestion.classify import classify_path
from backend.ingestion.pipeline import FileResult, IngestionResult, ingest_file, ingest_tree
from backend.ingestion.walker import walk_estate

__all__ = [
    "FileResult",
    "IngestionResult",
    "classify_path",
    "ingest_file",
    "ingest_tree",
    "walk_estate",
]
