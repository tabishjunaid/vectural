"""Tier-2 (module) and tier-3 (service) summarisation (§5.2)."""

from __future__ import annotations

from datetime import UTC, date, datetime

from backend.llm import FakeGatewayClient, LLMRouter
from backend.quota import QuotaAccountant, QuotaConfig, QuotaGovernor, QuotaPool
from backend.summarise import (
    FileSummary,
    InMemorySummaryStore,
    ModuleChildSummary,
    ModuleInput,
    ModuleSummary,
    ServiceChildSummary,
    ServiceInput,
    summarise_modules,
    summarise_services,
)

TODAY = datetime(2026, 7, 1, tzinfo=UTC)


def _rig():
    pool = QuotaPool(QuotaConfig(1_000_000), period_start=date(2026, 7, 1))
    router = LLMRouter(FakeGatewayClient(), sinks=[QuotaAccountant(pool)])
    return pool, router, QuotaGovernor(pool), InMemorySummaryStore()


def _module_inputs(pay_hash: str = "h1") -> list[ModuleInput]:
    return [
        ModuleInput(
            module_key="payments",
            service="payments",
            file_summaries=[
                ModuleChildSummary("payments/pay.py", pay_hash, FileSummary(purpose="charge")),
                ModuleChildSummary("payments/refund.py", "h2", FileSummary(purpose="refund")),
            ],
        )
    ]


def test_tier2_generates_module_summary() -> None:
    _pool, router, gov, store = _rig()
    report = summarise_modules(
        _module_inputs(), router=router, governor=gov, store=store, today=TODAY
    )
    assert report.counts()["generated"] == 1
    assert report.tokens_spent > 0
    record = store.get(2, "payments")
    assert record is not None and record.text


def test_tier2_skips_when_children_unchanged() -> None:
    _pool, router, gov, store = _rig()
    summarise_modules(_module_inputs(), router=router, governor=gov, store=store, today=TODAY)

    gateway = FakeGatewayClient()
    router2 = LLMRouter(gateway)
    report = summarise_modules(
        _module_inputs(), router=router2, governor=gov, store=store, today=TODAY
    )
    assert report.counts()["skipped"] == 1
    assert gateway.calls == 0  # no re-spend on unchanged children


def test_tier2_regenerates_when_child_changes() -> None:
    _pool, router, gov, store = _rig()
    summarise_modules(_module_inputs("h1"), router=router, governor=gov, store=store, today=TODAY)
    # A child file summary changes its hash -> the module regenerates (§5.9 cascade).
    report = summarise_modules(
        _module_inputs("h1-CHANGED"), router=router, governor=gov, store=store, today=TODAY
    )
    assert report.counts()["generated"] == 1


def test_tier3_generates_service_summary_from_modules() -> None:
    _pool, router, gov, store = _rig()
    summarise_modules(_module_inputs(), router=router, governor=gov, store=store, today=TODAY)
    module = store.get(2, "payments")
    assert module is not None

    service_input = ServiceInput(
        service="payments",
        module_summaries=[
            ServiceChildSummary(
                "payments", module.content_hash, ModuleSummary(responsibility=module.text)
            )
        ],
        openapi_text="POST /charge",
        readme_text="Payments service",
    )
    report = summarise_services(
        [service_input], router=router, governor=gov, store=store, today=TODAY
    )
    assert report.counts()["generated"] == 1
    record = store.get(3, "payments")
    assert record is not None and record.text


def test_tier2_dead_letters_malformed_output() -> None:
    pool = QuotaPool(QuotaConfig(1_000_000), period_start=date(2026, 7, 1))
    router = LLMRouter(FakeGatewayClient(malformed=True), sinks=[QuotaAccountant(pool)])
    store = InMemorySummaryStore()
    report = summarise_modules(
        _module_inputs(), router=router, governor=QuotaGovernor(pool), store=store, today=TODAY
    )
    assert report.counts()["dead_lettered"] == 1
    assert store.get(2, "payments") is None
