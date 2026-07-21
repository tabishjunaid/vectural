"""Run a :class:`RetrievalService` over a golden set and score it (§7).

Produces an :class:`EvalReport` — per-question ranks plus aggregate recall@k and
MRR. In CI this is the gate: a regression in these numbers blocks a prompt,
model, chunking, or schema change (§7 "regression blocks merge").
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from backend.eval.metrics import hit_at_k, rank_of_first_relevant, recall_at_k, reciprocal_rank
from backend.eval.models import GoldenQuestion
from backend.retrieval.service import RetrievalService

DEFAULT_K_VALUES = (1, 5, 10)


@dataclass
class QuestionEval:
    question_id: str
    retrieved_paths: list[str]
    first_relevant_rank: int | None
    reciprocal_rank: float
    recall: dict[int, float] = field(default_factory=dict)
    hit: dict[int, bool] = field(default_factory=dict)


@dataclass
class EvalReport:
    per_question: list[QuestionEval]
    mrr: float
    mean_recall: dict[int, float]
    hit_rate: dict[int, float]

    @property
    def count(self) -> int:
        return len(self.per_question)

    def summary(self) -> str:
        lines = [f"eval over {self.count} questions", f"  MRR            {self.mrr:.3f}"]
        for k in sorted(self.mean_recall):
            lines.append(
                f"  recall@{k:<3}    {self.mean_recall[k]:.3f}"
                f"   hit@{k:<3} {self.hit_rate[k]:.3f}"
            )
        return "\n".join(lines)


def run_eval(
    service: RetrievalService,
    golden: list[GoldenQuestion],
    *,
    k_values: tuple[int, ...] = DEFAULT_K_VALUES,
    candidate_k: int = 50,
) -> EvalReport:
    if not golden:
        raise ValueError("golden set is empty")
    max_k = max(k_values)
    # Fetch at least max_k results, and rerank over an even wider candidate pool.
    top_n = max_k
    candidate_k = max(candidate_k, top_n)

    per_question: list[QuestionEval] = []
    for q in golden:
        scope = set(q.services) if q.services else None
        hits = service.search(q.question, services=scope, candidate_k=candidate_k, top_n=top_n)
        retrieved = _dedupe_paths(h.path for h in hits)
        relevant = q.relevant
        per_question.append(
            QuestionEval(
                question_id=q.id,
                retrieved_paths=retrieved,
                first_relevant_rank=rank_of_first_relevant(retrieved, relevant),
                reciprocal_rank=reciprocal_rank(retrieved, relevant),
                recall={k: recall_at_k(retrieved, relevant, k) for k in k_values},
                hit={k: hit_at_k(retrieved, relevant, k) for k in k_values},
            )
        )

    n = len(per_question)
    mrr = sum(qe.reciprocal_rank for qe in per_question) / n
    mean_recall = {
        k: sum(qe.recall[k] for qe in per_question) / n for k in k_values
    }
    hit_rate = {
        k: sum(1 for qe in per_question if qe.hit[k]) / n for k in k_values
    }
    return EvalReport(
        per_question=per_question, mrr=mrr, mean_recall=mean_recall, hit_rate=hit_rate
    )


def _dedupe_paths(paths: Iterable[str]) -> list[str]:
    """Collapse multiple chunks from the same file to one ranked path entry,
    preserving first-seen (highest-ranked) order."""
    out: list[str] = []
    seen: set[str] = set()
    for path in paths:
        if path not in seen:
            seen.add(path)
            out.append(path)
    return out
