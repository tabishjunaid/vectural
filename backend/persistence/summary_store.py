"""Tier 1/2/3 summaries over Postgres (§5.2) — a durable :class:`SummaryStore`.

Same protocol as :class:`InMemorySummaryStore`, so the summarisation drivers work
unchanged over a real database. Durability matters here: tiers 2-3 aggregate the
tier below, and a resumed indexing run skips already-summarised tier-1 files — so
their summary text must persist, or the higher tiers would have nothing to read.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from backend.summarise.store import SummaryRecord

if TYPE_CHECKING:
    from psycopg import Connection


class PgSummaryStore:
    """``summaries`` over Postgres (SummaryStore)."""

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def get(self, tier: int, key: str) -> SummaryRecord | None:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT tier, kind, key, text, data, content_hash, prompt_version, updated_at "
                "FROM summaries WHERE tier = %s AND key = %s",
                (tier, key),
            )
            row = cur.fetchone()
        return _to_record(row) if row is not None else None

    def upsert(self, record: SummaryRecord) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                "INSERT INTO summaries "
                "(tier, key, kind, text, data, content_hash, prompt_version, updated_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (tier, key) DO UPDATE SET "
                "kind = EXCLUDED.kind, text = EXCLUDED.text, data = EXCLUDED.data, "
                "content_hash = EXCLUDED.content_hash, prompt_version = EXCLUDED.prompt_version, "
                "updated_at = EXCLUDED.updated_at",
                (
                    record.tier, record.key, record.kind, record.text,
                    json.dumps(record.data), record.content_hash,
                    record.prompt_version, record.updated_at,
                ),
            )

    def all(self, tier: int) -> list[SummaryRecord]:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT tier, kind, key, text, data, content_hash, prompt_version, updated_at "
                "FROM summaries WHERE tier = %s ORDER BY key",
                (tier,),
            )
            return [_to_record(row) for row in cur.fetchall()]


def _to_record(row: tuple[Any, ...]) -> SummaryRecord:
    data = row[4] if isinstance(row[4], dict) else json.loads(row[4])
    return SummaryRecord(
        tier=int(row[0]), kind=str(row[1]), key=str(row[2]), text=str(row[3]),
        data=data, content_hash=str(row[5]), prompt_version=str(row[6]), updated_at=row[7],
    )
