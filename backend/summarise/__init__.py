"""Summarisation tiers (implementation-plan §5.2).

Phase 5 implements the tier-1 (file) driver end to end: it requests budget from
the governor, routes the summary call through the single egress, records spend,
and keys results by content hash + prompt version so a resumed run never
re-spends on unchanged files (the §Phase 5 exit criterion).

Prompt *content* is frozen only after Phase 4 calibration (against open-source
Java, never company code — §2). The template here is a minimal placeholder; what
is real and tested is the orchestration around it: skip/spend/hold/dead-letter.
"""

from backend.summarise.driver import (
    FileToSummarise,
    SummariseOutcome,
    SummariseReport,
    summarise_files,
)
from backend.summarise.tiers import (
    TIER1_PROMPT_VERSION,
    FileSummary,
    content_hash,
    estimate_tier1_cost,
    render_tier1_prompt,
)

__all__ = [
    "TIER1_PROMPT_VERSION",
    "FileSummary",
    "FileToSummarise",
    "SummariseOutcome",
    "SummariseReport",
    "content_hash",
    "estimate_tier1_cost",
    "render_tier1_prompt",
    "summarise_files",
]
