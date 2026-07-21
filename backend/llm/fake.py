"""Deterministic fake gateway client (dev / test).

Stands in for the company AI gateway so the entire routing + accounting + quota +
summarisation loop runs with **zero real spend and no network**. It returns
schema-shaped JSON for structured tasks and prose for synthesis, and counts
tokens deterministically. It can also simulate the two gateway-side failure
modes (§5.8): transient errors (to exercise retry) and malformed output (to
exercise the content-failure/dead-letter path).
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable

from backend.domain.models import TaskType
from backend.failures import TransientGatewayError
from backend.llm.base import GatewayRequest, GatewayResult

Responder = Callable[[GatewayRequest], str]

_MARKER = re.compile(r"\[([^\[\]]+)\]")


def _first_chunk_id(prompt: str) -> str | None:
    """Pick the first bracketed marker that looks like a real chunk_id.

    Chunk ids are ``service:path:lines:hash`` (they contain colons), so this
    skips the literal ``[id]`` example the instruction text uses."""
    for match in _MARKER.finditer(prompt):
        marker = match.group(1)
        if ":" in marker:
            return marker
    return None


def _default_responder(request: GatewayRequest) -> str:
    if not request.json_mode:  # synthesis: prose that cites the first evidence id
        chunk_id = _first_chunk_id(request.prompt)
        citation = f" [{chunk_id}]" if chunk_id else ""
        return f"Based on the retrieved evidence, the change propagates as described.{citation}"
    if request.task_type is TaskType.FILE_SUMMARY:
        return json.dumps(
            {
                "purpose": "Handles the operation described in the file.",
                "key_operations": ["operation_a", "operation_b"],
                "business_concepts": ["concept_x"],
                "external_calls": [],
            }
        )
    if request.task_type is TaskType.MODULE_SUMMARY:
        return json.dumps(
            {"responsibility": "Coordinates the module's operations.", "key_files": []}
        )
    if request.task_type is TaskType.SERVICE_SUMMARY:
        return json.dumps(
            {"description": "Delivers the service's business capability.", "capabilities": []}
        )
    if request.task_type is TaskType.GROUNDEDNESS:
        return json.dumps({"grounded": True, "unsupported_claims": []})
    if request.task_type is TaskType.ENTITY_LINKING:
        return json.dumps({"anchors": [], "capabilities": []})
    if request.task_type is TaskType.CYPHER_GENERATION:
        return json.dumps(
            {"cypher": "MATCH (s:Service)-[:CALLS*1..2]->(d:Service) RETURN d.key"}
        )
    if request.task_type is TaskType.FLOW_NARRATIVE:
        return json.dumps(
            {
                "narrative": "The services collaborate in sequence to complete the flow.",
                "summary": "Cross-service flow.",
            }
        )
    return json.dumps({"ok": True, "task": request.task_type.value})


class FakeGatewayClient:
    """A deterministic gateway. ``responder`` customises the body; ``fail_times``
    raises transient errors on the first N calls; ``malformed`` returns non-JSON
    for JSON-mode tasks."""

    def __init__(
        self,
        responder: Responder | None = None,
        *,
        fail_times: int = 0,
        malformed: bool = False,
        crash_after: int | None = None,
    ) -> None:
        self._responder = responder or _default_responder
        self._fail_times = fail_times
        self._malformed = malformed
        # Simulate a worker being killed mid-run: after this many calls, raise a
        # non-retryable error (models a Temporal worker crash, for resume tests).
        self._crash_after = crash_after
        self.calls = 0

    def complete(self, request: GatewayRequest) -> GatewayResult:
        self.calls += 1
        if self._crash_after is not None and self.calls > self._crash_after:
            raise RuntimeError("worker killed mid-workflow")
        if self.calls <= self._fail_times:
            raise TransientGatewayError("simulated transient error", status_code=503)

        text = "this is not json {" if (self._malformed and request.json_mode) else self._responder(
            request
        )
        return GatewayResult(
            text=text,
            input_tokens=_count(request.prompt) + _count(request.system or ""),
            output_tokens=_count(text),
        )


def _count(text: str) -> int:
    """A deterministic token proxy. Real counts come from the gateway; this only
    needs to be stable and roughly proportional for offline accounting/tests."""
    return len(text.split())
