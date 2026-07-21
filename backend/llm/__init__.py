"""LLM routing layer (implementation-plan §5.6, design-document §5.1).

**The single egress point.** Every call into either model goes through
:class:`LLMRouter`. It is the *only* component permitted an HTTP client pointed
at the gateway — that is what makes the model/licence boundary (§2) a structural
property of the codebase rather than a reviewed convention (§7.3).

The router owns model selection, prompt versioning, and synchronous token
accounting. It does **not** own prompt content — callers pass a rendered prompt;
the frozen templates live with each task.
"""

from backend.llm.base import (
    GatewayClient,
    GatewayRequest,
    GatewayResult,
    ModelName,
    RoutedResponse,
    UsageRecord,
)
from backend.llm.config import model_for, task_temperature, task_uses_json
from backend.llm.fake import FakeGatewayClient
from backend.llm.router import LLMRouter, TokenSink

__all__ = [
    "FakeGatewayClient",
    "GatewayClient",
    "GatewayRequest",
    "GatewayResult",
    "LLMRouter",
    "ModelName",
    "RoutedResponse",
    "TokenSink",
    "UsageRecord",
    "model_for",
    "task_temperature",
    "task_uses_json",
]
