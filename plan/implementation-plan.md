# Vectural — Implementation Plan

**Status:** Draft for review — pre-development
**Scope:** RAG-based information retrieval over the multi-language microservices estate, serving engineers, product owners, business owners, and architects as a governed source of truth.

---

## 1. Objectives and non-negotiables

### 1.1 Objective

Vectural is a single retrieval system over the entire codebase (frontend and backend, multiple languages) that answers questions at four levels of abstraction, aggregates across service boundaries, and never presents an unverifiable claim as fact.

### 1.2 Non-negotiable requirements

| # | Requirement | Consequence for design |
|---|---|---|
| R1 | Accuracy cannot be compromised | Mandatory citations, groundedness verification, explicit refusal path, eval gating |
| R2 | Cross-service aggregation | Graph plans retrieval; vectors fetch evidence within the plan |
| R3 | No stale data | Commit-driven incremental reindex with cascading deletes and reconciliation sweep |
| R4 | Fully open-source stack | Self-hosted on Kubernetes; no BSL, SSPL, or source-available components |
| R5 | Constrained gateway quota | Quota governance is a first-class subsystem, not an optimisation |
| R6 | Four personas | Persona routing selects retrieval altitude and answer template |

### 1.3 Explicitly out of scope for v1

- Code generation or modification suggestions
- Sourcegraph-grade exact symbol search (revisit in Phase 8 if evals demand it)
- Multi-tenancy or external access
- Any use of the platform as a system of record — Neo4j and OpenSearch hold derived data only

---

## 2. Model and licence governance

This separation is a compliance boundary, not a preference. It must hold for the life of the project.

| Model | Licence | May see | Used for |
|---|---|---|---|
| Opus | Personal Claude licence | **No company code, ever** | Writing application code; iterating prompt templates against synthetic or open-source samples |
| Sonnet | Company AI gateway | Company code | Service summaries, flow narratives, Cypher generation, answer synthesis |
| Haiku | Company AI gateway | Company code | File summaries, module summaries, entity linking, groundedness checking |

**Control:** the application has exactly one outbound LLM client (the routing layer, §5.6). It is configured with the gateway endpoint only. There is no code path from the application to a personal endpoint. Development happens in a separate workspace with no access to the codebase mount.

### 2.1 Gateway access and quota (confirmed)

| Property | Value |
|---|---|
| Access path | Claude Code via the company AI gateway, authenticated with an API token |
| Credentials | Gateway endpoint and API key configured in a `.claude` directory at the root |
| Quota scope | **Per user** |
| Quota period | **Monthly, resetting each month** |
| Model pool | **Shared across models**; the model is selectable per call |

Three consequences follow directly:

- **A shared pool means Haiku and Sonnet trade against each other.** Every Sonnet call is Haiku capacity forgone. This strengthens rather than weakens the tier design in §5.2 — pushing volume down to Haiku is not just cheaper, it directly buys Sonnet headroom for synthesis.
- **A monthly reset raises the cost of overspending.** Exhausting the pool in week one leaves three weeks with no interactive serving, not one. Pacing (§5.7) is therefore a correctness requirement, not a refinement.
- **Quota is attached to a person, not a service account.** Before Phase 5, confirm whether a dedicated application identity can be provisioned. Running production indexing against an individual's allocation is a single point of failure and a governance problem — see §9.

---

## 3. Technology stack (agreed)

| Layer | Choice | Licence | Rationale |
|---|---|---|---|
| Parsing | tree-sitter (`py-tree-sitter`) | MIT | One toolchain across all languages; error-tolerant |
| Backend | Python 3.12+, FastAPI, Pydantic | MIT | Latency is LLM-bound, not CPU-bound; ecosystem is decisive |
| Graph | **Neo4j Community 2026.x** | GPLv3 | Only mature OSI-open graph DB; Cypher → ISO GQL; best LLM query generation |
| Chunk retrieval | **OpenSearch** | Apache 2.0 | Native hybrid BM25 + k-NN with RRF; index aliases; snapshot lifecycle |
| Relational | PostgreSQL | PostgreSQL | Ledger, cache, evals, Temporal persistence |
| Cache / queue | **Valkey** (not Redis) | BSD | Redis is no longer open source |
| Orchestration | Temporal | MIT | Durable execution, resumability, quota-aware long-running workflows |
| Embeddings | BGE-M3 | MIT | Multilingual, code-capable, 8k context, dense + sparse |
| Reranking | BGE-reranker-v2-m3 | MIT | Highest accuracy-per-infrastructure component in the system |
| Model serving | vLLM or Infinity | Apache 2.0 / MIT | Batched, containerised, no bespoke serving code |
| Frontend | React + TypeScript, SSE | MIT | Persona switcher as first-class control |
| Observability | SigNoz (OSS core) + OpenTelemetry | MIT (core); avoid `ee/` module (proprietary) | OTel-native, self-hosted, single backend for metrics/traces/logs; open-core pattern mirrors Neo4j Community |

