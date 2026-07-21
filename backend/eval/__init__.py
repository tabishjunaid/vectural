"""Evaluation harness (implementation-plan §7).

Not a phase — standing infrastructure from Phase 2 onward. Phase 2 measures
*retrieval* (recall@k, MRR) against a golden set of questions with known-correct
file targets, "the cheapest possible point to discover the chunking strategy is
wrong" (§Phase 2). Answer-quality metrics (citation validity, groundedness,
refusal precision) join later once the answer path exists.
"""

from backend.eval.harness import EvalReport, QuestionEval, run_eval
from backend.eval.metrics import hit_at_k, rank_of_first_relevant, recall_at_k, reciprocal_rank
from backend.eval.models import GoldenQuestion, load_golden_set

__all__ = [
    "EvalReport",
    "GoldenQuestion",
    "QuestionEval",
    "hit_at_k",
    "load_golden_set",
    "rank_of_first_relevant",
    "recall_at_k",
    "reciprocal_rank",
    "run_eval",
]
