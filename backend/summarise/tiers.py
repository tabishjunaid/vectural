"""Tier definitions, output schema, and cost estimation (§5.2, §5.2.1).

The tier-1 output schema is fixed (JSON mode); the router enforces JSON. Cost
estimation exists because indexing is budgeted per service (§5.7) and the
instruction template overhead is fully billed on every call (§5.2.1) — so the
estimate separates content tokens from the fixed ``prompt_overhead_tokens`` lever.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import PurePosixPath

from pydantic import BaseModel, Field

# Frozen after Phase 4 calibration in reality; bumping it invalidates every tier-1
# summary (a budgeted event, §8 risk 1). Kept explicit so a change is deliberate.
TIER1_PROMPT_VERSION = "file-v1"
MODULE_PROMPT_VERSION = "module-v1"  # tier 2 (Haiku)
SERVICE_PROMPT_VERSION = "service-v1"  # tier 3 (Sonnet)

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


def module_key(service: str, path: str) -> str:
    """The tier-2 module a file belongs to: its parent folder, or the service
    itself for a file at the service root. Paths are service-prefixed, so module
    keys are naturally namespaced across services."""
    parent = PurePosixPath(path).parent
    return str(parent) if str(parent) != "." else service


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


# --------------------------------------------------------------------------- #
# Tier 2 (module) and tier 3 (service) — aggregate lower-tier summaries (§5.2)
# --------------------------------------------------------------------------- #


class ModuleSummary(BaseModel):
    """Tier-2 output: what a module (folder) is responsible for."""

    responsibility: str = Field(min_length=1)
    key_files: list[str] = Field(default_factory=list)


class ServiceSummary(BaseModel):
    """Tier-3 output: a plain-language business description of a service."""

    description: str = Field(min_length=1)
    capabilities: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class ModuleChildSummary:
    """A tier-1 file summary tagged with its identity + content hash (tier-2 input)."""

    path: str
    content_hash: str
    summary: FileSummary


@dataclass(frozen=True)
class ServiceChildSummary:
    """A tier-2 module summary tagged with its identity + content hash (tier-3 input)."""

    module_key: str
    content_hash: str
    summary: ModuleSummary


def higher_content_hash(child_hashes: list[str], prompt_version: str) -> str:
    """Key a higher-tier summary by its children's hashes + the prompt version.

    Order-independent, so a module summary is stable regardless of file ordering
    and regenerates only when a child file summary actually changes (§5.9)."""
    joined = "|".join(sorted(child_hashes))
    return hashlib.sha256(f"{joined}::{prompt_version}".encode()).hexdigest()[:16]


def render_module_prompt(module_key: str, file_summaries: list[ModuleChildSummary]) -> str:
    lines = "\n".join(f"- {c.path}: {c.summary.purpose}" for c in file_summaries)
    return (
        "Summarise this module's responsibility as JSON "
        '{"responsibility": string, "key_files": [string]} from its file summaries.\n'
        f"# module: {module_key}\n{lines}"
    )


def render_service_prompt(
    service: str,
    module_summaries: list[ServiceChildSummary],
    *,
    openapi_text: str | None = None,
    readme_text: str | None = None,
) -> str:
    modules = "\n".join(f"- {c.module_key}: {c.summary.responsibility}" for c in module_summaries)
    extra = ""
    if openapi_text:
        extra += f"\n# OPENAPI\n{openapi_text[:2000]}"
    if readme_text:
        extra += f"\n# README\n{readme_text[:2000]}"
    return (
        "Write a plain-language business description of this service as JSON "
        '{"description": string, "capabilities": [string]} from its module '
        "summaries (and any OpenAPI/README).\n"
        f"# service: {service}\n# MODULES\n{modules}{extra}"
    )


def estimate_module_cost(file_summaries: list[ModuleChildSummary]) -> int:
    body = sum(max(1, len(c.summary.purpose.split())) for c in file_summaries)
    return body + PROMPT_OVERHEAD_TOKENS + 40


def estimate_service_cost(module_summaries: list[ServiceChildSummary]) -> int:
    body = sum(max(1, len(c.summary.responsibility.split())) for c in module_summaries)
    return body + PROMPT_OVERHEAD_TOKENS + 60
