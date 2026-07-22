# Vectural

Governed RAG source of truth over a multi-language microservices estate. Answers
questions at four persona altitudes, aggregates across service boundaries, and
never presents an unverifiable claim as fact.

See [`plan/implementation-plan.md`](plan/implementation-plan.md) and
[`plan/design-document.md`](plan/design-document.md) for the full design. The
frontend is tracked separately in [`plan/ui-development-plan.md`](plan/ui-development-plan.md).

## Starting the application

The React frontend and the FastAPI backend run as an integrated system. There are
three ways to start it, from simplest to full. In every mode the LLM gateway is the
`FakeGatewayClient` — the real gateway is the one adapter that can't be exercised
here, per the §2 licence boundary — so answer/summary *wording* is placeholder while
retrieval, citations, grounding/refusal, coverage, and review are all real.

### A. Quick demo — in-memory, no infrastructure

Boots on the bundled `sample-estate/`, indexing + summarising in RAM against the
fake gateway. Nothing external required.

```bash
# Whole demo stack in Docker (backend + frontend + Postgres + Valkey):
docker compose up --build            # → UI http://localhost:5175, API http://localhost:8000

# …or locally, two terminals:
uv run uvicorn backend.asgi:app --port 8000          # backend
npm --prefix frontend run dev                        # frontend → http://localhost:5175
```

This serves the sample estate (services `gateway` / `ledger` / `notifications`) — a
self-contained walkthrough of every surface.

### B. Full stack in Docker, serving *your* indexed estate

