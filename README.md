# Vectural

Governed RAG source of truth over a multi-language microservices estate. Answers
questions at four persona altitudes, aggregates across service boundaries, and
never presents an unverifiable claim as fact.

See [`plan/implementation-plan.md`](plan/implementation-plan.md) and
[`plan/design-document.md`](plan/design-document.md) for the full design. The
frontend is tracked separately in [`plan/ui-development-plan.md`](plan/ui-development-plan.md).

## Backend status

Three phases implemented, all fully testable offline (no gateway, no infra):

- **Phase 1 — deterministic ingestion** (implementation-plan §5.1): estate → chunks
  + graph skeleton, **zero LLM calls**. The load-bearing foundation — bad chunk
  boundaries corrupt every downstream tier (design-document §2.1).
- **Phase 2 — retrieval slice** (§5.3 steps 5-6, §Phase 2): hybrid BM25 + k-NN
  search with RRF fusion, rerank, a FastAPI `/search` that returns **ranked
  chunks with no LLM synthesis**, and the eval harness (recall@k, MRR) that lets
  retrieval quality be measured before any token is spent on answers.
- **Phase 3 — graph construction** (§Phase 3, §5.2, §5.5): Neo4j schema/constraints,
  the **Cypher validation layer** (allowed labels, hop cap, read-only), call-graph
  / endpoint / topic extraction, and structural fast-path queries — "what depends
  on service X, three hops out" answered directly from the graph, still no LLM.

```
backend/          # the Python package (import root); pairs with the UI agent's frontend/
  domain/         # shared contracts: Persona, TaskType, Chunk, Node/Edge, manifest loader
  ingestion/      # walk → classify → parse (tree-sitter) → chunk → identifiers → graph deltas
  index/          # OpenSearch code_analyzer template (§4.3) + Chunk→bulk actions
  embedding/      # Embedder protocol (BGE-M3) + deterministic offline embedder
  retrieval/      # SearchBackend protocol, in-memory hybrid (BM25+kNN+RRF), rerank, service
  graph/          # Neo4j schema, Cypher validator (§5.2), extraction, structural queries (§5.5)
  eval/           # golden-set schema, recall@k / MRR, harness (§7)
  api/            # FastAPI app: /healthz, /search (persona-threaded, R6)
  cli.py          # `vectural-ingest` — run ingestion, print a coverage summary
  demo.py         # `python -m backend.demo` — ingest+graph+index+search, fully offline
tests/            # offline unit tests + synthetic estate fixtures
```

Graph construction layers extracted relationships (`CALLS`, `PUBLISHES`/`CONSUMES`,
`EXPOSES`) on top of the Phase-1 skeleton: cross-service calls are resolved from
call sites (a call to a symbol defined in another service is a dependency edge),
topics from `publish`/`subscribe` sites, endpoints from OpenAPI specs. Resolution
is conservative — library/framework calls are dropped, not invented as edges. The
**Cypher validator** guards every LLM-generated query before it reaches Neo4j; the
structural fast-path query builders self-validate, so the no-LLM path can never
emit an unsafe query either.

Ingestion: `walk_estate` (manifest-driven, prunes vendored/build dirs, skips
binaries) → `classify_path` → tree-sitter parse → `chunk_source` (function/class
boundaries, decorators and `export const … =>` captured whole, class header +
method split) → identifier extraction → `ingest_tree`, which emits chunks for
OpenSearch plus a `Service -CONTAINS-> Module -CONTAINS-> File -DEFINES-> Function`
graph skeleton for Neo4j. Persistence lives in later phases; ingestion returns
in-memory results so it stays testable in isolation.

Retrieval is defined against protocols (`Embedder`, `SearchBackend`, `Reranker`)
so the same `RetrievalService` and FastAPI app run over the real OpenSearch +
BGE-M3 stack in production and over an in-memory hybrid backend + hashing
embedder in tests. The service scope is a **hard filter** (§5.3 step 5), matching
the graph-plan constraint the full pipeline will apply upstream.

Supported languages: Python, JavaScript, TypeScript, TSX, Java, Go, Ruby, C#,
Kotlin, Rust. Unsupported/unknown files still get whole-file module chunks so
they remain lexically searchable.

Not yet built (later phases): the LLM routing layer and gateway egress (§5.6),
summarisation tiers (§5.2), graph-planned retrieval steps 1-4 (§5.3), the answer
path with citation/groundedness gates (§5.4), quota governance (§5.7), and
Temporal orchestration (§5.7). The production infra adapters still to wire behind
the existing protocols/seams are the `opensearch`-backed `SearchBackend`, the
BGE-M3 `Embedder` client, and the `neo4j`-backed graph store (`Neo4jGraphStore`,
already written behind the `neo4j` extra — untested here as it needs a database).

## Develop

```bash
uv venv --python 3.12
uv pip install -e ".[dev]"

uv run pytest            # unit tests
uv run ruff check .      # lint
uv run mypy backend      # types

# Run ingestion over an estate (needs a manifest.yaml mapping paths -> services):
uv run vectural-ingest <estate-root> -m manifest.yaml --sample-chunks 10

# End-to-end offline: ingest + graph + index + search (no OpenSearch/Neo4j/model pod):
uv run python -m backend.demo <estate-root> -m manifest.yaml -q "how do refunds reverse"
uv run python -m backend.demo <estate-root> -m manifest.yaml --depends-on <service> --hops 3
```

See [`manifest.example.yaml`](manifest.example.yaml) for the manifest format.
The retrieval API is served by `create_app(retrieval_service)` from
`backend.api`; the production wiring (OpenSearch backend + BGE-M3 embedder) is
the next adapter to add.
