"""Temporal activities — the side-effecting steps the indexing workflow drives.

All I/O lives here (embed+index, graph upsert, gateway summarisation, quota
persistence); the workflow (:mod:`backend.orchestration.temporal_workflow`) stays
deterministic and only orchestrates these. Activities are **sync** — the worker
runs them in a thread-pool executor — and take/return JSON-simple values so
Temporal can serialise them into history.

Idempotency is inherited from :class:`IndexServiceActivities` (search skips
already-indexed files, graph MERGE is idempotent, the file_ledger skips summaries
already done), so Temporal's at-least-once activity retries never double-spend.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from temporalio import activity

from backend.orchestration.activities import IndexServiceActivities
from backend.persistence.quota_ledger import PgQuotaLedger
from backend.quota.ledger import QuotaPool
from backend.summarise.tiers import estimate_tier1_cost


@dataclass
class IndexingActivities:
    """Bound activity implementations; register the instance's methods on the worker."""

    indexer: IndexServiceActivities
    pool: QuotaPool
    # Full per-service gateway cost (tiers 1-3) from the estimator, so reserving it
    # before the service starts means the higher tiers' own governor checks pass
    # (service atomicity, §5.7). Falls back to a tier-1 proxy when absent.
    cost_by_service: dict[str, int] | None = None
    quota_ledger: PgQuotaLedger | None = None
    # Runs flows (tier 4) + cross-service edges once the estate is fully indexed.
    finalizer: Callable[[datetime], None] | None = None

    def _service_cost(self, service: str) -> int:
        if self.cost_by_service is not None and service in self.cost_by_service:
            return max(1, self.cost_by_service[service])
        unit = self.indexer.work.by_service.get(service)
        files = unit.files if unit is not None else []
        return sum(estimate_tier1_cost(f.content) for f in files) or 1

    @activity.defn(name="request_budget")
    def request_budget(self, service: str, today_iso: str) -> dict[str, object]:
        today = datetime.fromisoformat(today_iso).date()
        decision = self.indexer.governor.request_indexing_budget(
            self._service_cost(service), today
        )
        return {
            "proceed": decision.proceed,
            "reason": decision.reason or "",
            "available": decision.available,
        }

    @activity.defn(name="index_service")
    def index_service(self, service: str, today_iso: str) -> dict[str, object]:
        today = datetime.fromisoformat(today_iso)
        unit = self.indexer.work.by_service.get(service)
        files = unit.files if unit is not None else []
        report = self.indexer.summarise_service(service, files, today)
        self._persist_pool()
        return {"service": service, "summarised": len(report.summaries)}

    @activity.defn(name="finalize")
    def finalize(self, today_iso: str) -> dict[str, object]:
        if self.finalizer is not None:
            self.finalizer(datetime.fromisoformat(today_iso))
        self._persist_pool()
        return {"finalized": True}

    def _persist_pool(self) -> None:
        if self.quota_ledger is not None:
            self.quota_ledger.upsert(self.pool)