One command brings up every container serving an estate you indexed with the durable
worker (see [Indexing the knowledge base](#indexing-the-knowledge-base-durable-quota-partitioned)).
The backend image bundles the real-adapter extras (opensearch/neo4j/postgres) + temporal,
so the same image serves the API over the real stores **and** hosts the indexing worker.

1. Point the backend at your estate with a **local override** (the estate path is
   machine-specific — keep this file out of git). Create `docker-compose.override.yml`:

   ```yaml
   services:
     backend:
       environment:
         VECTURAL_BACKING: real
         VECTURAL_ESTATE_ROOT: /estate
         VECTURAL_MANIFEST_PATH: /estate/manifest.yaml
         VECTURAL_NEO4J_PASSWORD: vecturalpw
       volumes:
         - /abs/path/to/your/estate:/estate:ro   # ← edit for your machine
   ```

2. Bring up everything — core + datastores + Temporal:

   ```bash
   docker compose --profile datastores --profile indexing up -d --build
   ```

3. If the stores are empty, **index the estate first** (the backend serves
   connect-only — it never indexes on boot). See the Indexing section, then open the UI.

| URL | What |
|-----|------|
| **http://localhost:5175** | The app — Ask / Coverage / Review |
| http://localhost:8000/docs | Backend Swagger — try endpoints directly |
| http://localhost:8080 | Temporal UI — indexing workflow history |
| http://localhost:7474 | Neo4j browser (`neo4j` / `vecturalpw`) |

Stop it all with `docker compose --profile datastores --profile indexing down`.

### C. Local dev over the real stores (hot reload)

Run the datastores in Docker but the backend + frontend as local processes:

```bash
docker compose --profile datastores --profile indexing up -d postgres valkey opensearch neo4j temporal
VECTURAL_BACKING=real \
  VECTURAL_ESTATE_ROOT=/abs/path/to/estate \
  VECTURAL_MANIFEST_PATH=/abs/path/to/estate/manifest.yaml \
  uv run uvicorn backend.asgi:app --reload --port 8000
npm --prefix frontend run dev                        # → http://localhost:5175
```

`VECTURAL_BACKING=real` uses OpenSearch (chunks/hybrid search), Neo4j (graph +
structural queries), and Postgres (ledgers, summaries, flows, quota). The adapters
live behind the same protocols the in-memory ones satisfy
(`backend/retrieval/opensearch_backend.py`, `backend/graph/neo4j_store.py`,
`backend/persistence/*.py`), selected in `backend/bootstrap.py`, and their
integration tests run against the live containers:

```bash
VECTURAL_RUN_INTEGRATION=1 uv run pytest tests/test_real_adapters.py tests/test_quota_ledger.py
```

Live surfaces: **Ask** (`/ask` — cited answers, refusal, cache), **Coverage**
(`/coverage`), **Review** (`/review` — approve a flow narrative and watch that
service jump to tier 4 on Coverage, the §5.4 single source of truth), and
`/metrics`. The frontend API client lives in `frontend/src/lib/api.ts`; the
runnable backend is `backend/bootstrap.py` + `backend/asgi.py`.

## Using real models

Two components ship as offline stand-ins so the stack runs with no models or spend:
the **embedder** (`HashingEmbedder`, non-semantic) and the **LLM gateway**
(`FakeGatewayClient`, canned answers). Each is swapped via config.

### Real embeddings — BGE-M3 (semantic search)

`HashingEmbedder` makes dense/kNN search noise, so conceptual queries miss. Real
**BGE-M3** (`BAAI/bge-m3`, local, 1024-dim — the same dim as the index, so no schema
change) makes retrieval semantic. Set `VECTURAL_EMBEDDER=bge-m3` for **both** the
worker and the API (index-time and query-time must match), then re-index. In Docker
the model downloads once into the `hf-cache` volume:

```bash
# .env: VECTURAL_BACKING=real, VECTURAL_EMBEDDER=bge-m3, VECTURAL_ESTATE_HOST_PATH=…
docker compose --profile datastores --profile indexing up -d --build
curl -X DELETE localhost:9200/vectural-chunks-code          # drop hash vectors
docker compose run --rm backend vectural-index --wait       # worker re-embeds with BGE-M3
```

### Plugging in your LLM gateway (§2)

The **§2 licence boundary** is load-bearing: the platform has exactly one outbound
LLM client, and *this codebase never ships it* — you supply your own. Implement the
one-method `GatewayClient` protocol (`backend/llm/base.py`) and point at it:

```python
# your_pkg/gateway.py
from backend.llm.base import GatewayClient, GatewayRequest, GatewayResult

class MyGateway:  # satisfies backend.llm.base.GatewayClient
    def complete(self, request: GatewayRequest) -> GatewayResult:
        # your HTTP call to your model, using your own endpoint + key (from env):
        #   text, in_tokens, out_tokens = call_your_gateway(request.model, request.prompt, …)
        return GatewayResult(text=text, input_tokens=in_tokens, output_tokens=out_tokens)
```

```bash
VECTURAL_GATEWAY=real
VECTURAL_GATEWAY_CLIENT=your_pkg.gateway:MyGateway   # dotted path; dependency-injected
```

`build_gateway` (`backend/llm/factory.py`) only *loads* your class — it makes no
network calls and handles no credentials; your client owns the endpoint/key. With it
set, `/ask` answers come from your model instead of the canned template.

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
- **Durable orchestration** (§5.7): the indexing spend loop modelled as a Temporal
  parent→child workflow — services in priority order, per-service child activity
  (service atomicity), **checkpoint after each service**, quota **park** (durable
  timer, not an error) on hold, and **continue-as-new** at weekly tranche boundaries.
  A killed workflow **resumes from its checkpoint and re-spends nothing** (the Phase 5
  exit criterion), because completed files are skipped via the file_ledger. The real
  `temporalio` worker is a thin marked adapter over this deterministic logic.

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
  summarise/      # tiers 1-3 drivers: file/module/service, content-hash cascade keying (§5.2)
  answer/         # graph-planned retrieval, synthesis, citation + groundedness gates, cache (§5.4)
  flows/          # flow identification, tier-4 generation, architect review, invalidation (§Phase 7)
  freshness/      # git-diff-driven incremental reindex, cascade deletes, reconcile, staleness (§5.9)
  orchestration/  # durable indexing workflow: checkpoint, resume, continue-as-new, park (§5.7)
  observability/  # metrics collector (token/persona/latency/refusal), /metrics, OTel seam (§7.1)
  failures.py     # failure taxonomy: quota-exhausted / transient / content (§5.8)
  eval/           # golden-set schema, recall@k / MRR, harness (§7)
  api/            # FastAPI app: /healthz, /search, /ask (+ SSE), /review, /metrics (R6)
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

All of the plan's pipeline logic is now built and tested offline. What remains is
**infrastructure adapters and real spend**, none of which can be exercised in this
environment — each is a clearly-marked seam behind an existing protocol/extra:
the `opensearch`-backed `SearchBackend` (incl. delete-by-query for the §5.9
cascade), the BGE-M3 `Embedder` client (and the reranker), the `neo4j`-backed
`Neo4jGraphStore` (behind the `neo4j` extra; its cascading detach mirrors the
in-memory `delete_file`), the psycopg-backed persistence repositories, the real
HTTP gateway client behind the `LLMRouter` (the single egress), the `temporalio`
worker (behind the `temporal` extra) over the deterministic workflow, and the
OTel→SigNoz exporter (behind the `otel` extra) over the `MetricsCollector`
snapshot. **Phase 4** (prompt calibration) and any **real token spend** require
the company gateway and are out of scope by the §2 licence boundary. The Phase 6
offline path executes bounded scope via structural graph expansion; the real
backend runs the validated generated Cypher on Neo4j. The webhook path parses real
`git diff --name-status` via `git_name_status`.

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
uv run python -m backend.demo <estate-root> -m manifest.yaml --orchestrate  # §5.7 kill+resume
```

See [`manifest.example.yaml`](manifest.example.yaml) for the manifest format.
The retrieval API is served by `create_app(retrieval_service)` from `backend.api`.

## Estate tooling

### Guided setup (recommended)

`vectural-init` runs the three initial-setup stages in one flow — **clone** the
estate, **derive the manifest**, then **estimate the token cost** — before any
gateway spend. Interactive by default; every prompt has a flag. Idempotent, so
re-running just refreshes.

```bash
uv run vectural-init                                  # fully interactive
uv run vectural-init --path ./estate --parent https://github.com/acme --source-only
uv run vectural-init --path ./estate --skip-clone     # estate already on disk
```

Stage flags: `--shallow`, `--include-archived`, `--dry-run` (stage 1);
`--source-only`, `--exclude`, `--exclude-glob`, `--monthly-budget` (stage 3).
The estimate report goes to stdout, progress/guidance to stderr, so
`vectural-init … --skip-clone > estimate.txt` captures just the numbers.

### Individual stages

```bash
# Stage 1 — clone every repo under a Git org/group into one root (prompts for
# path + URL; GitHub via gh/API, GitLab via API incl. subgroups; idempotent):
uv run vectural-clone                                 # interactive prompts
uv run vectural-clone --path ./estate --parent https://github.com/acme

# Stage 3 — estimate the indexing token cost BEFORE spending anything (§Phase 0, §5.2.1):
# embedding workload + gateway summarisation tokens (instruction overhead broken
# out) + a weekly bin-packed plan for the quota governor.
# Prompts for the estate path if omitted; if there's no manifest.yaml yet (e.g. a
# freshly-cloned estate) it derives one service per top-level directory.
uv run vectural-estimate                              # interactive
uv run vectural-estimate ./estate                     # no manifest needed
uv run vectural-estimate ./estate --write-manifest    # also save the derived manifest
                                                       # (writes manifest.draft.yaml if one exists)
uv run vectural-estimate ./estate -m ./estate/manifest.yaml --json

# Scope to source only (docs/config/data inflate the token count):
uv run vectural-estimate ./estate --source-only                 # only recognised languages
uv run vectural-estimate ./estate --exclude '.md,.json,.lock'   # drop specific extensions
uv run vectural-estimate ./estate --exclude-glob '*generated*,*.min.js,dist/*'
```

## Indexing the knowledge base (durable, quota-partitioned)

Real indexing is **not** done at app boot — it's a separate, resumable, Temporal-hosted
job so it can be split into weekly tranches under the gateway quota (§5.7). Each service
is one governed, atomic unit: embed+index its chunks (OpenSearch), load its graph (Neo4j),
and summarise it tier 1→2→3 (files → modules → service, gateway). Once every service is
indexed, a finalize step completes the graph (cross-service edges) and generates the
cross-service **flow narratives** (tier 4, left pending architect review). It parks when a
tranche is exhausted and resumes when the next unlocks, with **zero duplicate spend** on
restart (file-ledger + content-hash idempotency across every tier). Summaries and flows are
persisted in Postgres (`summaries`, `flow_narratives`), and the shared quota pool in
`quota_ledger`, so the worker and the serving API draw from one durable source.

```bash
# 1. Bring up the datastores + Temporal (its own Postgres + UI on :8080):
docker compose --profile datastores --profile indexing up -d

# 2. Run one or more workers (host the workflow + activities against the real stores):
VECTURAL_ESTATE_ROOT=./estate VECTURAL_MANIFEST_PATH=./estate/manifest.yaml \
  uv run vectural-indexer-worker

# 3. Start / resume the run — partitions by the estimator's weekly plan, stable id per estate:
uv run vectural-index --dry-run     # print the tranche partition only
uv run vectural-index --wait        # kick off (or resume) and block until done
```

Re-running `vectural-index` attaches to an in-flight run (resume) or starts a fresh one if the
last completed — either way already-indexed work is skipped. Watch history / continue-as-new /
park timers in the Temporal UI at http://localhost:8080.

Then boot the API **connect-only** (it serves what the worker indexed; it never re-indexes):

```bash
VECTURAL_BACKING=real VECTURAL_ESTATE_ROOT=./estate uv run uvicorn backend.asgi:app --port 8000
```

> The gateway stays on the fake client (the §2 licence boundary). Wiring the real gateway +
> BGE-M3 embeddings is the remaining seam; everything else (structural indexing, tiers 1-3,
> flow narratives) runs through the durable loop and is served from Postgres.