**Rejected and why:** Memgraph and ArangoDB (BSL, not open source), FalkorDB (SSPLv1, source-available), Kùzu (MIT but archived October 2025 following Apple acqui-hire; forks immature), ArcadeDB (Apache 2.0 but small ecosystem and bus-factor risk), Apache AGE (partial Cypher, weak deep traversal), JanusGraph and NebulaGraph (heavy ops, non-Cypher), Elasticsearch (licensing track record), Qdrant and Vespa (would add a store or a learning curve without solving a measured problem).

---

## 4. Data architecture

### 4.1 Store responsibilities

| Store | Holds | Rebuildable? |
|---|---|---|
| **Neo4j** | Services, files, functions, endpoints, call/emit/consume edges; service and capability summaries with vector index; flow narratives | Yes — from git + Postgres |
| **OpenSearch** | Code chunks with hybrid index; identifier fields; ADR and doc chunks | Yes — from git |
| **PostgreSQL** | `repo_state`, `file_ledger`, `quota_ledger`, `answer_cache`, `eval_runs`, `dead_letter`, `coverage_manifest`, Temporal persistence | **No — source of truth** |

### 4.2 Graph model (initial)

```
(:Service)-[:CALLS]->(:Service)
(:Service)-[:PUBLISHES|CONSUMES]->(:Topic)
(:Service)-[:EXPOSES]->(:Endpoint)
(:Service)-[:CONTAINS]->(:Module)-[:CONTAINS]->(:File)-[:DEFINES]->(:Function)
(:Capability)-[:IMPLEMENTED_BY]->(:Service)
(:Flow)-[:TRAVERSES {step:int}]->(:Service)
(:ADR)-[:DECIDES_ON]->(:Service|:Capability)
```

Every node carries `commit_sha`, `prompt_version`, `indexed_at`. Vector index on `Capability.embedding`, `Service.summary_embedding`, `Flow.embedding`.

### 4.3 OpenSearch analyzer (required)

Standard analyzers tokenise `processRefundReversal` as a single term, so a search for "refund reversal" returns nothing. This must be set at index-template time, before any bulk indexing.

```json
{
  "analysis": {
    "filter": {
      "code_delimiter": {
        "type": "word_delimiter_graph",
        "preserve_original": true,
        "catenate_words": true,
        "split_on_case_change": true,
        "split_on_numerics": true
      }
    },
    "analyzer": {
      "code_analyzer": {
        "tokenizer": "whitespace",
        "filter": ["code_delimiter", "flatten_graph", "lowercase"]
      }
    }
  }
}
```

Chunk mapping: `content` (code_analyzer) + `content.exact` (keyword) + `identifiers` (code_analyzer, boosted) + `embedding` (knn_vector, 1024 dims) + metadata (`service`, `path`, `lines`, `commit_sha`, `language`).

### 4.4 Codebase layout

All repositories under one root, one directory per repository, plus a checked-in `manifest.yaml`:

```yaml
services:
  - name: booking-api
    path: booking-api
    language: java
    art: retail-art
    criticality: tier-1
    owner: <team>
```

The manifest is human-owned, reviewed like code, and is what maps filesystem paths to graph nodes.

---

## 5. Component design

### 5.1 Ingestion pipeline (no LLM)

Walk → classify → parse (tree-sitter) → chunk at function/class boundaries → extract identifiers → build call graph → emit chunks and graph deltas. Also ingests OpenAPI specs, READMEs, CI configs, infra manifests, and ADRs (reusing the existing Confluence → markdown `ParsedADR` pipeline).

### 5.2 Summarisation tiers

| Tier | Model | Input | Output |
|---|---|---|---|
| 1 — File | Haiku | Parsed file + imports | Purpose, key operations, business concepts, external calls (JSON) |
| 2 — Module | Haiku | All tier-1 summaries in module | Module responsibility |
| 3 — Service | Sonnet | All tier-2 summaries + OpenAPI + README | Plain-language business description |
| 4 — Flow | Sonnet | Graph traversal path + tier-3 summaries | Cross-service business narrative, **human-reviewed** |

All keyed by content hash and prompt version. A change to a tier-1 prompt invalidates every file summary — treat prompt changes as budgeted events.

