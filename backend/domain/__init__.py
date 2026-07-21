"""Shared domain contracts.

These types are the data contracts that the design document (§3, §5) makes
load-bearing: chunks flow to OpenSearch, graph deltas flow to Neo4j, and the
manifest is the only human-authored path→node mapping. Keeping them in one
place means ingestion, summarisation, and retrieval cannot drift on the shape
of the data they exchange.
"""

from backend.domain.manifest import Manifest, ServiceManifest, load_manifest
from backend.domain.models import (
    Chunk,
    ChunkKind,
    Edge,
    EdgeKind,
    GraphDelta,
    Language,
    Node,
    NodeKind,
    Persona,
    Span,
    TaskType,
)

__all__ = [
    "Chunk",
    "ChunkKind",
    "Edge",
    "EdgeKind",
    "GraphDelta",
    "Language",
    "Manifest",
    "Node",
    "NodeKind",
    "Persona",
    "ServiceManifest",
    "Span",
    "TaskType",
    "load_manifest",
]
