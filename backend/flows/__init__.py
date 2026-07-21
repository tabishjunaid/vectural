"""Flow narratives — tier 4 + architect review (implementation-plan §Phase 7).

A flow narrative is a cross-service **business** claim. Two properties make it
different from every other tier:

- it is generated from the graph's cross-service structure, not a single file
- it is **not authoritative until an architect approves it**, and when the
  underlying code changes it is flagged ``needs_review`` and **never silently
  regenerated** (§4.4) — the same fail-closed posture as the answer path,
  applied to staleness rather than groundedness.

Everything here runs offline against the fake gateway; only approved narratives
are served as evidence, so an un-reviewed or stale flow can never present a
cross-service claim as fact.
"""

from backend.flows.identify import FlowCandidate, identify_flows
from backend.flows.models import FlowNarrative, ReviewStatus
from backend.flows.review import FlowStore, InMemoryFlowStore
from backend.flows.service import FlowNarrativeService

__all__ = [
    "FlowCandidate",
    "FlowNarrative",
    "FlowNarrativeService",
    "FlowStore",
    "InMemoryFlowStore",
    "ReviewStatus",
    "identify_flows",
]
