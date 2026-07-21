"""LLM routing layer — the single gateway egress (§5.6)."""

from __future__ import annotations

import pytest

from backend.domain.models import Persona, TaskType
from backend.failures import ContentFailure, RetryPolicy
from backend.llm import FakeGatewayClient, LLMRouter, ModelName, UsageRecord
from backend.llm.config import model_for, task_temperature, task_uses_json


class _Sink:
    def __init__(self) -> None:
        self.records: list[UsageRecord] = []

    def record(self, usage: UsageRecord) -> None:
        self.records.append(usage)


def test_model_selection_is_config_driven() -> None:
    assert model_for(TaskType.FILE_SUMMARY) is ModelName.HAIKU
    assert model_for(TaskType.SERVICE_SUMMARY) is ModelName.SONNET
    assert model_for(TaskType.SYNTHESIS) is ModelName.SONNET
    assert model_for(TaskType.GROUNDEDNESS) is ModelName.HAIKU


def test_json_mode_and_temperature_policy() -> None:
    assert task_uses_json(TaskType.FILE_SUMMARY) is True
    assert task_uses_json(TaskType.SYNTHESIS) is False  # synthesis is prose
    assert task_temperature(TaskType.FILE_SUMMARY) == 0.0
    assert task_temperature(TaskType.SYNTHESIS) > 0.0


def test_route_records_usage_synchronously() -> None:
    sink = _Sink()
    router = LLMRouter(FakeGatewayClient(), sinks=[sink])
    resp = router.route(
        TaskType.FILE_SUMMARY, "file-v1", {"prompt": "a b c"}, Persona.ENGINEER
    )
    assert resp.model is ModelName.HAIKU
    assert resp.parsed is not None and "purpose" in resp.parsed
    assert len(sink.records) == 1
    assert sink.records[0].prompt_version == "file-v1"
    assert sink.records[0].total_tokens > 0


def test_synthesis_returns_prose_not_parsed() -> None:
    router = LLMRouter(FakeGatewayClient())
    resp = router.route(TaskType.SYNTHESIS, "synth-v1", {"prompt": "answer"})
    assert resp.parsed is None
    assert resp.text


def test_retry_on_transient_then_success() -> None:
    gw = FakeGatewayClient(fail_times=2)
    router = LLMRouter(gw, retry_policy=RetryPolicy(max_attempts=4), sleep=lambda _: None)
    resp = router.route(TaskType.MODULE_SUMMARY, "v", {"prompt": "x"})
    assert resp.parsed is not None
    assert gw.calls == 3  # 2 failures + 1 success


def test_retry_gives_up_after_max_attempts() -> None:
    from backend.failures import TransientGatewayError

    gw = FakeGatewayClient(fail_times=10)
    router = LLMRouter(gw, retry_policy=RetryPolicy(max_attempts=3), sleep=lambda _: None)
    with pytest.raises(TransientGatewayError):
        router.route(TaskType.FILE_SUMMARY, "v", {"prompt": "x"})


def test_malformed_json_raises_content_failure_but_still_accounts() -> None:
    sink = _Sink()
    router = LLMRouter(FakeGatewayClient(malformed=True), sinks=[sink])
    with pytest.raises(ContentFailure):
        router.route(TaskType.FILE_SUMMARY, "v", {"prompt": "x"})
    assert len(sink.records) == 1  # spend surfaced even on failure (§7.2)


def test_empty_prompt_rejected() -> None:
    router = LLMRouter(FakeGatewayClient())
    with pytest.raises(ValueError, match="prompt"):
        router.route(TaskType.FILE_SUMMARY, "v", {"prompt": ""})
