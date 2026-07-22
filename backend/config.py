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


def load_settings() -> Settings:
    return Settings()
