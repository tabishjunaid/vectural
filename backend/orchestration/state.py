"""Workflow state — the durable, resumable checkpoint (§5.7).

In Temporal this state lives in workflow history; here it is an explicit,
serializable object persisted after each completed service. A restart loads the
last checkpoint and continues — never re-processing a service already marked
complete, and (via the file_ledger) never re-summarising a file already done.
"""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, Field


class WorkflowState(BaseModel):
    """Resumable state of one indexing run."""

    pending_services: list[str]  # priority order; index 0 is next
    completed_services: list[str] = Field(default_factory=list)
    parked: bool = False
    park_reason: str | None = None
    run_count: int = 0  # continue-as-new generations (history hygiene)

    @classmethod
    def initial(cls, services: list[str]) -> WorkflowState:
        return cls(pending_services=list(services))

    @property
    def done(self) -> bool:
        return not self.pending_services

    @property
    def next_service(self) -> str | None:
        return self.pending_services[0] if self.pending_services else None

    def complete_current(self) -> None:
        service = self.pending_services.pop(0)
        self.completed_services.append(service)
        self.parked = False
        self.park_reason = None


class WorkflowStore(Protocol):
    """Persists workflow checkpoints. The in-memory impl models Temporal history;
    production would checkpoint to Postgres alongside the ledgers."""

    def save(self, workflow_id: str, state: WorkflowState) -> None: ...
    def load(self, workflow_id: str) -> WorkflowState | None: ...


class InMemoryWorkflowStore:
    def __init__(self) -> None:
        self._rows: dict[str, WorkflowState] = {}

    def save(self, workflow_id: str, state: WorkflowState) -> None:
        # Deep-copy so a later in-place mutation can't retroactively alter the
        # persisted checkpoint (models durable, immutable history).
        self._rows[workflow_id] = state.model_copy(deep=True)

    def load(self, workflow_id: str) -> WorkflowState | None:
        stored = self._rows.get(workflow_id)
        return stored.model_copy(deep=True) if stored is not None else None
