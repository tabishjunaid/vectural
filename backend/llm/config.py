"""Task → model / mode configuration (§5.6, §5.2).

Model selection is a **config mapping, not inline logic** — swapping Haiku↔Sonnet
for a task is a change here, not a code change (§2.1). Pushing volume down to
Haiku directly buys Sonnet headroom because the pool is shared (§2 gateway note).
"""

from __future__ import annotations

from backend.domain.models import TaskType
from backend.llm.base import ModelName

# Per implementation-plan §5.2 / §5.3 / §5.4.
_TASK_MODEL: dict[TaskType, ModelName] = {
    TaskType.FILE_SUMMARY: ModelName.HAIKU,  # tier 1, high volume
    TaskType.MODULE_SUMMARY: ModelName.HAIKU,  # tier 2
    TaskType.SERVICE_SUMMARY: ModelName.SONNET,  # tier 3
    TaskType.FLOW_NARRATIVE: ModelName.SONNET,  # tier 4
    TaskType.ENTITY_LINKING: ModelName.HAIKU,  # retrieval step 1
    TaskType.CYPHER_GENERATION: ModelName.SONNET,  # retrieval step 2
    TaskType.SYNTHESIS: ModelName.SONNET,  # answer path
    TaskType.GROUNDEDNESS: ModelName.HAIKU,  # answer gate
}


def model_for(task_type: TaskType) -> ModelName:
    return _TASK_MODEL[task_type]


def task_uses_json(task_type: TaskType) -> bool:
    """JSON mode everywhere except answer synthesis (§5.6) — synthesis emits
    persona prose, every other task emits a structured object."""
    return task_type is not TaskType.SYNTHESIS


def task_temperature(task_type: TaskType) -> float:
    """Near-zero temperature everywhere except synthesis, which needs a little
    room for natural persona-appropriate phrasing (§5.6)."""
    return 0.3 if task_type is TaskType.SYNTHESIS else 0.0
