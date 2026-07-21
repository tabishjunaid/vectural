"""End-to-end offline demo: ingest → graph → search → summarise → answer.

Uses the in-memory backends, the deterministic embedder, and the fake gateway, so
it runs with no OpenSearch, Neo4j, Postgres, or model-serving pod — proving the
Phase 1-6 chain holds together end to end with zero real spend. Not a production
entrypoint; this is the "does the whole chain hold together" check.

    python -m backend.demo <estate-root> -m manifest.yaml -q "how do refunds reverse"
    python -m backend.demo <estate-root> -m manifest.yaml --depends-on ledger
    python -m backend.demo <estate-root> -m manifest.yaml --summarise
    python -m backend.demo <estate-root> -m manifest.yaml --ask "how does X work" --persona po
"""

from __future__ import annotations

import argparse
from datetime import UTC, date, datetime
from pathlib import Path

from backend.answer import AnswerService, RetrievalPlanner, SemanticAnswerCache
from backend.domain.manifest import Manifest, load_manifest
from backend.domain.models import NodeKind, Persona
from backend.embedding import HashingEmbedder
from backend.flows import FlowNarrativeService, InMemoryFlowStore, identify_flows
from backend.freshness import FreshnessState, Reindexer, parse_name_status, reindex
from backend.graph import StructuralQueries, build_graph
from backend.graph.builder import GraphBuildResult
from backend.llm import FakeGatewayClient, LLMRouter
from backend.persistence import InMemoryDeadLetter, InMemoryFileLedger
from backend.quota import QuotaAccountant, QuotaConfig, QuotaGovernor, QuotaPool
from backend.retrieval import InMemorySearchBackend, RetrievalService, TokenOverlapReranker
from backend.summarise import FileToSummarise, summarise_files


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="vectural-demo", description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("-m", "--manifest", type=Path, default=None)
    parser.add_argument("-q", "--query", default="how does a refund reversal propagate")
    parser.add_argument("-n", "--top-n", type=int, default=5)
    parser.add_argument(
        "--depends-on",
        metavar="SERVICE",
        default=None,
        help="print the services that depend on SERVICE (structural, no LLM)",
    )
    parser.add_argument("--hops", type=int, default=3)
    parser.add_argument(
        "--summarise",
        action="store_true",
        help="run the Phase 5 tier-1 spend loop over the estate (fake gateway, no real spend)",
    )
    parser.add_argument(
        "--ask",
        metavar="QUESTION",
        default=None,
        help="run the Phase 6 answer path (graph-planned retrieval + R1 gates, fake gateway)",
    )
    parser.add_argument(
        "--persona",
        choices=[p.value for p in Persona],
        default=Persona.ENGINEER.value,
    )
    parser.add_argument(
        "--flows",
        action="store_true",
        help="run the Phase 7 flow-narrative lifecycle: identify → generate → approve → invalidate",
    )
    parser.add_argument(
        "--reindex",
        metavar="DIFF",
        default=None,
        help="apply a git name-status DIFF (e.g. 'M<TAB>svc/f.py'); shows the §5.9 cascade",
    )
    parser.add_argument(
        "--orchestrate",
        action="store_true",
        help="run the §5.7 durable indexing workflow, kill it mid-run, and resume (no re-spend)",
    )
    args = parser.parse_args(argv)

    manifest_path = args.manifest or (args.root / "manifest.yaml")
    manifest = load_manifest(manifest_path.read_text(encoding="utf-8"))

    graph = build_graph(args.root, manifest, commit_sha="DEMO")
    print(
        f"built graph: {len(graph.chunks)} chunks, "
        f"{len(graph.nodes)} nodes, {len(graph.edges)} edges"
    )
    print(f"  nodes: {graph.counts_by_node_kind()}")
    print(f"  edges: {graph.counts_by_edge_kind()}\n")

    # Structural fast path (§5.5) — no gateway call.
    if args.depends_on:
        queries = StructuralQueries(graph.store())
        dependents = queries.service_dependents(args.depends_on, max_hops=args.hops)
        print(f'what depends on "{args.depends_on}" (<= {args.hops} hops):')
        if not dependents:
            print("  (nothing, or service not in graph)")
        for hop, services in dependents.items():
            print(f"  {hop} hop(s): {', '.join(services)}")
        print()

    # Hybrid retrieval (§5.3 steps 5-6).
    embedder = HashingEmbedder()
    backend = InMemorySearchBackend(embedder=embedder)
    backend.index(graph.chunks)
    service = RetrievalService(backend=backend, embedder=embedder, reranker=TokenOverlapReranker())

    print(f'query: "{args.query}"')
    for i, hit in enumerate(service.search(args.query, top_n=args.top_n), start=1):
        sym = hit.symbol or "—"
        print(f"  {i}. score={hit.score:.4f}  {hit.path}:{hit.span}  [{sym}]")

    if args.ask:
        _demo_ask(graph, service, args.ask, Persona(args.persona))

    if args.summarise:
        _demo_summarise(graph)

    if args.flows:
        _demo_flows(graph)

    if args.reindex is not None:
        _demo_reindex(graph, manifest, args.root, args.reindex)

    if args.orchestrate:
        _demo_orchestrate(graph)
    return 0


