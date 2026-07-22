"""Finalize step of a durable indexing run (§5.7, §Phase 7).

Runs once, after every service is indexed to tier 3. Two cross-service jobs that no
single per-service activity can own:

  * **flows (tier 4)** — identify cross-service call/event flows and generate their
    narratives (using the tier-3 service summaries as context), left PENDING for
    architect review (§4.4). Idempotent: unchanged flows skip by content hash.

Graph completion (service-less nodes + cross-service edges) is done by the worker's
finalizer alongside this, once all endpoints exist so their MATCH-based upsert lands.
"""

from __future__ import annotations

from backend.flows import FlowNarrativeService, identify_flows
from backend.flows.review import FlowStore
from backend.flows.service import GenerateReport
from backend.graph.queries import StructuralQueries
from backend.graph.store import GraphStore
from backend.llm.router import LLMRouter
from backend.summarise.store import SummaryStore


def generate_flows(
    graph: GraphStore,
    *,
    router: LLMRouter,
    summaries: SummaryStore,
    flow_store: FlowStore,
) -> GenerateReport:
    """Identify + generate cross-service flow narratives into ``flow_store``."""
    candidates = identify_flows(graph, StructuralQueries(graph))
    service_summaries = {r.key: r.text for r in summaries.all(3)}
    flows = FlowNarrativeService(store=flow_store, router=router)
    return flows.generate(candidates, service_summaries=service_summaries)