#### 5.2.1 Instruction overhead is fully billed

The gateway does not support prompt caching (§9.3), so the instruction template is re-billed on every call. At tier 1 this is multiplied by file count: a 400-token template across ~50,000 files is ~20M input tokens — roughly 12–15% of the projected first-pass cost — paid entirely for repeating instructions the model has already been sent tens of thousands of times.

Three design consequences:

- **Treat tier-1 prompt length as a budget line.** Every token added to that template costs file-count tokens. Terse schema over prose explanation; one minimal example rather than few-shot; no rationale or style guidance that the output schema already enforces.
- **Batch small files into single calls.** Several small files sharing one instruction block amortises the overhead across all of them. Files below a size threshold (tune in Phase 4) are grouped, with the response schema keyed by file path. This is the single largest available saving on tier-1 overhead.
- **Measure overhead separately in Phase 4 calibration.** Record instruction tokens and content tokens as distinct figures so the trade-off between prompt richness and cost is visible rather than inferred. `prompt_overhead_tokens` in `index_cost_estimator.py` is the corresponding lever.

Tiers 2–4 are unaffected in practice — call volume is three orders of magnitude lower.

### 5.3 Retrieval (graph-planned)

1. **Entity linking** (Haiku + capability vector search) → graph anchors
2. **Cypher generation** (Sonnet, schema in prompt) → traversal
3. **Validation** — parse the generated Cypher; allowed labels only, hop cap, read-only; one retry then templated fallback
4. **Execution** (Neo4j) → in-scope service set
5. **Scoped evidence** (OpenSearch, filtered to that set) → top-k
6. **Rerank** (BGE cross-encoder) → 3–5 chunks

### 5.4 Answer path

Synthesis (Sonnet, persona template, citations mandatory) → citation resolution check (deterministic: every citation must resolve to a retrieved chunk) → groundedness check (Haiku, separate call, claim by claim) → release or refuse.

**Fail closed.** Unsupported claims mean the answer is withheld and the user receives a scoped "no reliable coverage" response naming the likely owning services.

### 5.5 Fast paths (no gateway call)

- Semantic answer cache (local embedding, Valkey-backed)
- Structural graph queries ("which services call X", "where is Y defined")
- Coverage lookups

Target: 30–40% of queries terminate without a gateway call once the cache warms.

### 5.6 LLM routing layer

One internal client, task-type parameter, owning three concerns:

- **Model selection** — swapping a model is a config change
- **Prompt versioning** — every template versioned, logged per call, gated by evals
- **Token accounting** — per call, task type, and persona; emitted to SigNoz in real time

JSON mode and near-zero temperature everywhere except answer synthesis.

### 5.7 Quota governance

The quota is a single monthly per-user pool shared across models (§2.1). Two nested periods therefore apply: **the budget replenishes monthly, but is spent on a weekly pace.**

- **Serving reserve** — a fixed fraction of the monthly quota (default 30%) is ring-fenced for interactive queries; indexing checks remaining budget before each batch and stops at the floor
- **Monthly replenishment** — a durable Temporal timer aligned to the reset date; the workflow is always running, moving at the speed the budget allows
- **Weekly sub-allocation** — the indexing share is divided into four weekly tranches. Unspent tranche capacity rolls forward within the month; a tranche is never borrowed against in advance. This is what prevents a week-one sprint from leaving three weeks dark
- **Token bucket** — within a tranche, spend is smoothed across days so interactive latency stays stable
- **Shared-pool accounting** — one counter, not one per model. Sonnet and Haiku spend decrement the same budget
- **Service atomicity** — a service is either fully indexed across all four tiers or absent from the index; never partial
- **Bin packing** — services sorted by measured cost, packed into weekly tranches (see `index_cost_estimator.py`)

### 5.8 Failure taxonomy

| Class | Response |
|---|---|
| Quota exhausted | Not an error. Durable timer until replenishment. No retry, no alert |
| Transient gateway (502, timeout) | Temporal retry policy, exponential backoff |
| Content failure (parse error, malformed output, oversized file) | Dead-letter to Postgres, continue. Reviewed weekly |

### 5.9 Freshness

Post-merge webhook → Temporal workflow → `git diff --name-status <last_sha>..HEAD`:

- **Added/modified** → re-parse, re-chunk, re-embed (skip unchanged content hashes)
- **Deleted** → cascade: chunks from OpenSearch, nodes and **all referencing edges** from Neo4j
- **Renamed** → delete + add, carrying forward the summary if content hash matches

