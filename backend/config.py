"""Runtime configuration.

Phase 1 needs almost nothing here — the deterministic pipeline takes its inputs
as explicit arguments. This module exists so later phases (gateway endpoint,
store DSNs, quota reserve) have one typed, env-driven home and never scatter
``os.environ`` reads across the codebase.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="VECTURAL_", env_file=".env", extra="ignore")

    # Root under which all repositories live (implementation-plan §4.4).
    estate_root: Path = Field(default=Path("sample-estate"))
    manifest_path: Path = Field(default=Path("sample-estate/manifest.yaml"))

    # Whether to run tiers 1-4 summarisation over the estate on boot (fake gateway,
    # no real spend) so coverage/review have content to serve.
    summarise_on_boot: bool = Field(default=True)

    # Origins allowed to call the API (the React dev server, by default).
    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:5175", "http://localhost:5173"]
    )

    # Backing stores: "inmemory" (default, no infra) or "real" (OpenSearch + Neo4j
    # + Postgres). The real path uses these connection settings.
    backing: str = Field(default="inmemory")
    opensearch_url: str = Field(default="http://localhost:9200")
    opensearch_index: str = Field(default="vectural-chunks-code")
    neo4j_uri: str = Field(default="bolt://localhost:7687")
    neo4j_user: str = Field(default="neo4j")
    neo4j_password: str = Field(default="vecturalpw")
    postgres_dsn: str = Field(
        default="postgresql://vectural:vectural@localhost:5432/vectural"
    )

    # Whether to (re)index into the real stores on app boot. Default False: real
    # indexing is a separate durable job (the Temporal indexing worker), so booting
    # the API connects to already-populated stores and serves — it never re-indexes.
    index_on_boot: bool = Field(default=False)

    # Quota policy — the shared pool the indexing worker and the serving API both
    # draw from (§5.7). Persisted in the quota_ledger row; these are the defaults
    # used when creating a fresh period.
    monthly_budget: int = Field(default=50_000_000)
    serving_reserve_fraction: float = Field(default=0.30)
    tranche_count: int = Field(default=4)

    # Temporal — the durable indexing orchestrator (§5.7). Used by the worker
    # (backend.orchestration.worker) and starter (backend.orchestration.starter).
    temporal_target: str = Field(default="localhost:7233")
    temporal_namespace: str = Field(default="default")
    temporal_task_queue: str = Field(default="vectural-indexing")

    # Dense-retrieval embedder: "hashing" (default, offline stand-in) or "bge-m3"
    # (real BGE-M3, local, 1024-dim, via the `embeddings` extra). Index-time and
    # query-time MUST use the same one, so set this for both worker and API.
    embedder: str = Field(default="hashing")

    # LLM gateway (§2 licence boundary): "fake" (default, no spend) or "real". The
    # real client is NOT shipped — supply your own GatewayClient implementation and
    # point at it: gateway_client="your_pkg.module:YourGatewayClient". Opus never
    # authors or operates this egress (design §2).
    gateway: str = Field(default="fake")
    gateway_client: str = Field(default="")


def load_settings() -> Settings:
    return Settings()
