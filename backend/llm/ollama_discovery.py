"""Discover which models a local Ollama server actually has pulled.

Each machine pulls different models for its hardware (a 7B on a 16 GB laptop, a
32B on a 64 GB one). Rather than hardcode a list that would offer models a given
box cannot run, we ask Ollama at startup what it has — via its OpenAI-compatible
``GET /v1/models`` — and surface exactly those in the dropdown.

Best-effort and non-fatal: if the server is down or unreachable, we return an
empty list and Ollama simply doesn't appear as a provider. Serving must never
fail to boot because a local model server isn't running.
"""

from __future__ import annotations

import logging

from backend.llm.catalog import SelectableModel

_log = logging.getLogger(__name__)


def discover_ollama_models(
    base_url: str, *, max_output: int = 8192, timeout: float = 2.0
) -> list[SelectableModel]:
    """The locally-pulled Ollama models as selectable catalog entries.

    ``base_url`` is the OpenAI-compatible endpoint (…/v1). Returns ``[]`` on any
    error (server down, bad URL, malformed payload) — the caller treats "no
    models" as "no local provider", so a missing Ollama is invisible, not fatal.
    """
    if not base_url:
        return []
    url = base_url.rstrip("/") + "/models"
    try:
        import httpx

        resp = httpx.get(url, timeout=timeout)
        resp.raise_for_status()
        payload = resp.json()
    except Exception as exc:  # any failure means "no local models" — never fatal
        _log.info("Ollama discovery skipped (%s unreachable: %s)", url, exc)
        return []

    models: list[SelectableModel] = []
    for entry in payload.get("data", []):
        model_id = entry.get("id")
        if not model_id:
            continue
        # Skip embedding models — they can't do chat/synthesis, so offering them in
        # the answer-model dropdown would only produce errors. Embedding models
        # universally carry "embed" in their name (nomic-embed-text, mxbai-embed…).
        if "embed" in model_id.lower():
            continue
        models.append(
            SelectableModel(
                id=model_id,
                label=model_id,  # ollama ids are already human-readable (qwen2.5-coder:7b)
                provider="ollama",
                concrete=model_id,
                max_output=max_output,
                uses_max_completion_tokens=False,
                supports_temperature=True,
                reasoning_effort=None,
            )
        )
    _log.info("Ollama discovery: %d local model(s) from %s", len(models), url)
    return models
