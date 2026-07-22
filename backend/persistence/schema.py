"""PostgreSQL DDL for the system-of-record tables (§3.3).

Generated as SQL strings so the schema is reviewable and versionable in the repo
(and applied by a thin migration runner in the real deployment). Every other
store rebuilds from git + these tables, so this is the schema that actually
matters for durability.
"""

from __future__ import annotations

# Kept minimal-but-faithful to the §3.3 table set. Columns mirror the Pydantic
# models in this package; extend alongside them, never independently.
DDL_STATEMENTS: tuple[str, ...] = (
    # Last-processed commit per repository (freshness pipeline, reconciliation).
    """
    CREATE TABLE IF NOT EXISTS repo_state (
        repository   TEXT PRIMARY KEY,
        last_commit  TEXT NOT NULL,
        updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    # Per-file indexing status — the resume/dedup key (§Phase 5 exit criterion).
    """
    CREATE TABLE IF NOT EXISTS file_ledger (
        service         TEXT NOT NULL,
        path            TEXT NOT NULL,
        content_hash    TEXT NOT NULL,
        prompt_version  TEXT NOT NULL,
        tier            INT  NOT NULL,
        status          TEXT NOT NULL,
        updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (service, path)
    )
    """,
    # Single shared-pool counter across models (§4.3: one counter, not one per model).
    """
    CREATE TABLE IF NOT EXISTS quota_ledger (
        period_start             DATE PRIMARY KEY,
        monthly_budget           BIGINT NOT NULL,
        serving_reserve_fraction DOUBLE PRECISION NOT NULL,
        spent_indexing           BIGINT NOT NULL DEFAULT 0,
        spent_serving            BIGINT NOT NULL DEFAULT 0,
        updated_at               TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    # Semantic answer cache (embedding -> answer); fast-path lookups (§5.5).
    """
    CREATE TABLE IF NOT EXISTS answer_cache (
        id           BIGSERIAL PRIMARY KEY,
        question     TEXT NOT NULL,
        embedding    BYTEA NOT NULL,
        answer       JSONB NOT NULL,
        commit_sha   TEXT NOT NULL,
        created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    # Golden-set run results, versioned; CI gate (§7).
    """
    CREATE TABLE IF NOT EXISTS eval_runs (
        id           BIGSERIAL PRIMARY KEY,
        suite        TEXT NOT NULL,
        commit_sha   TEXT NOT NULL,
        metrics      JSONB NOT NULL,
        created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    # Content failures for weekly review (§5.8).
    """
    CREATE TABLE IF NOT EXISTS dead_letter (
        id         BIGSERIAL PRIMARY KEY,
        service    TEXT NOT NULL,
        path       TEXT NOT NULL,
        kind       TEXT NOT NULL,
        detail     TEXT,
        at         TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    # Which services are indexed, at what tier, scheduled when (§5.4).
    """
    CREATE TABLE IF NOT EXISTS coverage_manifest (
        service         TEXT PRIMARY KEY,
        tier            INT  NOT NULL DEFAULT 0,
        status          TEXT NOT NULL,
        last_indexed    TIMESTAMPTZ,
        next_scheduled  TEXT,
        updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    # Tier 1/2/3 summaries (file / module / service), content-hash keyed so a
    # higher tier regenerates only when a child changes (§5.2, §5.9). Durable so
    # tiers 2-3 survive a resume that skips already-summarised tier-1 files.
    """
    CREATE TABLE IF NOT EXISTS summaries (
        tier            INT  NOT NULL,
        key             TEXT NOT NULL,
        kind            TEXT NOT NULL,
        text            TEXT NOT NULL,
        data            JSONB NOT NULL,
        content_hash    TEXT NOT NULL,
        prompt_version  TEXT NOT NULL,
        updated_at      TIMESTAMPTZ NOT NULL,
        PRIMARY KEY (tier, key)
    )
    """,
    # Tier-4 cross-service flow narratives + review lifecycle (§4.4, §Phase 7).
    """
    CREATE TABLE IF NOT EXISTS flow_narratives (
        id              TEXT PRIMARY KEY,
        status          TEXT NOT NULL,
        data            JSONB NOT NULL,
        updated_at      TIMESTAMPTZ NOT NULL
    )
    """,
)


def schema_sql() -> str:
    """The full schema as one script (statements separated by semicolons)."""
    return ";\n\n".join(stmt.strip() for stmt in DDL_STATEMENTS) + ";\n"
