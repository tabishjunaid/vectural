"""Vectural — governed RAG source of truth over a multi-language microservices estate.

This package currently implements the Phase 1 deterministic ingestion slice
(implementation-plan §5.1, design-document §4.1 sequence above the "zero LLM
calls" line). Nothing in this package reaches an LLM gateway or an external
datastore — that boundary is enforced by construction, not convention.
"""

__version__ = "0.1.0"
