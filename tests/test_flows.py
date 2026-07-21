"""Flow identification, generation, review lifecycle, invalidation (§Phase 7)."""

from __future__ import annotations

import pytest

from backend.flows import FlowNarrativeService, identify_flows
from backend.flows.models import ReviewStatus
from backend.flows.service import FlowNotFoundError
from backend.graph import StructuralQueries
from backend.graph.builder import GraphBuildResult


def test_identifies_call_chain_and_event_flows(
    graph_build: GraphBuildResult, structural: StructuralQueries
) -> None:
    candidates = identify_flows(graph_build.store(), structural)
    titles = {c.title for c in candidates}
    assert "gateway → payments → ledger" in titles  # call chain
    assert "payments → notifications" in titles  # topic flow
    assert all(c.is_cross_service for c in candidates)


def test_generation_is_content_hash_keyed(
    flow_service: FlowNarrativeService, flow_candidates
) -> None:
    # The fixture already generated once; a second run over the same candidates
    # re-spends nothing (unchanged structure + prompt).
    report = flow_service.generate(flow_candidates)
    assert report.created == 0
    assert report.skipped == len(flow_candidates)


def test_generated_narratives_start_pending(flow_service: FlowNarrativeService) -> None:
    queue = flow_service.queue()
    assert queue
    assert all(n.status is ReviewStatus.PENDING for n in queue)


def test_approve_makes_authoritative(flow_service: FlowNarrativeService, flow_candidates) -> None:
    fid = flow_candidates[0].id
    approved = flow_service.approve(fid, "A. Chen")
    assert approved.status is ReviewStatus.APPROVED
    assert approved.is_authoritative
    assert approved.last_approved_by == "A. Chen"
    assert approved.last_approved_text == approved.text


def test_only_approved_flows_are_served(
    flow_service: FlowNarrativeService, flow_candidates
) -> None:
    cand = flow_candidates[0]
    assert flow_service.evidence_for(set(cand.services)) == []  # pending -> not served
    flow_service.approve(cand.id, "A. Chen")
    hits = flow_service.evidence_for(set(cand.services))
    assert [h.chunk_id for h in hits] == [f"flow:{cand.id}"]


def test_needs_review_invalidation_is_fail_closed(
    flow_service: FlowNarrativeService, flow_candidates
) -> None:
    cand = flow_candidates[0]
    flow_service.approve(cand.id, "A. Chen")
    affected = flow_service.invalidate_on_change({cand.services[0]})
    assert cand.id in affected

    narrative = flow_service.get(cand.id)
    assert narrative is not None
    assert narrative.status is ReviewStatus.NEEDS_REVIEW
    assert cand.services[0] in (narrative.review_reason or "")
    # Last approved text retained for the re-review diff; text not regenerated.
    assert narrative.last_approved_text is not None
    # Dropped from authoritative serving until re-approved (never silently served).
    assert flow_service.evidence_for(set(cand.services)) == []


def test_invalidation_ignores_unrelated_services(
    flow_service: FlowNarrativeService, flow_candidates
) -> None:
    cand = flow_candidates[0]
    flow_service.approve(cand.id, "A. Chen")
    assert flow_service.invalidate_on_change({"unrelated-service"}) == []
    assert flow_service.get(cand.id).status is ReviewStatus.APPROVED


def test_request_changes_and_reject(flow_service: FlowNarrativeService, flow_candidates) -> None:
    fid = flow_candidates[0].id
    changed = flow_service.request_changes(fid, "A. Chen", "needs the retry path")
    assert changed.status is ReviewStatus.CHANGES_REQUESTED
    assert changed.review_reason == "needs the retry path"
    rejected = flow_service.reject(fid, "A. Chen")
    assert rejected.status is ReviewStatus.REJECTED


def test_missing_flow_raises(flow_service: FlowNarrativeService) -> None:
    with pytest.raises(FlowNotFoundError):
        flow_service.approve("does-not-exist", "A. Chen")
