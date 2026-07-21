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
from pathlib import Path, PurePosixPath

from fastapi import FastAPI

from backend.answer import AnswerService, RetrievalPlanner, SemanticAnswerCache
from backend.api.app import create_app
from backend.api.coverage import CoverageService
from backend.domain.manifest import Manifest, load_manifest
from backend.domain.models import NodeKind
from backend.embedding import HashingEmbedder
from backend.flows import FlowNarrativeService, InMemoryFlowStore, identify_flows
from backend.graph import StructuralQueries, build_graph
from backend.graph.builder import GraphBuildResult
from backend.llm import FakeGatewayClient, LLMRouter
from backend.observability import MetricsCollector
from backend.persistence import InMemoryDeadLetter, InMemoryFileLedger
from backend.quota import QuotaAccountant, QuotaConfig, QuotaGovernor, QuotaPool
from backend.retrieval import InMemorySearchBackend, RetrievalService, TokenOverlapReranker
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

COMMIT = "BOOTSTRAP"


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
) -> AppServices:
    manifest = load_manifest(manifest_path.read_text(encoding="utf-8"))
    graph = build_graph(estate_root, manifest, commit_sha=COMMIT)
    store = graph.store()

    embedder = HashingEmbedder()
    search = InMemorySearchBackend(embedder=embedder)
    search.index(graph.chunks)
    retrieval = RetrievalService(backend=search, embedder=embedder, reranker=TokenOverlapReranker())

    metrics = MetricsCollector()
    pool = QuotaPool(QuotaConfig(monthly_budget=100_000_000), period_start=datetime.now(UTC).date())
    accountant = QuotaAccountant(pool)
    router = LLMRouter(FakeGatewayClient(), sinks=[accountant, metrics])
    governor = QuotaGovernor(pool)

    file_ledger = InMemoryFileLedger()
    summaries = InMemorySummaryStore()
    flows = FlowNarrativeService(store=InMemoryFlowStore(), router=router)

    if summarise_on_boot:
        _run_summarisation(graph, router, governor, file_ledger, summaries)
    flows.generate(identify_flows(store, StructuralQueries(store)))

    services = {n.key for n in store.nodes(NodeKind.SERVICE)}
    planner = RetrievalPlanner(StructuralQueries(store), router, services)
    answer = AnswerService(
        retrieval=retrieval, planner=planner, router=router,
        cache=SemanticAnswerCache(embedder), flows=flows, metrics=metrics, commit_sha=COMMIT,
    )
    coverage = CoverageService(manifest=manifest, graph=store, summaries=summaries, flows=flows)

    app = create_app(
        retrieval,
        answer_service=answer,
        flow_service=flows,
        coverage_service=coverage,
        metrics=metrics,
        cors_origins=cors_origins,
    )
    return AppServices(app=app, answer=answer, flows=flows, coverage=coverage, metrics=metrics)


def _run_summarisation(
    graph: GraphBuildResult,
    router: LLMRouter,
    governor: QuotaGovernor,
    file_ledger: InMemoryFileLedger,
    summaries: InMemorySummaryStore,
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
        dead_letter=InMemoryDeadLetter(), today=now,
    )

    # Tier 2 — group file summaries by folder (module).
    modules: dict[str, ModuleInput] = {}
    for key, summary in report.summaries.items():
        service, path = key.split(":", 1)
        module_key = _module_key(service, path)
        entry = file_ledger.get(service, path)
        child = ModuleChildSummary(path, entry.content_hash if entry else path, summary)
        modules.setdefault(
            module_key, ModuleInput(module_key=module_key, service=service, file_summaries=[])
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


def _module_key(service: str, path: str) -> str:
    parent = PurePosixPath(path).parent
    return str(parent) if str(parent) != "." else service


def build_app_from_env() -> FastAPI:
    from backend.config import load_settings

    settings = load_settings()
    return build_services(
        settings.estate_root,
        settings.manifest_path,
        summarise_on_boot=settings.summarise_on_boot,
        cors_origins=settings.cors_origins,
    ).app


def build_manifest_and_graph(
    estate_root: Path, manifest_path: Path
) -> tuple[Manifest, GraphBuildResult]:
    """Small helper for scripts/tests that want the raw graph without the app."""
    manifest = load_manifest(manifest_path.read_text(encoding="utf-8"))
    return manifest, build_graph(estate_root, manifest, commit_sha=COMMIT)
