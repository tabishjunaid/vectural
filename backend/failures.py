"""Failure taxonomy (implementation-plan §5.8, design-document §7.2).

Three qualitatively different failure classes with different responses — and
conflating them is the risk to design against:

- :class:`QuotaExhausted` — **not an error.** Expected, planned-for state. The
  caller waits (a durable timer, §5.7); it does not retry and does not alert.
- :class:`TransientGatewayError` — retry with exponential backoff (:class:`RetryPolicy`),
  but the spend must still be surfaced in accounting even on eventual success.
- :class:`ContentFailure` — dead-letter to Postgres and continue; never block a
  whole batch on one malformed file (atomicity is per-service, not per-batch).
"""

from __future__ import annotations

from dataclasses import dataclass


class QuotaExhausted(Exception):
    """The shared pool (or the current tranche/reserve) has no room. Control
    flow, not an error — the caller parks on a durable timer until replenishment."""

    def __init__(
        self, message: str = "quota exhausted", *, retry_after_seconds: float | None = None
    ):
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class TransientGatewayError(Exception):
    """A retryable gateway failure (502, timeout, rate-limit). Subject to the
    retry policy; its token spend, if any, must still be accounted."""

    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class ContentFailure(Exception):
    """A per-item failure: parse error, malformed model output, oversized file.
    Dead-lettered for weekly review; the batch continues."""

    def __init__(self, message: str, *, kind: str = "content", detail: str | None = None):
        super().__init__(message)
        self.kind = kind
        self.detail = detail


@dataclass(frozen=True)
class RetryPolicy:
    """Exponential backoff with a cap. Applies only to transient gateway errors —
    never to :class:`QuotaExhausted` (which is not retried) or
    :class:`ContentFailure` (which is dead-lettered, not retried)."""

    max_attempts: int = 4
    base_delay_seconds: float = 1.0
    multiplier: float = 2.0
    max_delay_seconds: float = 30.0

    def delay_for(self, attempt: int) -> float:
        """Backoff before the given 1-indexed attempt (attempt 1 → 0, no wait)."""
        if attempt <= 1:
            return 0.0
        delay = self.base_delay_seconds * (self.multiplier ** (attempt - 2))
        return min(delay, self.max_delay_seconds)

    def should_retry(self, attempt: int) -> bool:
        return attempt < self.max_attempts
