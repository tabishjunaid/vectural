"""Activities — the side-effecting units the workflow dispatches (§5.7).

In Temporal, activities do the I/O (gateway calls, ledger writes) while the
workflow stays deterministic. The child activity here summarises one service's
files by delegating to the Phase 5 driver, which is **idempotent**: a re-run
after a crash skips files already in the ``file_ledger`` (no duplicate spend).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from backend.domain.models import Edge, Node
from backend.llm.router import LLMRouter
from backend.orchestration.work import EstateWork
from backend.persistence.coverage import CoverageWriter
from backend.persistence.dead_letter import DeadLetterRepo
from backend.persistence.file_ledger import FileLedgerRepo
from backend.quota.governor import QuotaGovernor
from backend.retrieval.base import SearchBackend
from backend.summarise.driver import (
    DEFAULT_MAX_INPUT_TOKENS,
    FileToSummarise,
    SummariseReport,
    summarise_files,
)
from backend.summarise.higher import (
    ModuleInput,
    ServiceInput,
    summarise_modules,
    summarise_services,
)
from backend.summarise.store import SummaryStore
from backend.summarise.tiers import (
    TIER1_PROMPT_VERSION,
    FileSummary,
    ModuleChildSummary,
    ModuleSummary,
    ServiceChildSummary,
    module_key,
)


class ServiceActivities(Protocol):
    def summarise_service(
        self, service: str, files: list[FileToSummarise], today: datetime
    ) -> SummariseReport: ...


class GraphWriter(Protocol):
    """The write half of a graph store — shared by Neo4jGraphStore (real) and
    InMemoryGraphStore (tests). Reads live on the GraphStore protocol."""

    def upsert_nodes(self, nodes: list[Node]) -> None: ...
    def upsert_edges(self, edges: list[Edge]) -> None: ...


@dataclass
class SummariseServiceActivities:
    """A summarise-only child activity (tier 1). Kept for the pure-logic tests."""

    router: LLMRouter
    governor: QuotaGovernor
    file_ledger: FileLedgerRepo
    dead_letter: DeadLetterRepo
    prompt_version: str = TIER1_PROMPT_VERSION

    def summarise_service(
        self, service: str, files: list[FileToSummarise], today: datetime
    ) -> SummariseReport:
        return summarise_files(
            files,
            router=self.router,
            governor=self.governor,
            file_ledger=self.file_ledger,
            dead_letter=self.dead_letter,
            today=today,
            prompt_version=self.prompt_version,
        )


@dataclass
class IndexServiceActivities:
    """The production child activity: **fully** index one service (§5.7).

    One governed, resumable unit per service: embed+index its chunks (OpenSearch),
    upsert its own graph nodes+edges (Neo4j), then summarise it tier 1→2→3 — the
    gateway-billed steps. Tiers are intra-service (module = a folder, service summary
    from its modules), so the whole service summarises as one partitioned unit; the
    workflow reserves the full tier-1..3 cost before starting, so the higher tiers'
    own governor checks pass. Cross-service flows are the finalize step's job.

    Every sub-step is idempotent: the search index skips already-present files, graph
    MERGE is idempotent, and the summary stores skip by content hash — so a crash
    retry never double-spends. Tier-1 summary text is persisted (``summaries``) so a
    resume that skips already-summarised files still leaves tiers 2-3 their input.
    """

    work: EstateWork
    search: SearchBackend
    graph: GraphWriter
    router: LLMRouter
    governor: QuotaGovernor
    file_ledger: FileLedgerRepo
    dead_letter: DeadLetterRepo
    summaries: SummaryStore | None = None
    coverage: CoverageWriter | None = None
    prompt_version: str = TIER1_PROMPT_VERSION
    max_input_tokens: int = DEFAULT_MAX_INPUT_TOKENS

    def summarise_service(
        self, service: str, files: list[FileToSummarise], today: datetime
    ) -> SummariseReport:
        unit = self.work.by_service.get(service)
        if unit is not None:
            # Structural indexing (local, no gateway spend) — idempotent re-runs.
            already = set(self.search.indexed_files())
            fresh = [c for c in unit.chunks if (c.service, c.path) not in already]
            if fresh:
                self.search.index(fresh)
            self.graph.upsert_nodes(unit.nodes)
            self.graph.upsert_edges(unit.edges)

        # Tier 1 — file summaries (persisted so tiers 2-3 survive a resume).
        report = summarise_files(
            files,
            router=self.router,
            governor=self.governor,
            file_ledger=self.file_ledger,
            dead_letter=self.dead_letter,
            today=today,
            prompt_version=self.prompt_version,
            summary_store=self.summaries,
            max_input_tokens=self.max_input_tokens,
        )

        tier = 1
        if self.summaries is not None:
            tier = self._summarise_higher_tiers(service, today)
        if self.coverage is not None:
            self.coverage.mark_indexed(service, tier=tier, status="indexed", at=today)
        return report

    def _summarise_higher_tiers(self, service: str, today: datetime) -> int:
        """Tier 2 (modules) then tier 3 (service) for this service, from the durable
        tier-1 summaries. Returns the highest tier reached (1, 2, or 3)."""
        assert self.summaries is not None
        prefix = f"{service}:"
        tier1 = [r for r in self.summaries.all(1) if r.key.startswith(prefix)]
        if not tier1:
            return 1

        # Tier 2 — group this service's file summaries by module (folder).
        modules: dict[str, ModuleInput] = {}
        for rec in tier1:
            path = rec.key[len(prefix):]
            mk = module_key(service, path)
            child = ModuleChildSummary(
                path=path, content_hash=rec.content_hash,
                summary=FileSummary.model_validate(rec.data),
            )
            modules.setdefault(
                mk, ModuleInput(module_key=mk, service=service, file_summaries=[])
            ).file_summaries.append(child)
        summarise_modules(
            list(modules.values()), router=self.router, governor=self.governor,
            store=self.summaries, today=today,
        )

        # Tier 3 — the service summary from its (now stored) module summaries.
        module_children: list[ServiceChildSummary] = []
        for mk in modules:
            mrec = self.summaries.get(2, mk)
            if mrec is not None:
                module_children.append(ServiceChildSummary(
                    module_key=mk, content_hash=mrec.content_hash,
                    summary=ModuleSummary.model_validate(mrec.data),
                ))
        if module_children:
            summarise_services(
                [ServiceInput(service=service, module_summaries=module_children)],
                router=self.router, governor=self.governor, store=self.summaries, today=today,
            )

        if self.summaries.get(3, service) is not None:
            return 3
        return 2 if module_children else 1
