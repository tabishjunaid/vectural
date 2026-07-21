"""Flow narrative model + review status (§Phase 7, §4.4)."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class ReviewStatus(StrEnum):
    PENDING = "pending"  # newly generated, never reviewed
    NEEDS_REVIEW = "needs_review"  # code changed since last approval — not regenerated
    APPROVED = "approved"  # authoritative
    CHANGES_REQUESTED = "changes_requested"  # architect asked for changes
    REJECTED = "rejected"  # not a valid flow


class FlowNarrative(BaseModel):
    id: str
    title: str
    services: list[str]
    trigger: str
    signature: str  # structural signature of the flow (change detection, §4.4)
    text: str  # the current narrative markdown
    summary: str = ""
    prompt_version: str
    content_hash: str  # signature + prompt version → generation cache key
    status: ReviewStatus = ReviewStatus.PENDING
    review_reason: str | None = None
    # The last architect-approved text, retained for the re-review diff.
    last_approved_text: str | None = None
    last_approved_by: str | None = None
    last_approved_at: datetime | None = None
    updated_at: datetime

    @property
    def is_authoritative(self) -> bool:
        """Only an approved narrative may be served as a cross-service fact."""
        return self.status is ReviewStatus.APPROVED

    @property
    def in_review_queue(self) -> bool:
        return self.status in (ReviewStatus.PENDING, ReviewStatus.NEEDS_REVIEW)


class ReviewDecision(BaseModel):
    architect: str = Field(min_length=1)
    reason: str | None = None
