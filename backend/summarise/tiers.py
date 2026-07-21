"""Tier definitions, output schema, and cost estimation (§5.2, §5.2.1).

The tier-1 output schema is fixed (JSON mode); the router enforces JSON. Cost
estimation exists because indexing is budgeted per service (§5.7) and the
instruction template overhead is fully billed on every call (§5.2.1) — so the
estimate separates content tokens from the fixed ``prompt_overhead_tokens`` lever.
"""

from __future__ import annotations

import hashlib

from pydantic import BaseModel, Field

# Frozen after Phase 4 calibration in reality; bumping it invalidates every tier-1
# summary (a budgeted event, §8 risk 1). Kept explicit so a change is deliberate.
TIER1_PROMPT_VERSION = "file-v1"

# The fixed per-call instruction overhead, in tokens (§5.2.1). Every token added
# to the template costs this x file-count, so it is a budget line, not prose.
PROMPT_OVERHEAD_TOKENS = 120


class FileSummary(BaseModel):
    """Tier-1 (file) summary output (§5.2)."""

    purpose: str = Field(min_length=1)
    key_operations: list[str] = Field(default_factory=list)
    business_concepts: list[str] = Field(default_factory=list)
    external_calls: list[str] = Field(default_factory=list)


def content_hash(content: str) -> str:
    """Stable content hash used as the summary cache key (with prompt version)."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]


def render_tier1_prompt(*, path: str, content: str) -> str:
    """Render the tier-1 prompt. Placeholder template — terse by design so the
    billed overhead stays small (§5.2.1). Real wording is frozen in Phase 4."""
    return (
        "Summarise this source file as JSON with keys "
        "purpose, key_operations, business_concepts, external_calls.\n"
        f"# file: {path}\n{content}"
    )


def estimate_tier1_cost(content: str) -> int:
    """Rough token cost for one tier-1 call: content proxy + fixed overhead.

    Deliberately conservative and cheap to compute — the governor bin-packs on
    measured cost (§5.7), and a real calibration run (Phase 4) replaces this proxy
    with `chars_per_token` measured on a stratified sample."""
    content_tokens = max(1, len(content.split()))
    # Input overhead + an allowance for the structured output.
    return content_tokens + PROMPT_OVERHEAD_TOKENS + 40
