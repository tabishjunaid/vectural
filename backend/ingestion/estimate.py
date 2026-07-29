"""Per-repo indexing estimate with a model-aware dollar figure (Ingestion UI).

The estimator (:mod:`backend.estimator`) counts tokens but never prices them —
embedding is local (BGE-M3, $0) and the summarisation gateway tokens' cost depends
on which model the user picks. This wraps it for a single repo/service and applies
the model's price, so the UI can show a cost **before** any tokens are spent. A
local Ollama pick prices at $0 (see :func:`backend.llm.catalog.price_of`); an
unpriced model yields ``None`` (shown as "unknown") rather than a misleading $0.
"""

from __future__ import annotations

from pathlib import Path

from backend.domain.manifest import Manifest
from backend.estimator import EstateEstimate, EstimatorConfig, estimate_estate
from backend.llm import catalog


def summarise_cost_usd(estimate: EstateEstimate, model_id: str | None) -> float | None:
    """Best-effort USD cost of the tier-1-3 summarisation for the chosen model.

    Embedding is local, so only the gateway (summarisation) tokens carry a price.
    Inputs (source + summary reads, dominant) are priced at the model's input rate
    and tier-1 outputs at its output rate. ``None`` if the model has no known price;
    ``0.0`` for a local/$0 model."""
    price = catalog.price_of(model_id) if model_id else None
    if price is None:
        return None
    in_price, out_price = price
    inp = sum(
        s.tier1_input + s.tier1_overhead + s.tier2_tokens + s.tier3_tokens
        for s in estimate.services
    )
    out = sum(s.tier1_output for s in estimate.services)
    return round(inp / 1e6 * in_price + out / 1e6 * out_price, 4)


def estimate_repo(
    estate_root: Path,
    manifest: Manifest,
    service: str,
    *,
    model: str | None = None,
    config: EstimatorConfig | None = None,
) -> dict[str, object]:
    """Token + model-aware cost estimate for a single service/repo.

    Runs the estimator over a one-service manifest so only that repo's files are
    walked, then attaches the chosen model + its dollar cost. Raises ``KeyError``
    if the service is not in the manifest."""
    one = Manifest(services=[s for s in manifest.services if s.name == service])
    if not one.services:
        raise KeyError(service)
    estimate = estimate_estate(estate_root, one, config or EstimatorConfig())
    out = estimate.as_dict()
    out["model"] = model
    out["cost_usd"] = summarise_cost_usd(estimate, model)
    return out
