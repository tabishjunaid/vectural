"""Core domain models shared across ingestion, summarisation, and retrieval.

Design references:
- Chunk mapping — design-document §3.2 / implementation-plan §4.3
- Graph node/edge kinds — design-document §3.1 / implementation-plan §4.2
- Node property conventions (commit_sha / prompt_version / indexed_at) — §3.1
- Personas — §5.1 / R6; TaskType — routing layer §5.1
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field, ValidationInfo, field_validator


class Persona(StrEnum):
    """The four abstraction levels the platform serves (R6)."""

    ENGINEER = "engineer"
    PRODUCT_OWNER = "po"
    BUSINESS_OWNER = "bo"
    ARCHITECT = "architect"


class Depth(StrEnum):
    """How thorough an answer to produce — orthogonal to :class:`Persona`.

    Persona sets the *altitude* (engineer vs business owner); depth sets the
    *budget* (how much evidence is gathered and how long the answer may run).
    Separate because they vary independently: an engineer may want a one-line
    answer, a business owner a thorough briefing. Deep costs materially more per
    question, so it is opt-in rather than the default."""

    BRIEF = "brief"
    STANDARD = "standard"
    DEEP = "deep"


class TaskType(StrEnum):
    """Every distinct call the routing layer (§5.1) can make.

    The routing layer maps ``task_type -> model`` as config, so this enum is the
    stable key that binding lives against. Deterministic ingestion never appears
    here — it makes no model calls at all.
    """

    FILE_SUMMARY = "file_summary"  # tier 1, Haiku
    MODULE_SUMMARY = "module_summary"  # tier 2, Haiku
    SERVICE_SUMMARY = "service_summary"  # tier 3, Sonnet
    FLOW_NARRATIVE = "flow_narrative"  # tier 4, Sonnet, human-reviewed
    ENTITY_LINKING = "entity_linking"  # retrieval step 1, Haiku
    CYPHER_GENERATION = "cypher_generation"  # retrieval step 2, Sonnet
    SYNTHESIS = "synthesis"  # answer path, Sonnet
    GROUNDEDNESS = "groundedness"  # answer path gate, Haiku


class Language(StrEnum):
    """Languages the ingestion pipeline can parse.

    Values double as the ``language`` metadata field on chunks (§3.2) and as the
    key into the tree-sitter grammar registry. ``UNKNOWN`` marks a classified but
    unparseable file — it is still walked and can still be lexically indexed.
    """

    PYTHON = "python"
    JAVASCRIPT = "javascript"
    TYPESCRIPT = "typescript"
    TSX = "tsx"
    JAVA = "java"
    GO = "go"
    RUBY = "ruby"
    CSHARP = "c_sharp"
    KOTLIN = "kotlin"
    RUST = "rust"
    UNKNOWN = "unknown"


class ChunkKind(StrEnum):
    """What a chunk's content represents.

    Code chunks are split at function/class boundaries (§5.1). ``MODULE`` covers
    file-level residue (imports, top-level statements) that belongs to no single
    definition but is still worth indexing.
    """

    FUNCTION = "function"
    CLASS = "class"
    METHOD = "method"
    MODULE = "module"


class Span(BaseModel):
    """A 1-indexed inclusive line range within a file."""

    start: int = Field(ge=1)
    end: int = Field(ge=1)

    @field_validator("end")
    @classmethod
    def _end_after_start(cls, end: int, info: ValidationInfo) -> int:
        start = info.data.get("start")
        if start is not None and end < start:
            raise ValueError(f"span end {end} precedes start {start}")
        return end

    @property
    def line_count(self) -> int:
        return self.end - self.start + 1

    def __str__(self) -> str:  # matches the "path:lines" citation form (§5.4)
        return f"{self.start}-{self.end}"


class Chunk(BaseModel):
    """A retrievable code (or doc) chunk destined for the OpenSearch index (§3.2).

    ``chunk_id`` is the stable citation target (§5.3 citation contract): an answer
    citation resolves to exactly one chunk_id. It is deterministic — derived from
    service, path, span, and content hash — so re-ingesting unchanged content
    yields the same id and never invalidates a citation gratuitously.
    """

    chunk_id: str
    service: str
    path: str
    language: Language
    kind: ChunkKind
    span: Span
    content: str
    identifiers: list[str] = Field(default_factory=list)
    symbol: str | None = None
    commit_sha: str
    content_hash: str
    doc_type: str = "code"
    # Populated later by the model-serving pod (BGE-M3); absent at ingestion time.
    embedding: list[float] | None = None


class NodeKind(StrEnum):
    """Graph node labels (§4.2). The allowed-label set the Cypher validator
    (§5.2) enforces is derived from this enum."""

    SERVICE = "Service"
    MODULE = "Module"
    FILE = "File"
    FUNCTION = "Function"
    ENDPOINT = "Endpoint"
    TOPIC = "Topic"
    CAPABILITY = "Capability"
    FLOW = "Flow"
    ADR = "ADR"


class EdgeKind(StrEnum):
    """Graph relationship types (§4.2)."""

    CALLS = "CALLS"
    PUBLISHES = "PUBLISHES"
    CONSUMES = "CONSUMES"
    EXPOSES = "EXPOSES"
    CONTAINS = "CONTAINS"
    DEFINES = "DEFINES"
    IMPLEMENTED_BY = "IMPLEMENTED_BY"
    TRAVERSES = "TRAVERSES"
    DECIDES_ON = "DECIDES_ON"


class Node(BaseModel):
    """A graph node with the property conventions every node carries (§3.1).

    ``prompt_version`` is ``None`` for deterministic (Phase 1) nodes — they carry
    no LLM-derived content. It is populated only once a summary tier writes to the
    node, which is precisely what makes ``prompt_version`` a useful staleness key.
    """

    kind: NodeKind
    key: str  # stable identity, unique within kind (e.g. service name, path, path#symbol)
    properties: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
    commit_sha: str
    indexed_at: datetime
    prompt_version: str | None = None


class Edge(BaseModel):
    """A directed graph relationship between two node keys."""

    kind: EdgeKind
    src_kind: NodeKind
    src_key: str
    dst_kind: NodeKind
    dst_key: str
    properties: dict[str, str | int | float | bool | None] = Field(default_factory=dict)


class GraphDelta(BaseModel):
    """The graph-side output of ingesting one file: nodes and edges to upsert.

    Kept separate from chunks because the two land in different stores (Neo4j vs
    OpenSearch, §2 of the design doc) and the freshness pipeline (§5.9) invalidates
    them on different cadences.
    """

    nodes: list[Node] = Field(default_factory=list)
    edges: list[Edge] = Field(default_factory=list)

    def extend(self, other: GraphDelta) -> None:
        self.nodes.extend(other.nodes)
        self.edges.extend(other.edges)
