"""Tier-4 flow narratives over Postgres (§4.4) — a durable :class:`FlowStore`.

Same protocol as :class:`InMemoryFlowStore`. The whole narrative (including its
review lifecycle) is stored as JSONB with the status pulled out as a column, so
the store stays a dumb repository and all transitions remain in the service.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from backend.flows.models import FlowNarrative

if TYPE_CHECKING:
    from psycopg import Connection


class PgFlowStore:
    """``flow_narratives`` over Postgres (FlowStore)."""

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def get(self, flow_id: str) -> FlowNarrative | None:
        with self._conn.cursor() as cur:
            cur.execute("SELECT data FROM flow_narratives WHERE id = %s", (flow_id,))
            row = cur.fetchone()
        return _to_narrative(row[0]) if row is not None else None

    def upsert(self, narrative: FlowNarrative) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                "INSERT INTO flow_narratives (id, status, data, updated_at) "
                "VALUES (%s, %s, %s, %s) "
                "ON CONFLICT (id) DO UPDATE SET "
                "status = EXCLUDED.status, data = EXCLUDED.data, updated_at = EXCLUDED.updated_at",
                (
                    narrative.id, narrative.status.value,
                    narrative.model_dump_json(), narrative.updated_at,
                ),
            )

    def all(self) -> list[FlowNarrative]:
        with self._conn.cursor() as cur:
            cur.execute("SELECT data FROM flow_narratives ORDER BY id")
            return [_to_narrative(row[0]) for row in cur.fetchall()]


def _to_narrative(data: Any) -> FlowNarrative:
    if isinstance(data, str):
        return FlowNarrative.model_validate_json(data)
    return FlowNarrative.model_validate(data)  # psycopg returns JSONB as dict