def _demo_orchestrate(graph: GraphBuildResult) -> None:
    """§5.7: run the durable indexing workflow, kill it mid-run, resume from the
    checkpoint — and show the total spend is identical (no duplicate spend)."""
    from collections.abc import Callable
    from datetime import date

    from backend.orchestration import (
        IndexingDeps,
        InMemoryWorkflowStore,
        SummariseServiceActivities,
        WorkflowState,
        drive,
    )
    from backend.quota import QuotaAccountant, QuotaConfig, QuotaGovernor, QuotaPool
    from backend.summarise import FileToSummarise

    by_service: dict[str, list[FileToSummarise]] = {}
    for chunk in graph.chunks:
        by_service.setdefault(chunk.service, []).append(
            FileToSummarise(chunk.service, chunk.path, chunk.content)
        )
    order = sorted(by_service)
    today = datetime.now(UTC)

    def make(
        pool: QuotaPool,
        gateway: FakeGatewayClient,
        ledger: InMemoryFileLedger,
        ckpt: Callable[[WorkflowState], None],
    ) -> IndexingDeps:
        router = LLMRouter(gateway, sinks=[QuotaAccountant(pool)])
        acts = SummariseServiceActivities(router, QuotaGovernor(pool), ledger, InMemoryDeadLetter())
        return IndexingDeps(activities=acts, governor=QuotaGovernor(pool), checkpoint=ckpt)

    print("\n§5.7 durable indexing workflow:")
    pool = QuotaPool(QuotaConfig(1_000_000), period_start=date(2026, 7, 1))
    ledger = InMemoryFileLedger()
    store = InMemoryWorkflowStore()
    state = WorkflowState.initial(order)
    store.save("wf", state)

    crash_gw = FakeGatewayClient(crash_after=1)
    try:
        drive(state, by_service, make(pool, crash_gw, ledger, lambda s: store.save("wf", s)),
              today=today)
    except RuntimeError:
        resumed = store.load("wf")
        assert resumed is not None
        print(
            f"  worker killed after {crash_gw.calls} calls; "
            f"checkpoint = {resumed.completed_services}"
        )

    resumed = store.load("wf")
    assert resumed is not None
    ok_gw = FakeGatewayClient()
    drive(resumed, by_service, make(pool, ok_gw, ledger, lambda s: store.save("wf", s)),
          today=today)
    summarised = sum(1 for e in ledger.all() if e.status.value == "summarised")
    print(f"  resumed → completed all {len(resumed.completed_services)} services")
    print(f"  summaries in ledger: {summarised}")
    print(f"  total spend (crash+resume): {pool.spent_indexing} tokens — no duplicate spend")


def _demo_reindex(
    graph: GraphBuildResult, manifest: Manifest, root: Path, diff_text: str
) -> None:
    """Phase 8/§5.9: apply a git diff → incremental reindex with cascade deletes,
    flow needs_review invalidation, and a staleness flag (fake gateway)."""
    embedder = HashingEmbedder()
    search = InMemorySearchBackend(embedder=embedder)
    search.index(graph.chunks)
    store = graph.store()
    flows = FlowNarrativeService(store=InMemoryFlowStore(), router=LLMRouter(FakeGatewayClient()))
    flows.generate(identify_flows(store, StructuralQueries(store)))
    for narrative in flows.queue():
        flows.approve(narrative.id, "A. Chen")

    reindexer = Reindexer(
        root=root, manifest=manifest, search_backend=search, graph_store=store,
        file_ledger=InMemoryFileLedger(), commit_sha="DEMO2",
    )
    freshness = FreshnessState()
    changes = parse_name_status(diff_text.replace("<TAB>", "\t"))

    print(f"\nPhase 8 incremental reindex: {[(c.status.value, c.path) for c in changes]}")
    result = reindex(changes, reindexer, flows=flows, freshness=freshness)
    print(f"  reindexed={result.reindexed} deleted={result.deleted}")
    print(f"  to re-summarise={result.resummarise}")
    print(f"  affected services={sorted(result.affected_services)}")
    print(f"  flows flipped to needs_review={result.flows_flagged}")
    print(f"  services now serving-stale={sorted(freshness.stale_services)}")


