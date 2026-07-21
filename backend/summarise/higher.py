"""Tier-2 (module) and tier-3 (service) summarisation drivers (§5.2).

Both aggregate the tier below: a module summary from its files' tier-1 summaries
(Haiku), a service summary from its modules' tier-2 summaries plus OpenAPI/README
(Sonnet). They share one core that is content-hash keyed on the *children's*
hashes, so a higher-tier summary regenerates only when a child changes — the
automatic upward cascade (§5.9) with no wasted spend — and is governor-gated and
router-accounted like every indexing task.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from pydantic import BaseModel, ValidationError

from backend.domain.models import TaskType
from backend.failures import ContentFailure
from backend.llm.router import LLMRouter
from backend.quota.governor import QuotaGovernor
from backend.summarise.store import SummaryRecord, SummaryStore
from backend.summarise.tiers import (
    MODULE_PROMPT_VERSION,
    SERVICE_PROMPT_VERSION,
    ModuleChildSummary,
    ModuleSummary,
    ServiceChildSummary,
    ServiceSummary,
    estimate_module_cost,
    estimate_service_cost,
    higher_content_hash,
    render_module_prompt,
    render_service_prompt,
)


@dataclass
class ModuleInput:
    module_key: str
    service: str
    file_summaries: list[ModuleChildSummary]


@dataclass
class ServiceInput:
    service: str
    module_summaries: list[ServiceChildSummary]
    openapi_text: str | None = None
    readme_text: str | None = None


@dataclass
class HigherTierReport:
    generated: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    dead_lettered: list[str] = field(default_factory=list)
    held: list[str] = field(default_factory=list)
    tokens_spent: int = 0

    def counts(self) -> dict[str, int]:
        return {
            "generated": len(self.generated),
            "skipped": len(self.skipped),
            "dead_lettered": len(self.dead_lettered),
            "held": len(self.held),
        }


@dataclass
class _Unit:
    key: str
    kind: str
    tier: int
    child_hashes: list[str]
    prompt: str
    cost: int


def summarise_modules(
    inputs: list[ModuleInput],
    *,
    router: LLMRouter,
    governor: QuotaGovernor,
    store: SummaryStore,
    today: datetime | None = None,
    prompt_version: str = MODULE_PROMPT_VERSION,
) -> HigherTierReport:
    units = [
        _Unit(
            key=i.module_key, kind="module", tier=2,
            child_hashes=[c.content_hash for c in i.file_summaries],
            prompt=render_module_prompt(i.module_key, i.file_summaries),
            cost=estimate_module_cost(i.file_summaries),
        )
        for i in inputs
    ]
    return _run(
        units, task_type=TaskType.MODULE_SUMMARY, prompt_version=prompt_version,
        parse=ModuleSummary.model_validate, text_of=lambda m: m.responsibility,
        router=router, governor=governor, store=store, now=today or datetime.now(UTC),
    )


def summarise_services(
    inputs: list[ServiceInput],
    *,
    router: LLMRouter,
    governor: QuotaGovernor,
    store: SummaryStore,
    today: datetime | None = None,
    prompt_version: str = SERVICE_PROMPT_VERSION,
) -> HigherTierReport:
    units = [
        _Unit(
            key=i.service, kind="service", tier=3,
            child_hashes=[c.content_hash for c in i.module_summaries],
            prompt=render_service_prompt(
                i.service, i.module_summaries,
                openapi_text=i.openapi_text, readme_text=i.readme_text,
            ),
            cost=estimate_service_cost(i.module_summaries),
        )
        for i in inputs
    ]
    return _run(
        units, task_type=TaskType.SERVICE_SUMMARY, prompt_version=prompt_version,
        parse=ServiceSummary.model_validate, text_of=lambda s: s.description,
        router=router, governor=governor, store=store, now=today or datetime.now(UTC),
    )


def _run[M: BaseModel](
    units: list[_Unit],
    *,
    task_type: TaskType,
    prompt_version: str,
    parse: Callable[[dict[str, object]], M],
    text_of: Callable[[M], str],
    router: LLMRouter,
    governor: QuotaGovernor,
    store: SummaryStore,
    now: datetime,
) -> HigherTierReport:
    report = HigherTierReport()
    held = False
    for unit in units:
        if held:
            report.held.append(unit.key)
            continue

        new_hash = higher_content_hash(unit.child_hashes, prompt_version)
        existing = store.get(unit.tier, unit.key)
        if existing is not None and existing.content_hash == new_hash:
            report.skipped.append(unit.key)  # children unchanged — no re-spend
            continue

        decision = governor.request_indexing_budget(unit.cost, now.date())
        if not decision.proceed:
            held = True
            report.held.append(unit.key)
            continue

        try:
            response = router.route(task_type, prompt_version, {"prompt": unit.prompt})
        except ContentFailure:
            report.dead_lettered.append(unit.key)
            continue

        report.tokens_spent += response.usage.total_tokens
        try:
            model = parse(response.parsed or {})
        except ValidationError:
            report.dead_lettered.append(unit.key)
            continue

        store.upsert(
            SummaryRecord(
                tier=unit.tier, kind=unit.kind, key=unit.key, text=text_of(model),
                data=model.model_dump(), content_hash=new_hash,
                prompt_version=prompt_version, updated_at=now,
            )
        )
        report.generated.append(unit.key)
    return report
