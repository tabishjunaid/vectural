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
    estate_root: Path = Field(default=Path("."))
    manifest_path: Path = Field(default=Path("manifest.yaml"))


def load_settings() -> Settings:
    return Settings()
