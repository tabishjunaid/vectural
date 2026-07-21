"""In-process metrics collector (§7.1).

Aggregates the design's dashboard metrics: token spend by persona / task / model
(fed automatically as a router ``TokenSink``), plus answer-mode rates (refusal,
cache-hit) and retrieval latency percentiles (fed by the answer path). The
snapshot is exactly what a read-only ops view (ui-plan UI-5) renders and what the
OTel exporter ships to SigNoz.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field

from pydantic import BaseModel

from backend.answer.models import AnswerMode
from backend.llm.base import UsageRecord


class MetricsSnapshot(BaseModel):
    total_calls: int
    total_input_tokens: int
    total_output_tokens: int
    tokens_by_task: dict[str, int]
    tokens_by_persona: dict[str, int]
    tokens_by_model: dict[str, int]
    answers_total: int
    refusal_rate: float
    cache_hit_rate: float
    latency_p50_ms: float
    latency_p95_ms: float


@dataclass
class MetricsCollector:
    """Thread-unsafe by design — one per worker, exported periodically."""

    _tokens_by_task: Counter[str] = field(default_factory=Counter)
    _tokens_by_persona: Counter[str] = field(default_factory=Counter)
    _tokens_by_model: Counter[str] = field(default_factory=Counter)
    _input_tokens: int = 0
    _output_tokens: int = 0
    _calls: int = 0
    _answers_by_mode: Counter[str] = field(default_factory=Counter)
    _latencies_ms: list[float] = field(default_factory=list)

    # -- TokenSink ---------------------------------------------------------- #

    def record(self, usage: UsageRecord) -> None:
        self._calls += 1
        self._input_tokens += usage.input_tokens
        self._output_tokens += usage.output_tokens
        self._tokens_by_task[usage.task_type.value] += usage.total_tokens
        self._tokens_by_persona[usage.persona.value if usage.persona else "none"] += (
            usage.total_tokens
        )
        self._tokens_by_model[usage.model.value] += usage.total_tokens

    # -- answer-path metrics ------------------------------------------------ #

    def record_answer(self, mode: AnswerMode) -> None:
        self._answers_by_mode[mode.value] += 1

    def record_latency_ms(self, latency_ms: float) -> None:
        self._latencies_ms.append(latency_ms)

    # -- export ------------------------------------------------------------- #

    def snapshot(self) -> MetricsSnapshot:
        answers_total = sum(self._answers_by_mode.values())
        refusals = self._answers_by_mode.get(AnswerMode.REFUSAL.value, 0)
        cache_hits = self._answers_by_mode.get(AnswerMode.INSTANT.value, 0)
        return MetricsSnapshot(
            total_calls=self._calls,
            total_input_tokens=self._input_tokens,
            total_output_tokens=self._output_tokens,
            tokens_by_task=dict(self._tokens_by_task),
            tokens_by_persona=dict(self._tokens_by_persona),
            tokens_by_model=dict(self._tokens_by_model),
            answers_total=answers_total,
            refusal_rate=_ratio(refusals, answers_total),
            cache_hit_rate=_ratio(cache_hits, answers_total),
            latency_p50_ms=_percentile(self._latencies_ms, 0.50),
            latency_p95_ms=_percentile(self._latencies_ms, 0.95),
        )


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _percentile(samples: list[float], pct: float) -> float:
    if not samples:
        return 0.0
    ordered = sorted(samples)
    rank = (len(ordered) - 1) * pct
    low = math.floor(rank)
    high = math.ceil(rank)
    if low == high:
        return ordered[int(rank)]
    return ordered[low] * (high - rank) + ordered[high] * (rank - low)
