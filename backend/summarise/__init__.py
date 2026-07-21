"""Summarisation tiers (implementation-plan §5.2).

All four tiers are content-hash + prompt-version keyed, so a resumed run never
re-spends on unchanged inputs (the §Phase 5 exit criterion), and a higher tier
regenerates only when a child changes (the §5.9 upward cascade). Tier 1 (file)
and tiers 2-3 (module, service) live here; tier 4 (flow narratives, human-gated)
lives in :mod:`backend.flows`.

Prompt *content* is frozen only after Phase 4 calibration (against open-source
Java, never company code — §2). The templates here are minimal placeholders;
what is real and tested is the orchestration: skip/spend/hold/dead-letter.
"""

from backend.summarise.driver import (
    FileToSummarise,
    SummariseOutcome,
    SummariseReport,
    summarise_files,
)
from backend.summarise.higher import (
    HigherTierReport,
    ModuleInput,
    ServiceInput,
    summarise_modules,
    summarise_services,
)
from backend.summarise.store import (
    InMemorySummaryStore,
    SummaryRecord,
    SummaryStore,
)
from backend.summarise.tiers import (
    MODULE_PROMPT_VERSION,
    SERVICE_PROMPT_VERSION,
    TIER1_PROMPT_VERSION,
    FileSummary,
    ModuleChildSummary,
    ModuleSummary,
    ServiceChildSummary,
    ServiceSummary,
    content_hash,
    estimate_tier1_cost,
    render_tier1_prompt,
)

__all__ = [
    "MODULE_PROMPT_VERSION",
    "SERVICE_PROMPT_VERSION",
    "TIER1_PROMPT_VERSION",
    "FileSummary",
    "FileToSummarise",
    "HigherTierReport",
    "InMemorySummaryStore",
    "ModuleChildSummary",
    "ModuleInput",
    "ModuleSummary",
    "ServiceChildSummary",
    "ServiceInput",
    "ServiceSummary",
    "SummariseOutcome",
    "SummariseReport",
    "SummaryRecord",
    "SummaryStore",
    "content_hash",
    "estimate_tier1_cost",
    "render_tier1_prompt",
    "summarise_files",
    "summarise_modules",
    "summarise_services",
]
