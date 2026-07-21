"""Golden-set schema (§7). Versioned in git; grown from real team questions."""

from __future__ import annotations

from typing import Any

import yaml
from pydantic import BaseModel, Field

from backend.domain.models import Persona


class GoldenQuestion(BaseModel):
    """One evaluated question with its known-correct file targets."""

    id: str
    question: str
    persona: Persona = Persona.ENGINEER
    target_paths: list[str] = Field(min_length=1)
    services: list[str] | None = None  # optional retrieval scope for this question

    @property
    def relevant(self) -> set[str]:
        return set(self.target_paths)


def load_golden_set(source: str) -> list[GoldenQuestion]:
    """Parse a golden set from YAML: a top-level ``questions:`` list."""
    raw: Any = yaml.safe_load(source)
    if not isinstance(raw, dict) or "questions" not in raw:
        raise ValueError("golden set must be a mapping with a top-level 'questions' key")
    return [GoldenQuestion.model_validate(q) for q in raw["questions"]]
