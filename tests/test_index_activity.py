"""The full per-service indexing activity: embed+index + graph + tier-1 (§5.7)."""

from __future__ import annotations

from datetime import UTC, datetime

from backend.embedding import HashingEmbedder
from backend.graph.builder import GraphBuildResult
from backend.graph.store import InMemoryGraphStore
from backend.llm import FakeGatewayClient, LLMRouter
from backend.orchestration.activities import IndexServiceActivities
from backend.orchestration.work import build_work_by_service
from backend.persistence import InMemoryDeadLetter, InMemoryFileLedger
from backend.persistence.coverage import InMemoryCoverageWriter
from backend.persistence.file_ledger import FileStatus
from backend.quota import QuotaAccountant, QuotaConfig, QuotaGovernor, QuotaPool
from backend.retrieval import InMemorySearchBackend
from backend.summarise import InMemorySummaryStore

NOW = datetime(2026, 7, 1, tzinfo=UTC)


def _activity(work, pool: QuotaPool, summaries: InMemorySummaryStore | None = None) -> tuple[
    IndexServiceActivities, InMemorySearchBackend, InMemoryGraphStore, InMemoryCoverageWriter
]:
    embedder = HashingEmbedder()
    search = InMemorySearchBackend(embedder=embedder)
    graph = InMemoryGraphStore()
    coverage = InMemoryCoverageWriter()
    router = LLMRouter(FakeGatewayClient(), sinks=[QuotaAccountant(pool)])
    act = IndexServiceActivities(
        work=work, search=search, graph=graph, router=router,
        governor=QuotaGovernor(pool), file_ledger=InMemoryFileLedger(),
        dead_letter=InMemoryDeadLetter(), summaries=summaries, coverage=coverage,
    )
    return act, search, graph, coverage


def _pool() -> QuotaPool:
    return QuotaPool(QuotaConfig(monthly_budget=1_000_000_000), period_start=NOW.date())


def test_index_service_indexes_chunks_graph_and_summaries(graph_build: GraphBuildResult) -> None:
    work = build_work_by_service(graph_build)
    service = next(s for s in work.services if work.by_service[s].files)
    unit = work.by_service[service]
    pool = _pool()
    act, search, graph, coverage = _activity(work, pool)

    report = act.summarise_service(service, unit.files, NOW)

    # Chunks are in the search index (that service's files).
    indexed = search.indexed_files()
    assert {(c.service, c.path) for c in unit.chunks} <= indexed
    # Graph got the service's own nodes.
    assert graph.node_count == len(unit.nodes)
    # Tier-1 summaries were written to the ledger.
    assert any(e.status is FileStatus.SUMMARISED for e in act.file_ledger.all())
    assert report.summaries
    # Coverage progress recorded at tier 1.
    assert coverage.rows[service] == (1, "indexed", NOW)


def test_index_service_is_idempotent_no_duplicate_spend(graph_build: GraphBuildResult) -> None:
    work = build_work_by_service(graph_build)
    service = next(s for s in work.services if work.by_service[s].files)
    unit = work.by_service[service]
    pool = _pool()
    act, search, graph, _ = _activity(work, pool)

    act.summarise_service(service, unit.files, NOW)
    spent_after_first = pool.spent_indexing
    files_after_first = search.indexed_files()
    nodes_after_first = graph.node_count

    # Re-run (crash/retry): no re-index, no re-summarise, no extra spend.
    act.summarise_service(service, unit.files, NOW)
    assert pool.spent_indexing == spent_after_first
    assert search.indexed_files() == files_after_first
    assert graph.node_count == nodes_after_first


def test_index_service_runs_tiers_2_and_3(graph_build: GraphBuildResult) -> None:
    work = build_work_by_service(graph_build)
    service = next(s for s in work.services if work.by_service[s].files)
    unit = work.by_service[service]
    pool = _pool()
    summaries = InMemorySummaryStore()
    act, _search, _graph, coverage = _activity(work, pool, summaries)

    act.summarise_service(service, unit.files, NOW)

    # Tier 1 file summaries persisted, tier 2 module summaries + tier 3 service summary.
    assert [r for r in summaries.all(1) if r.key.startswith(f"{service}:")]
    assert summaries.all(2)  # at least one module summary
    assert summaries.get(3, service) is not None  # the service summary
    assert coverage.rows[service] == (3, "indexed", NOW)  # reached tier 3


def test_tiers_2_3_idempotent_on_rerun(graph_build: GraphBuildResult) -> None:
    work = build_work_by_service(graph_build)
    service = next(s for s in work.services if work.by_service[s].files)
    unit = work.by_service[service]
    pool = _pool()
    summaries = InMemorySummaryStore()
    act, _s, _g, _c = _activity(work, pool, summaries)

    act.summarise_service(service, unit.files, NOW)
    spent = pool.spent_indexing
    svc_summary = summaries.get(3, service)

    # Re-run: content-hash keyed skips mean no extra spend and a stable summary.
    act.summarise_service(service, unit.files, NOW)
    assert pool.spent_indexing == spent
    assert summaries.get(3, service) == svc_summary


def test_finalize_generates_cross_service_flows(graph_build: GraphBuildResult) -> None:
    from backend.flows import InMemoryFlowStore
    from backend.flows.models import ReviewStatus
    from backend.orchestration.finalize import generate_flows

    router = LLMRouter(FakeGatewayClient(), sinks=[])
    flow_store = InMemoryFlowStore()
    report = generate_flows(
        graph_build.store(), router=router,
        summaries=InMemorySummaryStore(), flow_store=flow_store,
    )
    # The fixture estate has cross-service call flows → narratives generated, PENDING.
    assert report.created > 0
    flows = flow_store.all()
    assert flows
    assert all(f.status is ReviewStatus.PENDING for f in flows)
    assert all(len(f.services) >= 2 for f in flows)  # cross-service by construction
