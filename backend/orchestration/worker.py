"""Temporal indexing worker (``vectural-indexer-worker``).

Hosts the :class:`IndexingWorkflow` and its activities against the real stores.
Builds the estate graph + per-service work map **once** at startup, connects
OpenSearch / Neo4j / Postgres, loads the durable shared quota pool, and runs a
Temporal worker until interrupted. Run one or more of these; the starter
(``vectural-index``) kicks off / resumes the workflow.

Gateway + embedder are selected from config (``build_gateway`` / ``build_embedder``):
set ``VECTURAL_EMBEDDER=bge-m3`` for real embeddings and ``VECTURAL_GATEWAY=real`` +
``VECTURAL_GATEWAY_CLIENT`` to route through your own gateway client (§2 — this
codebase never ships the real gateway egress).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from backend.config import Settings, load_settings
from backend.domain.manifest import Manifest, load_manifest
from backend.estimator import EstimatorConfig, estimate_estate
from backend.graph import build_graph
from backend.graph.builder import GraphBuildResult
from backend.graph.neo4j_store import Neo4jGraphStore
from backend.llm import LLMRouter
from backend.llm.factory import build_gateway
from backend.observability import MetricsCollector
from backend.orchestration.activities import IndexServiceActivities
from backend.orchestration.finalize import generate_flows
from backend.orchestration.temporal import build_worker
from backend.orchestration.temporal_activities import IndexingActivities
from backend.orchestration.work import EstateWork, build_work_by_service
from backend.persistence.coverage import CoverageWriter
from backend.persistence.flow_store import PgFlowStore
from backend.persistence.postgres import (
    PgCoverageManifest,
    PgDeadLetter,
    PgFileLedger,
    apply_schema,
    open_connection,
)
from backend.persistence.quota_ledger import PgQuotaLedger
from backend.persistence.summary_store import PgSummaryStore
from backend.quota import QuotaAccountant, QuotaConfig, QuotaGovernor, QuotaPool
from backend.retrieval.opensearch_backend import OpenSearchBackend

_COMMIT = "INDEXING"
_log = logging.getLogger(__name__)


def _build_indexer(
    work: EstateWork, neo: Neo4jGraphStore, cfg: Settings
) -> tuple[IndexServiceActivities, QuotaPool, PgQuotaLedger, LLMRouter, PgSummaryStore]:
    from backend.embedding.factory import build_embedder

    embedder = build_embedder(cfg)
    search = OpenSearchBackend.connect(cfg.opensearch_url, cfg.opensearch_index, embedder)
    conn = open_connection(cfg.postgres_dsn)
    apply_schema(conn)

    quota_ledger = PgQuotaLedger(conn, tranche_count=cfg.tranche_count)
    pool = quota_ledger.load_or_create(
        QuotaConfig(
            monthly_budget=cfg.monthly_budget,
            serving_reserve_fraction=cfg.serving_reserve_fraction,
            tranche_count=cfg.tranche_count,
        ),
        datetime.now(UTC).date(),
    )
    router = LLMRouter(build_gateway(cfg), sinks=[QuotaAccountant(pool), MetricsCollector()])
    coverage: CoverageWriter = PgCoverageManifest(conn)
    summaries = PgSummaryStore(conn)
    indexer = IndexServiceActivities(
        work=work, search=search, graph=neo, router=router,
        governor=QuotaGovernor(pool), file_ledger=PgFileLedger(conn),
        dead_letter=PgDeadLetter(conn), summaries=summaries, coverage=coverage,
    )
    return indexer, pool, quota_ledger, router, summaries


def _cost_by_service(cfg: Settings, manifest: Manifest) -> dict[str, int]:
    """Full per-service gateway cost (tiers 1-3) from the estimator, so the workflow
    reserves the whole service before starting it (service atomicity, §5.7)."""
    estimate = estimate_estate(
        cfg.estate_root, manifest,
        EstimatorConfig(
            monthly_budget=cfg.monthly_budget,
            serving_reserve_fraction=cfg.serving_reserve_fraction,
            tranche_count=cfg.tranche_count,
        ),
    )
    return {s.service: s.summary_gateway_tokens for s in estimate.services}


def _make_finalizer(  # type: ignore[no-untyped-def]
    work: EstateWork, graph: GraphBuildResult, neo: Neo4jGraphStore,
    router: LLMRouter, summaries: PgSummaryStore, flow_store: PgFlowStore,
):
    """After every service is indexed: complete the graph (service-less nodes +
    cross-service edges, whose endpoints now all exist) and generate the tier-4
    cross-service flow narratives (left PENDING for architect review, §4.4)."""

    def finalize(_today: datetime) -> None:
        neo.upsert_nodes(work.shared_nodes)
        neo.upsert_edges(work.cross_service_edges)

        # A complete in-memory graph gives fast, exact flow identification.
        report = generate_flows(
            graph.store(), router=router, summaries=summaries, flow_store=flow_store
        )
        _log.info("finalize: %d shared nodes, %d cross-service edges, "
                  "flows created=%d skipped=%d",
                  len(work.shared_nodes), len(work.cross_service_edges),
                  report.created, report.skipped)

    return finalize


async def _run(cfg: Settings) -> None:
    from temporalio.client import Client

    manifest = load_manifest(cfg.manifest_path.read_text(encoding="utf-8"))
    graph = build_graph(cfg.estate_root, manifest, commit_sha=_COMMIT)
    work = build_work_by_service(graph)
    _log.info("estate graph: %d services, %d chunks, %d nodes, %d edges",
              len(work.by_service), len(graph.chunks), len(graph.nodes), len(graph.edges))

    neo = Neo4jGraphStore.connect(cfg.neo4j_uri, cfg.neo4j_user, cfg.neo4j_password)
    neo.apply_schema()
    conn_indexer, pool, quota_ledger, router, summaries = _build_indexer(work, neo, cfg)
    flow_store = PgFlowStore(open_connection(cfg.postgres_dsn))

    activities = IndexingActivities(
        indexer=conn_indexer, pool=pool,
        cost_by_service=_cost_by_service(cfg, manifest), quota_ledger=quota_ledger,
        finalizer=_make_finalizer(work, graph, neo, router, summaries, flow_store),
    )
    client = await Client.connect(cfg.temporal_target, namespace=cfg.temporal_namespace)
    worker = build_worker(client, cfg.temporal_task_queue, activities=activities)
    _log.info("worker ready on task queue %r (target %s) — waiting for work",
              cfg.temporal_task_queue, cfg.temporal_target)
    await worker.run()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(_run(load_settings()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
