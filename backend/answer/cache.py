"""Semantic answer cache (§5.5) — a fast path that skips the gateway entirely.

Keyed on question-embedding similarity (not exact match), so a paraphrase of a
recently-answered question returns instantly. Entries are tagged with the
``commit_sha`` they were produced at; a lookup at a different commit misses,
which is a conservative stand-in for the full freshness invalidation (§4.4,
open item #2) — a cached answer must never outlive the code it cited.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from backend.answer.models import Answer
from backend.domain.models import Depth, Persona
from backend.embedding.base import Embedder, cosine

DEFAULT_SIMILARITY_THRESHOLD = 0.97


@dataclass
class _Entry:
    embedding: list[float]
    commit_sha: str
    persona: Persona
    depth: Depth
    answer: Answer


@dataclass
class SemanticAnswerCache:
    embedder: Embedder
    threshold: float = DEFAULT_SIMILARITY_THRESHOLD
    _entries: list[_Entry] = field(default_factory=list)

    def get(
        self, question: str, commit_sha: str, persona: Persona, depth: Depth = Depth.STANDARD
    ) -> Answer | None:
        # Persona changes the answer's altitude (R6) and depth changes how thorough
        # it is, so both are part of the key. Without depth, asking a question at
        # `brief` and then at `deep` returns the brief answer from cache — the user
        # asks for more and silently gets less.
        query = self.embedder.embed_one(question)
        best: Answer | None = None
        best_sim = self.threshold
        for entry in self._entries:
            if (
                entry.commit_sha != commit_sha
                or entry.persona is not persona
                or entry.depth is not depth
            ):
                continue  # stale commit, different persona, or different depth
            sim = cosine(query, entry.embedding)
            if sim >= best_sim:
                best_sim = sim
                best = entry.answer
        return best

    def put(
        self,
        question: str,
        commit_sha: str,
        persona: Persona,
        answer: Answer,
        depth: Depth = Depth.STANDARD,
    ) -> None:
        self._entries.append(
            _Entry(self.embedder.embed_one(question), commit_sha, persona, depth, answer)
        )

    def invalidate_commit(self, commit_sha: str) -> None:
        """Drop every entry from a superseded commit (cascade-delete hook, §4.4)."""
        self._entries = [e for e in self._entries if e.commit_sha != commit_sha]
