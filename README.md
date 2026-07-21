# Vectural

Governed RAG source of truth over a multi-language microservices estate. Answers
questions at four persona altitudes, aggregates across service boundaries, and
never presents an unverifiable claim as fact.

See [`plan/implementation-plan.md`](plan/implementation-plan.md) and
[`plan/design-document.md`](plan/design-document.md) for the full design. The
frontend is tracked separately in [`plan/ui-development-plan.md`](plan/ui-development-plan.md).

## Backend status

Seven phases implemented, all fully testable offline (no gateway, no infra):

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
- **Phase 5 — LLM routing + quota + summarisation** (§5.6, §5.7, §5.2, §5.8): the
  **single gateway egress** (model selection, prompt versioning, synchronous token
  accounting), the **quota governor** (one shared pool, weekly tranches that roll
  forward but never borrow ahead, 30% serving reserve, token bucket), the
  PostgreSQL ledger (§3.3), the failure taxonomy, and a tier-1 summarisation driver
  that **resumes without duplicate spend**. Built against a fake gateway — zero
  real spend, no network.
- **Phase 6 — the answer path** (§5.3 steps 1-4, §5.4, §5.5, R1/R2/R6): graph-planned
  retrieval (entity linking → Cypher generation → validation with retry+fallback →
  bounded scope), persona-templated synthesis, and **two independent fail-closed R1
  gates** between synthesis and release — deterministic **citation resolution** then
  a separate-call **groundedness** check. Refusal is a first-class result naming the
  likely owning services. Plus the semantic answer cache (§5.5, no-gateway fast path)
  and a `/ask` endpoint (+ SSE stream) returning the four terminal states.
- **Phase 7 — flow narratives + architect review** (§Phase 7, §4.4): identify
  recurring cross-service flows from the graph, generate tier-4 narratives (Sonnet,
  content-hash keyed), and gate them behind an **architect review workflow** — a
  narrative is authoritative only once approved, and a code change flips it to
  `needs_review` **without silently regenerating**. Only approved narratives are
  served as citable evidence (fail-closed). Architect-only `/review` endpoints
  mirror the review UI.
- **Phase 8 / §5.9 — freshness & incremental reindex** (R3): a git diff drives the
  reindex — added/modified re-chunk (skipping unchanged content hashes), deleted
  **cascades** (chunks + graph nodes + all referencing edges), renamed carries the
  summary forward when unchanged. Cascade invalidation flips affected flow narratives
  to `needs_review` (reusing Phase 7), marks files for re-summarisation, and sets a
  **visible staleness flag** on answers (serving continues, §4.4). Backstop:
  a reconciliation sweep deletes index orphans no longer in the git tree.

```
backend/          # the Python package (import root); pairs with the UI agent's frontend/
  domain/         # shared contracts: Persona, TaskType, Chunk, Node/Edge, manifest loader
  ingestion/      # walk → classify → parse (tree-sitter) → chunk → identifiers → graph deltas
  index/          # OpenSearch code_analyzer template (§4.3) + Chunk→bulk actions
  embedding/      # Embedder protocol (BGE-M3) + deterministic offline embedder
  retrieval/      # SearchBackend protocol, in-memory hybrid (BM25+kNN+RRF), rerank, service
  graph/          # Neo4j schema, Cypher validator (§5.2), extraction, structural queries (§5.5)
  llm/            # the single gateway egress: router, model config, fake+real gateway (§5.6)
  quota/          # shared-pool ledger, governor, tranches, token bucket, bin packing (§5.7)
  persistence/    # PostgreSQL system-of-record: file_ledger, quota_ledger, dead_letter, DDL (§3.3)
  summarise/      # tier-1 driver: skip/spend/hold/dead-letter spend loop (§5.2)
  answer/         # graph-planned retrieval, synthesis, citation + groundedness gates, cache (§5.4)
  flows/          # flow identification, tier-4 generation, architect review, invalidation (§Phase 7)
  freshness/      # git-diff-driven incremental reindex, cascade deletes, reconcile, staleness (§5.9)
  failures.py     # failure taxonomy: quota-exhausted / transient / content (§5.8)
  eval/           # golden-set schema, recall@k / MRR, harness (§7)
  api/            # FastAPI app: /healthz, /search, /ask (+ SSE), /review (architect), R6
  cli.py          # `vectural-ingest` — run ingestion, print a coverage summary
  demo.py         # `python -m backend.demo` — ingest+graph+search+summarise, fully offline
tests/            # offline unit tests + synthetic estate fixtures
```

The LLM routing layer is the **only** component permitted a gateway client — that
is what makes the model/licence boundary (§2) structural, not a convention.
Callers pass a rendered prompt and get back a routed, accounted response; the
quota governor gates them first, and every spend decrements one shared pool
whether it was a Haiku or a Sonnet call. Everything runs against a deterministic
`FakeGatewayClient` in tests, so the whole spend loop — including resume,
dead-letter, and quota-hold — is exercised with zero real tokens.

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

Not yet built: tiers 2-3 driver generalization (only tier-1 and tier-4 drivers
exist so far), **Temporal orchestration** to make the spend loop and the reindex
durable/resumable (§5.7 — the logic is written as pure functions ready to wrap),
and OTel→SigNoz observability (§7.1). Phase 4 (prompt calibration) and any real
spend require the company gateway and cannot be done here. The production infra
adapters still to wire behind the existing protocols/seams: the `opensearch`-backed
`SearchBackend` (incl. delete-by-query for the §5.9 cascade), the BGE-M3 `Embedder`
client (and the reranker), the `neo4j`-backed `Neo4jGraphStore` (written behind the
`neo4j` extra; its cascading detach mirrors the in-memory `delete_file`), the
psycopg-backed persistence repositories, and the real HTTP gateway client behind the
`LLMRouter` (the single egress). The Phase 6 offline path executes bounded scope via
structural graph expansion; the real backend runs the validated generated Cypher on
Neo4j. The webhook path parses real `git diff --name-status` via `git_name_status`.

## Develop

```bash
uv venv --python 3.12
uv pip install -e ".[dev]"

uv run pytest            # unit tests
uv run ruff check .      # lint
uv run mypy backend      # types

# Run ingestion over an estate (needs a manifest.yaml mapping paths -> services):
uv run vectural-ingest <estate-root> -m manifest.yaml --sample-chunks 10

# End-to-end offline (no OpenSearch/Neo4j/Postgres/gateway/model pod):
uv run python -m backend.demo <estate-root> -m manifest.yaml -q "how do refunds reverse"
uv run python -m backend.demo <estate-root> -m manifest.yaml --depends-on <service> --hops 3
uv run python -m backend.demo <estate-root> -m manifest.yaml --summarise  # Phase 5 spend loop
uv run python -m backend.demo <estate-root> -m manifest.yaml --ask "how does X work" --persona architect
uv run python -m backend.demo <estate-root> -m manifest.yaml --flows  # Phase 7 review lifecycle
uv run python -m backend.demo <estate-root> -m manifest.yaml --reindex "M<TAB>svc/f.py"  # §5.9 cascade
```

See [`manifest.example.yaml`](manifest.example.yaml) for the manifest format.
The retrieval API is served by `create_app(retrieval_service)` from
`backend.api`; the production wiring (OpenSearch backend + BGE-M3 embedder) is
the next adapter to add.
