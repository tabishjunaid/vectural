"""Eval metrics and harness (§7)."""

from __future__ import annotations

import pytest

from backend.eval import (
    load_golden_set,
    rank_of_first_relevant,
    recall_at_k,
    reciprocal_rank,
    run_eval,
)
from backend.eval.metrics import hit_at_k
from backend.retrieval.service import RetrievalService

# --- pure metrics ----------------------------------------------------------- #


def test_rank_and_reciprocal_rank() -> None:
    retrieved = ["a.py", "b.py", "c.py"]
    assert rank_of_first_relevant(retrieved, {"c.py"}) == 3
    assert reciprocal_rank(retrieved, {"c.py"}) == pytest.approx(1 / 3)
    assert rank_of_first_relevant(retrieved, {"z.py"}) is None
    assert reciprocal_rank(retrieved, {"z.py"}) == 0.0


def test_recall_and_hit_at_k() -> None:
    retrieved = ["a.py", "b.py", "c.py", "d.py"]
    relevant = {"b.py", "d.py"}
    assert recall_at_k(retrieved, relevant, 2) == pytest.approx(0.5)  # only b in top-2
    assert recall_at_k(retrieved, relevant, 4) == pytest.approx(1.0)
    assert hit_at_k(retrieved, relevant, 2) is True
    assert hit_at_k(retrieved, relevant, 1) is False


def test_recall_empty_relevant_is_zero() -> None:
    assert recall_at_k(["a"], set(), 5) == 0.0


# --- golden set + harness --------------------------------------------------- #

GOLDEN_YAML = """
questions:
  - id: q-refund
    question: how does a refund reversal propagate across services
    target_paths: ["payments-api/refund.py"]
  - id: q-charge
    question: where is a charge applied to the ledger
    target_paths: ["ledger-svc/index.ts"]
  - id: q-java-refund
    question: java handler that reverses a refund and publishes an event
    target_paths: ["booking-api/src/RefundHandler.java"]
"""


def test_load_golden_set() -> None:
    golden = load_golden_set(GOLDEN_YAML)
    assert len(golden) == 3
    assert golden[0].relevant == {"payments-api/refund.py"}


def test_run_eval_over_estate(retrieval_service: RetrievalService) -> None:
    golden = load_golden_set(GOLDEN_YAML)
    report = run_eval(retrieval_service, golden, k_values=(1, 5, 10))

    assert report.count == 3
    # Retrieval over this tiny, well-separated estate should be strong.
    assert report.mean_recall[10] == pytest.approx(1.0)
    assert report.mrr > 0.5
    assert 0.0 <= report.hit_rate[1] <= 1.0
    assert "recall@10" in report.summary()


def test_run_eval_empty_golden_raises(retrieval_service: RetrievalService) -> None:
    with pytest.raises(ValueError, match="empty"):
        run_eval(retrieval_service, [])
