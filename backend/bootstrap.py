"""Wire a fully-runnable app from an estate (in-memory backing).

This is the "in-memory bootstrap" serving path: ingest an estate, index it,
optionally run all four summarisation tiers (fake gateway — no real spend), and
construct every service the API needs. It lets the frontend talk to a live
backend with **no external datastore or gateway**. The real OpenSearch / Neo4j /
Postgres / gateway adapters slot in behind the same service objects once
provisioned (config-gated).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI

from backend.answer import AnswerService, RetrievalPlanner, SemanticAnswerCache
from backend.api.app import create_app
from backend.api.coverage import CoverageService
from backend.config import Settings
from backend.domain.manifest import Manifest, load_manifest
from backend.domain.models import NodeKind
from backend.embedding.base import Embedder
from backend.embedding.factory import build_embedder
from backend.flows import FlowNarrativeService, InMemoryFlowStore, identify_flows
from backend.flows.review import FlowStore
from backend.graph import StructuralQueries, build_graph
from backend.graph.builder import GraphBuildResult
from backend.graph.store import GraphStore
from backend.llm import LLMRouter
from backend.llm.factory import build_gateway
from backend.observability import MetricsCollector
from backend.persistence import InMemoryDeadLetter, InMemoryFileLedger
from backend.persistence.dead_letter import DeadLetterRepo
from backend.persistence.file_ledger import FileLedgerRepo
from backend.quota import QuotaAccountant, QuotaConfig, QuotaGovernor, QuotaPool
from backend.retrieval import InMemorySearchBackend, RetrievalService, TokenOverlapReranker
from backend.retrieval.base import SearchBackend
from backend.summarise import (
    FileToSummarise,
    InMemorySummaryStore,
    ModuleChildSummary,
    ModuleInput,
    ModuleSummary,
    ServiceChildSummary,
    ServiceInput,
    summarise_files,
    summarise_modules,
    summarise_services,
)
from backend.summarise.store import SummaryStore
from backend.summarise.tiers import module_key

COMMIT = "BOOTSTRAP"


@dataclass
class Backing:
    """The store implementations the app runs over — in-memory or real."""

    search: SearchBackend
    graph: GraphStore
    file_ledger: FileLedgerRepo
    dead_letter: DeadLetterRepo
    quota_pool: QuotaPool
    summaries: SummaryStore
    flow_store: FlowStore


@dataclass
class AppServices:
    app: FastAPI
    answer: AnswerService
    flows: FlowNarrativeService
    coverage: CoverageService
    metrics: MetricsCollector


def build_services(
    estate_root: Path,
    manifest_path: Path,
    *,
    summarise_on_boot: bool = True,
    cors_origins: list[str] | None = None,
    backing: str = "inmemory",
    settings: object | None = None,
) -> AppServices:
    manifest = load_manifest(manifest_path.read_text(encoding="utf-8"))
    embedder = build_embedder(settings if isinstance(settings, Settings) else None)

    # Real indexing is a separate durable job (the Temporal indexing worker), so a
    # real-backing boot connects to already-populated stores and serves — it does
    # NOT parse/index/summarise the estate. In-memory backing (the demo path) still
    # indexes on boot, as does an explicit index_on_boot override.
    do_boot_index = backing != "real" or _index_on_boot(settings)
    graph = build_graph(estate_root, manifest, commit_sha=COMMIT) if do_boot_index else None

    store = _build_backing(backing, graph, embedder, settings)
    retrieval = RetrievalService(
        backend=store.search, embedder=embedder, reranker=TokenOverlapReranker()
    )

    metrics = MetricsCollector()
    pool = store.quota_pool  # durable shared pool (persisted for real backing)
    accountant = QuotaAccountant(pool)
    router = LLMRouter(
        build_gateway(settings if isinstance(settings, Settings) else None),
        sinks=[accountant, metrics],
    )
    governor = QuotaGovernor(pool)

    # For real backing these are the durable stores the indexing worker wrote to
    # (Postgres); the API serves tiers 2-3 + flows from them. In-memory backing gets
    # fresh stores it populates on boot.
    summaries = store.summaries
    flows = FlowNarrativeService(store=store.flow_store, router=router)

    if graph is not None:  # boot-time indexing (in-memory demo / index_on_boot)
        if summarise_on_boot:
            _run_summarisation(
                graph, router, governor, store.file_ledger, store.dead_letter, summaries
            )
        flows.generate(identify_flows(store.graph, StructuralQueries(store.graph)))

    services = {n.key for n in store.graph.nodes(NodeKind.SERVICE)}
    planner = RetrievalPlanner(StructuralQueries(store.graph), router, services)
    answer = AnswerService(
        retrieval=retrieval, planner=planner, router=router,
        cache=SemanticAnswerCache(embedder), flows=flows, metrics=metrics, commit_sha=COMMIT,
    )
    coverage = CoverageService(
        manifest=manifest, graph=store.graph, summaries=summaries, flows=flows
    )

    app = create_app(
        retrieval,
        answer_service=answer,
        flow_service=flows,
        coverage_service=coverage,
        metrics=metrics,
        cors_origins=cors_origins,
    )
    return AppServices(app=app, answer=answer, flows=flows, coverage=coverage, metrics=metrics)


def _build_backing(
    backing: str,
    graph: GraphBuildResult | None,
    embedder: Embedder,
    settings: object | None,
) -> Backing:
    if backing != "real":
        assert graph is not None  # in-memory backing always indexes on boot
        search = InMemorySearchBackend(embedder=embedder)
        search.index(graph.chunks)
        pool = QuotaPool(
            QuotaConfig(monthly_budget=100_000_000), period_start=datetime.now(UTC).date()
        )
        return Backing(
            search=search, graph=graph.store(),
            file_ledger=InMemoryFileLedger(), dead_letter=InMemoryDeadLetter(), quota_pool=pool,
            summaries=InMemorySummaryStore(), flow_store=InMemoryFlowStore(),
        )
    return _real_backing(graph, embedder, settings)


def _real_backing(
    graph: GraphBuildResult | None, embedder: Embedder, settings: object | None
) -> Backing:
    """Connect to OpenSearch + Neo4j + Postgres and load the durable quota pool.

    Connect-only by default: real indexing is the durable worker's job, so boot does
    NOT write to the stores. Only when ``graph`` is provided (index_on_boot) are chunks
    indexed and the graph loaded here — an explicit, opt-in escape hatch."""
    from backend.config import Settings
    from backend.graph.neo4j_store import Neo4jGraphStore
    from backend.persistence.flow_store import PgFlowStore
    from backend.persistence.postgres import (
        PgDeadLetter,
        PgFileLedger,
        apply_schema,
        open_connection,
    )
    from backend.persistence.quota_ledger import PgQuotaLedger
    from backend.persistence.summary_store import PgSummaryStore
    from backend.retrieval.opensearch_backend import OpenSearchBackend

    cfg = settings if isinstance(settings, Settings) else Settings()

    search = OpenSearchBackend.connect(cfg.opensearch_url, cfg.opensearch_index, embedder)
    neo = Neo4jGraphStore.connect(cfg.neo4j_uri, cfg.neo4j_user, cfg.neo4j_password)
    conn = open_connection(cfg.postgres_dsn)
    apply_schema(conn)

    if graph is not None:  # index_on_boot escape hatch — normally the worker does this
        search.index(graph.chunks)
        neo.load(graph.nodes, graph.edges)

    pool = PgQuotaLedger(conn, tranche_count=cfg.tranche_count).load_or_create(
        QuotaConfig(
            monthly_budget=cfg.monthly_budget,
            serving_reserve_fraction=cfg.serving_reserve_fraction,
            tranche_count=cfg.tranche_count,
        ),
        datetime.now(UTC).date(),
    )
    return Backing(
        search=search, graph=neo, file_ledger=PgFileLedger(conn),
        dead_letter=PgDeadLetter(conn), quota_pool=pool,
        summaries=PgSummaryStore(conn), flow_store=PgFlowStore(conn),
    )


def _run_summarisation(
    graph: GraphBuildResult,
    router: LLMRouter,
    governor: QuotaGovernor,
    file_ledger: FileLedgerRepo,
    dead_letter: DeadLetterRepo,
    summaries: SummaryStore,
) -> None:
    """Run tiers 1-3 over the estate (fake gateway) so coverage reflects real
    tiers. Tier 4 (flows) is generated separately and left pending review."""
    now = datetime.now(UTC)

    # Tier 1 — one file per (service, path), content aggregated from its chunks.
    by_file: dict[tuple[str, str], list[str]] = {}
    for chunk in graph.chunks:
        by_file.setdefault((chunk.service, chunk.path), []).append(chunk.content)
    files = [
        FileToSummarise(service=svc, path=path, content="\n".join(parts))
        for (svc, path), parts in sorted(by_file.items())
    ]
    report = summarise_files(
        files, router=router, governor=governor, file_ledger=file_ledger,
        dead_letter=dead_letter, today=now,
    )

    # Tier 2 — group file summaries by folder (module).
    modules: dict[str, ModuleInput] = {}
    for key, summary in report.summaries.items():
        service, path = key.split(":", 1)
        mod_key = module_key(service, path)
        entry = file_ledger.get(service, path)
        child = ModuleChildSummary(path, entry.content_hash if entry else path, summary)
        modules.setdefault(
            mod_key, ModuleInput(module_key=mod_key, service=service, file_summaries=[])
        ).file_summaries.append(child)
    summarise_modules(
        list(modules.values()), router=router, governor=governor, store=summaries, today=now
    )

    # Tier 3 — group module summaries by service.
    services: dict[str, ServiceInput] = {}
    for record in summaries.all(2):
        module_input = modules.get(record.key)
        service = module_input.service if module_input else record.key.split("/")[0]
        svc_child = ServiceChildSummary(
            record.key, record.content_hash, ModuleSummary(responsibility=record.text)
        )
        services.setdefault(
            service, ServiceInput(service=service, module_summaries=[])
        ).module_summaries.append(svc_child)
    summarise_services(
        list(services.values()), router=router, governor=governor, store=summaries, today=now
    )


def _index_on_boot(settings: object | None) -> bool:
    from backend.config import Settings

    return isinstance(settings, Settings) and settings.index_on_boot


def build_app_from_env() -> FastAPI:
    from backend.config import load_settings

    settings = load_settings()
    return build_services(
        settings.estate_root,
        settings.manifest_path,
        summarise_on_boot=settings.summarise_on_boot,
        cors_origins=settings.cors_origins,
        backing=settings.backing,
        settings=settings,
    ).app


def build_manifest_and_graph(
    estate_root: Path, manifest_path: Path
) -> tuple[Manifest, GraphBuildResult]:
    """Small helper for scripts/tests that want the raw graph without the app."""
    manifest = load_manifest(manifest_path.read_text(encoding="utf-8"))
    return manifest, build_graph(estate_root, manifest, commit_sha=COMMIT)
