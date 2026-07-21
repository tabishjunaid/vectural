"""OpenTelemetry → SigNoz exporter (optional ``otel`` extra) — the infra seam.

Ships the :class:`MetricsSnapshot` to SigNoz over OTLP. Import-guarded so the
metrics collector works with no OpenTelemetry installed. SigNoz's OSS core is
MIT; its ``ee/`` module must never be deployed (design-doc §6 licence note).
"""

from __future__ import annotations

from typing import Any

from backend.observability.metrics import MetricsSnapshot


def export_snapshot(
    snapshot: MetricsSnapshot, *, endpoint: str, service_name: str = "vectural"
) -> None:  # pragma: no cover - requires an OTLP collector
    """Emit the snapshot's gauges/counters via OTLP. Lazy-imports opentelemetry."""
    from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader

    reader = PeriodicExportingMetricReader(OTLPMetricExporter(endpoint=endpoint))
    provider = MeterProvider(metric_readers=[reader])
    meter = provider.get_meter(service_name)

    gauges: dict[str, float] = {
        "vectural.tokens.input": snapshot.total_input_tokens,
        "vectural.tokens.output": snapshot.total_output_tokens,
        "vectural.answers.refusal_rate": snapshot.refusal_rate,
        "vectural.answers.cache_hit_rate": snapshot.cache_hit_rate,
        "vectural.latency.p95_ms": snapshot.latency_p95_ms,
    }
    for name, value in gauges.items():
        _observe(meter, name, value)


def _observe(meter: Any, name: str, value: float) -> None:  # pragma: no cover
    meter.create_observable_gauge(
        name, callbacks=[lambda _options: [_measurement(value)]]
    )


def _measurement(value: float) -> Any:  # pragma: no cover
    from opentelemetry.metrics import Observation

    return Observation(value)