def _demo_flows(graph: GraphBuildResult) -> None:
    """Phase 7 lifecycle: identify cross-service flows → generate (tier 4) →
    architect approves → a code change flips it to needs_review, never silently
    regenerated (fake gateway, no real spend)."""
    structural = StructuralQueries(graph.store())
    candidates = identify_flows(graph.store(), structural)
    flows = FlowNarrativeService(store=InMemoryFlowStore(), router=LLMRouter(FakeGatewayClient()))
    report = flows.generate(candidates)

    print("\nPhase 7 flow narratives:")
    print(f"  identified {len(candidates)} flows; generated {report.created} (pending review)")
    for narrative in flows.queue():
        print(f"    - {narrative.id}  [{narrative.status.value}]  {narrative.title}")

    if candidates:
        first = candidates[0]
        flows.approve(first.id, "A. Chen")
        print(f'  architect approved "{first.title}" -> authoritative')
        affected = flows.invalidate_on_change({first.services[0]})
        approved = flows.get(first.id)
        assert approved is not None
        print(
            f"  code change in {first.services[0]} -> flipped {len(affected)} to "
            f"{approved.status.value} (not regenerated); dropped from serving"
        )


def _demo_ask(
    graph: GraphBuildResult, retrieval: RetrievalService, question: str, persona: Persona
) -> None:
    """Phase 6 answer path: graph-planned scope → synthesis → citation gate →
    groundedness gate → release/refuse (fake gateway, no real spend)."""
    services = {n.key for n in graph.nodes if n.kind is NodeKind.SERVICE}
    router = LLMRouter(FakeGatewayClient())
    planner = RetrievalPlanner(StructuralQueries(graph.store()), router, services)
    embedder = HashingEmbedder()
    answer_service = AnswerService(
        retrieval=retrieval, planner=planner, router=router,
        cache=SemanticAnswerCache(embedder), commit_sha="DEMO",
    )
    answer = answer_service.answer(question, persona)
    print(f'\nask ({persona.value}): "{question}"')
    print(f"  mode: {answer.mode.value}")
    print(f"  {answer.text}")
    for c in answer.citations:
        print(f"  [{c.index}] {c.path}:{c.span}  ({c.chunk_id})")
    if answer.mode.value == "refusal":
        print(f"  likely owning services: {', '.join(answer.likely_services) or '—'}")


def _demo_summarise(graph: GraphBuildResult) -> None:
    """Phase 5 spend loop over the estate's chunks, twice — the second run resumes
    from the file ledger and re-spends nothing (fake gateway, no real tokens)."""
    # One entry per file (service, path) — the ledger is keyed per file, so
    # aggregate a file's chunks into a single content blob.
    by_file: dict[tuple[str, str], list[str]] = {}
    for chunk in graph.chunks:
        by_file.setdefault((chunk.service, chunk.path), []).append(chunk.content)
    files = [
        FileToSummarise(service=service, path=path, content="\n".join(parts))
        for (service, path), parts in sorted(by_file.items())
    ]
    pool = QuotaPool(QuotaConfig(monthly_budget=1_000_000), period_start=date(2026, 7, 1))
    governor = QuotaGovernor(pool)
    router = LLMRouter(FakeGatewayClient(), sinks=[QuotaAccountant(pool)])
    ledger, dead_letter = InMemoryFileLedger(), InMemoryDeadLetter()
    now = datetime.now(UTC)

    print("\nPhase 5 summarisation (fake gateway):")
    first = summarise_files(
        files, router=router, governor=governor, file_ledger=ledger,
        dead_letter=dead_letter, today=now,
    )
    print(f"  run 1: {first.counts()}  tokens_spent={first.tokens_spent}")
    spent = pool.spent_indexing
    second = summarise_files(
        files, router=router, governor=governor, file_ledger=ledger,
        dead_letter=dead_letter, today=now,
    )
    print(f"  run 2 (resume): {second.counts()}  additional_spend={pool.spent_indexing - spent}")


if __name__ == "__main__":
    raise SystemExit(main())