Cascade invalidation upward: file → module → service regenerate automatically; **flow narratives are marked `needs_review` and the owning architect is notified, never silently regenerated**. Serving continues on the previous version with a visible staleness indicator.

Backstops: nightly full-diff run for missed webhooks; periodic reconciliation sweep comparing the git tree against the index and deleting orphans.

---

## 6. Phased delivery

Durations assume one engineer with Opus assistance. Each phase has a hard exit criterion.

### Phase 0 — Confirm and provision (Weeks 1–2)

- Resolve every open question in §9
- Kubernetes namespace, Helm charts for Postgres, OpenSearch, Neo4j, Valkey, Temporal, model-serving pod, SigNoz + ClickHouse (OSS core only — `ee/` module never enabled)
- Codebase mount + `manifest.yaml` first draft
- Run `index_cost_estimator.py` (Phase 1 inventory only, zero tokens)

**Exit:** all infrastructure reachable; inventory numbers in hand; gateway quota mechanics confirmed in writing.

### Phase 1 — Deterministic ingestion (Weeks 2–4)

- tree-sitter pipeline across all estate languages
- Chunker at function/class boundaries, identifier extraction
- OpenSearch index template with `code_analyzer` (§4.3) — **before any bulk indexing**
- BGE-M3 and reranker serving pod
- Full non-LLM index of the entire estate

**Exit:** every source file chunked, embedded, and searchable. Zero gateway tokens spent.

### Phase 2 — Thin slice: engineer persona (Weeks 4–6)

- FastAPI service, hybrid retrieval + rerank, no LLM synthesis (return ranked chunks)
- Minimal React UI
- **Eval harness stood up** — 50 engineer questions with known-correct file targets, measuring recall@10 and MRR

**Exit:** retrieval quality measured and acceptable before a single token is spent on summarisation. This is the cheapest possible point to discover the chunking strategy is wrong.

### Phase 3 — Graph construction (Weeks 6–9)

- Call graph, endpoint, and topic extraction from AST + OpenAPI + infra manifests
- Neo4j schema, constraints, indexes
- Cypher validation layer (allowed labels, hop cap, read-only)
- Structural fast-path queries end to end — still no LLM

**Exit:** "what depends on service X, three hops out" answers correctly and in under 200ms. Graph rebuild-from-scratch tested and timed.

### Phase 4 — Calibration and quota plan (Week 9)

- Tier-1 prompt iterated with **Opus against open-source Java**, not company code
- Calibration run: tier 1 against a stratified 200-file sample, measuring real `chars_per_token` and output sizes
- Re-run estimator with `--calibration`; produce the weekly plan
- **Formal quota allocation agreed with the gateway team**

**Exit:** signed-off monthly indexing budget with weekly tranches, per-service coverage schedule, prompt v1 frozen.

### Phase 5 — Summarisation at scale (Weeks 10–16, quota-paced)

- Temporal parent workflow + child workflow per service; `continue-as-new` at weekly checkpoints for history hygiene, with the replenishment timer on the monthly reset date
- `file_ledger` and `quota_ledger` (single shared-pool counter) in Postgres; token bucket; serving reserve
- Failure taxonomy implemented, dead-letter queue
- Tiers 1–3 executed service by service in priority order
- `coverage_manifest` exposed in the UI from day one of this phase

**Exit:** priority services fully summarised; quota consumption tracking within 10% of projection; a killed workflow demonstrably resumes without duplicate spend.

### Phase 6 — Full serving pipeline (Weeks 14–19, overlapping)

- Entity linking, Cypher generation, graph-planned retrieval
- Persona routing (four templates), SSE streaming
- Answer synthesis with mandatory citations; deterministic citation resolution; Haiku groundedness gate; refusal path
- Semantic answer cache
- Eval set extended to 200+ questions per persona, architect-validated, running nightly and gating CI

**Exit:** end-to-end answers with citations for all indexed services; measured hallucination rate at or near zero on the golden set; refusal fires correctly for out-of-coverage questions.

### Phase 7 — Flow narratives (Weeks 18–21)

- Recurring cross-service flows identified from the graph
- Tier-4 generation (Sonnet)
- **Architect review workflow** — a narrative is not authoritative until approved
- `needs_review` invalidation wired to the freshness pipeline

**Exit:** 20–50 reviewed flow narratives live; multi-hop questions demonstrably answered from reviewed narratives rather than live reconstruction.

### Phase 8 — Freshness, hardening, rollout (Weeks 20–24)

