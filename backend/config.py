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

from backend.summarise.driver import DEFAULT_MAX_INPUT_TOKENS


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

    # Largest tier-1 prompt to send, in tokens. A file estimated above this is
    # dead-lettered as an oversized content failure (§5.8) instead of being sent —
    # the provider would reject it with a permanent 400, which previously aborted the
    # whole service batch. Lower this for a gateway with a smaller context window.
    tier1_max_input_tokens: int = Field(default=DEFAULT_MAX_INPUT_TOKENS)

    # Temporal — the durable indexing orchestrator (§5.7). Used by the worker
    # (backend.orchestration.worker) and starter (backend.orchestration.starter).
    temporal_target: str = Field(default="localhost:7233")
    temporal_namespace: str = Field(default="default")
    temporal_task_queue: str = Field(default="vectural-indexing")

    # Dense-retrieval embedder: "hashing" (default, offline stand-in) or "bge-m3"
    # (real BGE-M3, local, 1024-dim, via the `embeddings` extra). Index-time and
    # query-time MUST use the same one, so set this for both worker and API.
    embedder: str = Field(default="hashing")

    # Rerank stage (§5.3 step 6). "bge" is the real cross-encoder; "token-overlap"
    # is the offline stand-in; "noop" keeps the fused order. Default stays the
    # stand-in so offline tests and the no-model demo behave as before.
    reranker: str = Field(default="token-overlap")

    # LLM gateway (§5.6) — the single model egress. One of:
    #   "fake"      (default, canned, no spend)
    #   "anthropic" (Claude; alias: "real")   — key from ANTHROPIC_API_KEY / ant profile
    #   "openai"    (GPT)                      — key from OPENAI_API_KEY
    # Or set gateway_client="your_pkg.module:YourGatewayClient" to inject your own.
    # Keys are always read from the environment by each SDK, never stored here.
    gateway: str = Field(default="fake")
    gateway_client: str = Field(default="")
    # Concrete model ids per logical tier (blank → the client's defaults).
    anthropic_haiku_model: str = Field(default="")   # default claude-haiku-4-5
    anthropic_sonnet_model: str = Field(default="")  # default claude-sonnet-5
    # Point the Anthropic-compatible client at a company AI Gateway that fronts
    # Claude (its own key + URL, §2). Blank → api.anthropic.com.
    anthropic_base_url: str = Field(default="")
    openai_small_model: str = Field(default="")      # default gpt-4o-mini
    openai_large_model: str = Field(default="")      # default gpt-4o
    # Point the OpenAI-compatible client at a different endpoint — e.g. a company AI
    # gateway that issues its own key + URL. Blank → api.openai.com. Swapping between
    # the two is just this URL + the key; no code change.
    openai_base_url: str = Field(default="")


def load_settings() -> Settings:
    return Settings()
