"""Observability (implementation-plan §7.1, design-document §7.1).

Every component that spends tokens or serves a query emits to SigNoz via
OpenTelemetry: token spend by persona and task type (from the routing layer),
retrieval latency percentiles, refusal rate, and cache-hit rate. Because quota
pacing depends on the token figures being current, this is a **correctness
dependency, not a monitoring nicety** — a lagging counter would let indexing
overspend before the governor could react.

:class:`MetricsCollector` is a :class:`~backend.llm.router.TokenSink`, so it
receives every routed call's usage for free by being added to the router's sinks.
The real OTel→SigNoz exporter is a thin, optional adapter over the same snapshot
(``otel.py``).
"""

from backend.observability.metrics import MetricsCollector, MetricsSnapshot

__all__ = ["MetricsCollector", "MetricsSnapshot"]