- Post-merge webhooks, incremental reindex, cascade deletes
- Reconciliation sweep, nightly backstop
- OpenSearch alias-based zero-downtime reindex; Neo4j snapshot schedule; documented and **rehearsed** graph rebuild
- SigNoz dashboards: token spend by persona and task, retrieval latency percentiles, refusal rate, cache hit rate, coverage percentage
- Runbooks; pilot with one ART; feedback loop into the eval set

**Exit:** a merge to main is reflected in answers within the agreed SLA; no orphaned data after a full delete cycle; pilot users report the answers trustworthy.

---

## 7. Cross-cutting: evaluation

Not a phase — infrastructure standing from Phase 2 onward.

- **Golden set:** 200+ questions per persona, architect-validated answers, versioned in git
- **Metrics:** recall@k and MRR (retrieval); citation validity rate, groundedness pass rate, refusal precision (answers); p50/p95 latency; tokens per answered question
- **Gates:** every prompt change, model change, chunking change, and schema change runs the suite; regression blocks merge
- **Sources:** seeded from real questions asked in team channels; grown from pilot feedback

Without this, there is no way to know whether a change helped, and over a multi-year horizon the prompts, models, and chunking will all change.

---

## 8. Risk register

| Risk | Impact | Mitigation |
|---|---|---|
| Prompt v1 proves wrong after bulk indexing | Full re-spend of first-pass quota | Freeze prompt only after Phase 4 calibration; version prompts; treat changes as budgeted events |
| Indexing starves interactive users | Product perceived as dead — and with a monthly reset, for up to three weeks | Serving reserve enforced in the routing layer, not by convention; weekly tranches prevent early-month exhaustion |
| Quota is tied to an individual user account | Indexing halts if that person's allocation changes or they move on | Request a dedicated application identity before Phase 5; treat as a blocker, not a preference |
| Neo4j Community single-instance outage | Serving degraded | Graph is derived data; rehearsed rebuild from git + Postgres; snapshots; PodDisruptionBudget |
| Lexical recall insufficient for engineers | Engineers revert to their IDE | Custom analyzer from Phase 1; measure in evals; add a trigram index in Phase 8 only if evals demand it |
| Cross-service questions do not cluster into enumerable flows | Precomputed narratives underused; latency and cost rise | Instrument question distribution from Phase 6; revisit at month three |
| Coverage confusion during staged indexing | Users distrust the system | Coverage manifest visible in the UI from Phase 5; refusal names the unindexed service and its scheduled week |
| Temporal operational burden | Delivery slows | Confirm platform ownership in Phase 0; documented fallback is Celery + strict Postgres job ledger |
| Estimator assumptions off by more than 2x | Quota plan invalid | Phase 4 calibration is a hard gate before bulk spend |

---

## 9. Open questions to resolve before Phase 1

1. ~~**Gateway quota mechanics**~~ — **Resolved.** Per-user, monthly reset, shared pool across selectable models. See §2.1. *Remaining sub-question:* can a dedicated application identity be provisioned so production indexing does not run against an individual's allocation? This is a Phase 5 blocker.
2. **Batch and API surface** — access is currently provisioned as Claude Code with the gateway endpoint and API key in `.claude` (§2.1). Confirm what else that same proxy exposes: the raw Messages API for programmatic calls, the Batches API (does not reduce tokens but typically halves their cost, and indexing is inherently asynchronous), and the token-counting endpoint used by the Phase 4 calibration.
3. ~~**Prompt caching**~~ — **Resolved: not supported by the gateway.** Every call is billed in full, including the instruction template. See §5.2.1 for the consequence.
4. **Temporal ownership** — does any team at Emirates already operate it? If not, the Celery fallback becomes the default.
5. **OpenSearch vs existing platform** — does the platform team already run a managed OpenSearch or Elasticsearch? Inheriting a supported platform beats introducing one.
6. **Neo4j GDS community tier** — confirm which algorithms are available under the open plugin. Fallback is offline computation with NetworkX or igraph from a graph export.
7. **Batch scanning sanction** — socialise the indexing volume with the gateway owners before the first bulk run; a separate batch allocation would decouple indexing from serving entirely.
8. **`--service-depth`** — confirm the repository layout so service attribution is correct from the first inventory run.

---

## 10. Definition of done for v1

- Every question answered carries resolvable citations, or an explicit refusal
- A merge to main is reflected in answers within the agreed SLA, with no orphaned data
- Coverage is visible and accurate; unindexed areas are named, not guessed at
- Token spend is observable in real time and within the agreed weekly allocation
- The eval suite runs nightly and gates changes
- Neo4j and OpenSearch can both be rebuilt from git and Postgres, rehearsed and timed
- No component in the stack requires a commercial licence
