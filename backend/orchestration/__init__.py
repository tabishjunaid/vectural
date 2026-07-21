"""Durable orchestration of the indexing spend loop (implementation-plan §5.7).

Temporal owns durable execution, checkpointing, and resumability — "a
non-resumable workflow duplicates spend on restart" (design-doc §2.1). Temporal
needs a running server, so this package models those semantics deterministically
and testably:

- a **parent workflow** over services in priority order, each summarised by a
  **child activity** (service atomicity — a service is fully indexed or absent)
- **checkpointing** after every completed service, so a crash resumes from the
  last checkpoint and — because completed files are skipped via the file_ledger —
  **re-spends nothing** (the Phase 5 exit criterion)
- a **quota park** (durable timer, not an error) when the governor holds
- **continue-as-new** at weekly tranche boundaries for history hygiene

The real ``temporalio`` workflow/activity definitions are a thin adapter over
this logic (see ``temporal.py``), kept behind an optional import.
"""

from backend.orchestration.activities import ServiceActivities, SummariseServiceActivities
from backend.orchestration.state import (
    InMemoryWorkflowStore,
    WorkflowState,
    WorkflowStore,
)
from backend.orchestration.workflow import IndexingDeps, Outcome, drive, run_indexing

__all__ = [
    "InMemoryWorkflowStore",
    "IndexingDeps",
    "Outcome",
    "ServiceActivities",
    "SummariseServiceActivities",
    "WorkflowState",
    "WorkflowStore",
    "drive",
    "run_indexing",
]
