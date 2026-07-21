"""Coverage view (§5.4) — the single source of truth for "what is indexed".

Consumed by both the Coverage Explorer and the Ask screen's refusal card (design
§5.4), so the two never disagree about a service. A service's *tier reached* is
derived from what actually exists: chunked files (tier 1), a module summary
(tier 2), a service summary (tier 3), and an approved flow narrative it takes
part in (tier 4).
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, Field

from backend.domain.manifest import Manifest
from backend.domain.models import NodeKind
from backend.flows.service import FlowNarrativeService
from backend.graph.store import GraphStore
from backend.summarise.store import SummaryStore

_TIER_LABEL = {0: "not indexed", 1: "1 · file", 2: "2 · module", 3: "3 · service", 4: "4 · flow"}


class CoverageRow(BaseModel):
    service: str
    tier: int
    tier_label: str = Field(serialization_alias="tierLabel")
    last_indexed: str | None = Field(default=None, serialization_alias="lastIndexed")
    next_scheduled: str | None = Field(default=None, serialization_alias="nextScheduled")
    status: str  # "indexed" | "partial" | "not-indexed"


@dataclass
class CoverageService:
    manifest: Manifest
    graph: GraphStore
    summaries: SummaryStore
    flows: FlowNarrativeService

    def rows(self) -> list[CoverageRow]:
        files_by_service = self._files_by_service()
        module_services = {r.key.split("/")[0] for r in self.summaries.all(2)}
        service_summaries = {r.key for r in self.summaries.all(3)}
        approved_flow_services = {
            svc for f in self.flows.store.all() if f.is_authoritative for svc in f.services
        }

        out: list[CoverageRow] = []
        for svc in self.manifest.services:
            name = svc.name
            tier = self._tier_for(
                name, files_by_service, module_services, service_summaries, approved_flow_services
            )
            out.append(
                CoverageRow(
                    service=name,
                    tier=tier,
                    tier_label=_TIER_LABEL[tier],
                    last_indexed="recently" if tier >= 1 else None,
                    next_scheduled=None if tier >= 4 else f"tier-{tier + 1}",
                    status=_status(tier),
                )
            )
        return sorted(out, key=lambda r: r.service)

    def _files_by_service(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for node in self.graph.nodes(NodeKind.FILE):
            service = str(node.properties.get("service", ""))
            counts[service] = counts.get(service, 0) + 1
        return counts

    @staticmethod
    def _tier_for(
        name: str,
        files_by_service: dict[str, int],
        module_services: set[str],
        service_summaries: set[str],
        approved_flow_services: set[str],
    ) -> int:
        if name in approved_flow_services:
            return 4
        if name in service_summaries:
            return 3
        if name in module_services:
            return 2
        if files_by_service.get(name, 0) > 0:
            return 1
        return 0


def _status(tier: int) -> str:
    if tier >= 4:
        return "indexed"
    if tier >= 1:
        return "partial"
    return "not-indexed"
