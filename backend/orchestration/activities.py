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

from backend.llm.router import LLMRouter
from backend.persistence.dead_letter import DeadLetterRepo
from backend.persistence.file_ledger import FileLedgerRepo
from backend.quota.governor import QuotaGovernor
from backend.summarise.driver import FileToSummarise, SummariseReport, summarise_files
from backend.summarise.tiers import TIER1_PROMPT_VERSION


class ServiceActivities(Protocol):
    def summarise_service(
        self, service: str, files: list[FileToSummarise], today: datetime
    ) -> SummariseReport: ...


@dataclass
class SummariseServiceActivities:
    """The production child activity: summarise a service's files (tier 1)."""

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
