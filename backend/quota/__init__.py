"""Quota governance (implementation-plan §5.7, design-document §4.3).

The quota is a single monthly per-user pool shared across models. Two nested
periods apply: the budget replenishes **monthly** but is spent on a **weekly**
pace. This package enforces the two invariants the design makes load-bearing:

- **one counter, not one per model** — a Sonnet call and a Haiku call decrement
  the identical pool; there is no per-model accounting that could let one model
  silently starve the other (§2.1, §4.3)
- **tranches never borrow forward** — a week-one overspend cannot be repaid by
  consuming week-two's allocation in advance, which bounds the blast radius of an
  overspend to "this week's indexing pauses", not "three weeks dark" (§8 risk 2)
"""

from backend.quota.governor import BudgetDecision, QuotaGovernor, TokenBucket, bin_pack
from backend.quota.ledger import (
    QuotaAccountant,
    QuotaConfig,
    QuotaPool,
    is_indexing_task,
)

__all__ = [
    "BudgetDecision",
    "QuotaAccountant",
    "QuotaConfig",
    "QuotaGovernor",
    "QuotaPool",
    "TokenBucket",
    "bin_pack",
    "is_indexing_task",
]
