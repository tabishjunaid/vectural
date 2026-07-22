"""Tier-1 summarisation driver (§5.2, §5.7, §5.8).

Ties the Phase 5 pieces into one spend loop. For each file, in order:

1. **skip** if the ``file_ledger`` shows the same content hash + prompt version
   already summarised (resume without duplicate spend — the exit criterion)
2. **hold** if the quota governor won't approve the budget — not an error;
   indexing pauses and remaining files are parked (§5.8 quota-exhausted)
3. **route** the summary through the single egress; on malformed output,
   **dead-letter** and continue (§5.8 content failure), never blocking the batch
4. otherwise **record** the summary and update the ledger

The real deployment drives this from a Temporal workflow (durable, resumable);
this function is that workflow's pure core, testable with no infrastructure.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import ValidationError

from backend.domain.models import Persona, TaskType
from backend.failures import ContentFailure, QuotaExhausted
from backend.llm.router import LLMRouter
from backend.persistence.dead_letter import DeadLetterEntry, DeadLetterRepo
from backend.persistence.file_ledger import FileLedgerEntry, FileLedgerRepo, FileStatus
from backend.quota.governor import QuotaGovernor
from backend.summarise.store import SummaryRecord, SummaryStore
from backend.summarise.tiers import (
    TIER1_PROMPT_VERSION,
    FileSummary,
    content_hash,
    estimate_tier1_cost,
    render_tier1_prompt,
)


class SummariseOutcome(StrEnum):
    SUMMARISED = "summarised"
    SKIPPED = "skipped"  # unchanged content + prompt (§Phase 5 resume)
    DEAD_LETTERED = "dead_lettered"  # content failure (§5.8)
    HELD = "held"  # quota hold — parked for the next tranche/period (§5.8)


@dataclass(frozen=True)
class FileToSummarise:
    service: str
    path: str
    content: str
    content_hash: str | None = None  # computed from content when absent

    def hash(self) -> str:
        return self.content_hash or content_hash(self.content)


@dataclass
class FileResultRow:
    service: str
    path: str
    outcome: SummariseOutcome
    tokens: int = 0


@dataclass
class SummariseReport:
    rows: list[FileResultRow] = field(default_factory=list)
    summaries: dict[str, FileSummary] = field(default_factory=dict)
    tokens_spent: int = 0

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for row in self.rows:
            out[row.outcome.value] = out.get(row.outcome.value, 0) + 1
        return out


def summarise_files(
    files: list[FileToSummarise],
    *,
    router: LLMRouter,
    governor: QuotaGovernor,
    file_ledger: FileLedgerRepo,
    dead_letter: DeadLetterRepo,
    today: datetime | None = None,
    persona: Persona | None = None,
    prompt_version: str = TIER1_PROMPT_VERSION,
    summary_store: SummaryStore | None = None,
) -> SummariseReport:
    now = today or datetime.now(UTC)
    report = SummariseReport()
    held = False

    for file in files:
        if held:  # once indexing pauses, park the rest (§5.8)
            report.rows.append(FileResultRow(file.service, file.path, SummariseOutcome.HELD))
            continue

        h = file.hash()
        existing = file_ledger.get(file.service, file.path)
        if existing is not None and existing.matches(h, prompt_version):
            report.rows.append(FileResultRow(file.service, file.path, SummariseOutcome.SKIPPED))
            continue

        decision = governor.request_indexing_budget(estimate_tier1_cost(file.content), now.date())
        if not decision.proceed:
            held = True
            report.rows.append(FileResultRow(file.service, file.path, SummariseOutcome.HELD))
            continue

        outcome, tokens = _summarise_one(
            file,
            h,
            router=router,
            file_ledger=file_ledger,
            dead_letter=dead_letter,
            prompt_version=prompt_version,
            persona=persona,
            now=now,
            report=report,
            summary_store=summary_store,
        )
        report.rows.append(FileResultRow(file.service, file.path, outcome, tokens))
        report.tokens_spent += tokens

    return report


def _summarise_one(
    file: FileToSummarise,
    h: str,
    *,
    router: LLMRouter,
    file_ledger: FileLedgerRepo,
    dead_letter: DeadLetterRepo,
    prompt_version: str,
    persona: Persona | None,
    now: datetime,
    report: SummariseReport,
    summary_store: SummaryStore | None,
) -> tuple[SummariseOutcome, int]:
    prompt = render_tier1_prompt(path=file.path, content=file.content)
    try:
        response = router.route(TaskType.FILE_SUMMARY, prompt_version, {"prompt": prompt}, persona)
    except ContentFailure as exc:
        _dead_letter(file, exc.kind, exc.detail, dead_letter, file_ledger, h, prompt_version, now)
        return SummariseOutcome.DEAD_LETTERED, 0
    except QuotaExhausted:
        # Governor already approved, so this is unexpected — treat as a hold.
        return SummariseOutcome.HELD, 0

    tokens = response.usage.total_tokens
    try:
        summary = FileSummary.model_validate(response.parsed or {})
    except ValidationError as exc:
        _dead_letter(
            file, "schema_mismatch", str(exc), dead_letter, file_ledger, h, prompt_version, now
        )
        return SummariseOutcome.DEAD_LETTERED, tokens

    report.summaries[f"{file.service}:{file.path}"] = summary
    # Persist the tier-1 summary text durably (before the ledger marks the file
    # done) so a resume that skips this file still leaves tiers 2-3 their input.
    if summary_store is not None:
        summary_store.upsert(
            SummaryRecord(
                tier=1, kind="file", key=f"{file.service}:{file.path}",
                text=summary.purpose, data=summary.model_dump(),
                content_hash=h, prompt_version=prompt_version, updated_at=now,
            )
        )
    file_ledger.upsert(
        FileLedgerEntry(
            service=file.service,
            path=file.path,
            content_hash=h,
            prompt_version=prompt_version,
            tier=1,
            status=FileStatus.SUMMARISED,
            updated_at=now,
        )
    )
    return SummariseOutcome.SUMMARISED, tokens


def _dead_letter(
    file: FileToSummarise,
    kind: str,
    detail: str | None,
    dead_letter: DeadLetterRepo,
    file_ledger: FileLedgerRepo,
    h: str,
    prompt_version: str,
    now: datetime,
) -> None:
    dead_letter.add(
        DeadLetterEntry(service=file.service, path=file.path, kind=kind, detail=detail, at=now)
    )
    file_ledger.upsert(
        FileLedgerEntry(
            service=file.service,
            path=file.path,
            content_hash=h,
            prompt_version=prompt_version,
            tier=0,
            status=FileStatus.DEAD_LETTERED,
            updated_at=now,
        )
    )
