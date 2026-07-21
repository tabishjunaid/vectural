"""Graph-planned retrieval, steps 1-4 (§5.3).

Turns a question into a **bounded service set** so evidence search is scoped to
the relevant subgraph (R2: the graph plans retrieval, vectors fetch evidence
within the plan). The steps:

1. **entity linking** — deterministic match of the question against known service
   keys, augmented by a routed Haiku call (§5.3 step 1)
2. **Cypher generation** — a routed Sonnet call, schema-in-prompt (step 2)
3. **validation** — the Phase 3 validator; one regeneration retry with the
   failure reason appended, then a templated fallback (step 3, §5.2)
4. **scope** — the in-scope service set, expanded from the anchors over the graph
   (step 4; the real backend executes the validated Cypher on Neo4j)
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.domain.models import Persona, TaskType
from backend.graph.cypher_validator import validate_cypher
from backend.graph.queries import StructuralQueries, cypher_service_dependencies
from backend.llm.router import LLMRouter

ENTITY_LINKING_PROMPT_VERSION = "entity-v1"
CYPHER_PROMPT_VERSION = "cypher-v1"


@dataclass
class RetrievalPlan:
    anchors: list[str]
    scope: set[str] | None  # None => search the whole estate (no narrowing)
    cypher: str | None
    used_fallback: bool
    cypher_attempts: int


class RetrievalPlanner:
    def __init__(
        self,
        structural: StructuralQueries,
        router: LLMRouter,
        services: set[str],
        *,
        hops: int = 2,
    ) -> None:
        self._structural = structural
        self._router = router
        self._services = services
        self._hops = hops

    def plan(self, question: str, persona: Persona | None = None) -> RetrievalPlan:
        anchors = self._entity_link(question, persona)
        cypher, used_fallback, attempts = self._plan_cypher(question, anchors, persona)
        scope = self._scope(anchors)
        return RetrievalPlan(
            anchors=anchors,
            scope=scope,
            cypher=cypher,
            used_fallback=used_fallback,
            cypher_attempts=attempts,
        )

    # -- step 1: entity linking -------------------------------------------- #

    def _entity_link(self, question: str, persona: Persona | None) -> list[str]:
        q_low = question.lower()
        deterministic = {svc for svc in self._services if svc.lower() in q_low}

        response = self._router.route(
            TaskType.ENTITY_LINKING,
            ENTITY_LINKING_PROMPT_VERSION,
            {"prompt": f"Name the services this question is about: {question}"},
            persona,
        )
        model_anchors = response.parsed.get("anchors", []) if response.parsed else []
        merged = deterministic | {
            a for a in model_anchors if isinstance(a, str) and a in self._services
        }
        return sorted(merged)

    # -- steps 2-3: cypher generation + validation ------------------------- #

    def _plan_cypher(
        self, question: str, anchors: list[str], persona: Persona | None
    ) -> tuple[str | None, bool, int]:
        candidate = self._generate_cypher(question, anchors, persona, reason=None)
        result = validate_cypher(candidate) if candidate else None
        attempts = 1
        if result is not None and result.ok:
            return candidate, False, attempts

        # One regeneration retry with the failure reason appended (§5.2).
        reason = result.reason if result is not None else "no cypher produced"
        candidate = self._generate_cypher(question, anchors, persona, reason=reason)
        attempts = 2
        result = validate_cypher(candidate) if candidate else None
        if result is not None and result.ok:
            return candidate, False, attempts

        # Templated structural fallback rather than surfacing the error (§5.2).
        return cypher_service_dependencies(self._hops), True, attempts

    def _generate_cypher(
        self, question: str, anchors: list[str], persona: Persona | None, *, reason: str | None
    ) -> str | None:
        retry = f"\nPrevious attempt was rejected: {reason}. Fix it." if reason else ""
        prompt = (
            "Generate a read-only Cypher query over labels "
            "Service, Module, File, Function, Endpoint, Topic, Capability, Flow, ADR. "
            f"Question: {question}. Anchors: {anchors}.{retry}"
        )
        response = self._router.route(
            TaskType.CYPHER_GENERATION, CYPHER_PROMPT_VERSION, {"prompt": prompt}, persona
        )
        if response.parsed is None:
            return None
        cypher = response.parsed.get("cypher")
        return cypher if isinstance(cypher, str) else None

    # -- step 4: scope ----------------------------------------------------- #

    def _scope(self, anchors: list[str]) -> set[str] | None:
        if not anchors:
            return None  # no narrowing — search the whole indexed estate
        scope = set(anchors)
        for anchor in anchors:
            by_hop = self._structural.service_dependencies(anchor, max_hops=self._hops)
            for services in by_hop.values():
                scope.update(services)
        return scope
