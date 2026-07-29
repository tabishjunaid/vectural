"""Codebase manifest (§4.4 / §3.4).

``manifest.yaml`` is the only human-authored mapping from filesystem path to
graph node identity. It is reviewed like code, not generated. Two invariants
this module enforces so the design-doc guarantee holds:

- A service directory **absent** from the manifest is never walked and never
  silently appears in the graph.
- ``name`` and ``path`` are each unique, so a file resolves to exactly one
  owning service (there is no ambiguity about who owns a chunk).
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any

import yaml
from pydantic import BaseModel, Field, model_validator

from backend.domain.models import Language

# Free-form manifest strings ("java", "typescript") map onto the Language enum.
# Anything unrecognised is a manifest error surfaced at load time, not a silent
# fall-through to UNKNOWN — the manifest is reviewed, so a typo should fail loudly.
_LANGUAGE_ALIASES: dict[str, Language] = {
    "python": Language.PYTHON,
    "py": Language.PYTHON,
    "javascript": Language.JAVASCRIPT,
    "js": Language.JAVASCRIPT,
    "typescript": Language.TYPESCRIPT,
    "ts": Language.TYPESCRIPT,
    "tsx": Language.TSX,
    "java": Language.JAVA,
    "go": Language.GO,
    "golang": Language.GO,
    "ruby": Language.RUBY,
    "rb": Language.RUBY,
    "csharp": Language.CSHARP,
    "c#": Language.CSHARP,
    "cs": Language.CSHARP,
    "kotlin": Language.KOTLIN,
    "kt": Language.KOTLIN,
    "rust": Language.RUST,
    "rs": Language.RUST,
}


class ManifestError(ValueError):
    """Raised when the manifest is structurally or semantically invalid."""


class ServiceManifest(BaseModel):
    """One reviewed service entry."""

    name: str = Field(min_length=1)
    path: str = Field(min_length=1)
    language: Language | None = None
    art: str | None = None
    criticality: str | None = None
    owner: str | None = None
    # The Git URL a repo was added from (Ingestion UI). Optional — hand-authored
    # manifests and the sample estate omit it; it lets the UI key repo state on
    # origin and offer re-clone. Not load-bearing for indexing.
    git_url: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _coerce_language(cls, data: Any) -> Any:
        if isinstance(data, dict) and isinstance(data.get("language"), str):
            raw = data["language"].strip().lower()
            mapped = _LANGUAGE_ALIASES.get(raw)
            if mapped is None:
                raise ManifestError(
                    f"service {data.get('name')!r}: unknown language {data['language']!r}"
                )
            data = {**data, "language": mapped}
        return data

    @model_validator(mode="after")
    def _normalise_path(self) -> ServiceManifest:
        # Normalise to a posix relative path with no leading "./", no trailing
        # slash, and reject attempts to escape the repository root. Only a "./"
        # prefix is stripped — never a leading ".." (that must be rejected).
        raw = self.path.strip()
        while raw.startswith("./"):
            raw = raw[2:]
        norm = PurePosixPath(raw or ".")
        if norm.is_absolute() or ".." in norm.parts:
            raise ManifestError(f"service {self.name!r}: path {self.path!r} must stay within root")
        object.__setattr__(self, "path", str(norm))
        return self


class Manifest(BaseModel):
    """The full reviewed manifest, plus path→service resolution."""

    services: list[ServiceManifest]

    @model_validator(mode="after")
    def _unique_names_and_paths(self) -> Manifest:
        names: set[str] = set()
        paths: set[str] = set()
        for svc in self.services:
            if svc.name in names:
                raise ManifestError(f"duplicate service name {svc.name!r}")
            if svc.path in paths:
                raise ManifestError(f"duplicate service path {svc.path!r}")
            names.add(svc.name)
            paths.add(svc.path)
        return self

    def service_for_path(self, rel_path: str) -> ServiceManifest | None:
        """Return the owning service for a repo-relative file path, or ``None``.

        Longest matching prefix wins, so nested service directories resolve to the
        most specific owner. A path under no manifested service returns ``None`` —
        the caller must then skip it (never index the unmanifested).
        """
        candidate = PurePosixPath(rel_path)
        best: ServiceManifest | None = None
        best_len = -1
        for svc in self.services:
            svc_path = PurePosixPath(svc.path)
            if svc_path == PurePosixPath(".") or _is_relative_to(candidate, svc_path):
                depth = len(svc_path.parts)
                if depth > best_len:
                    best, best_len = svc, depth
        return best


def _is_relative_to(path: PurePosixPath, base: PurePosixPath) -> bool:
    try:
        path.relative_to(base)
        return True
    except ValueError:
        return False


def load_manifest(source: str) -> Manifest:
    """Parse and validate a manifest from YAML text.

    Raises :class:`ManifestError` on any structural problem so a broken manifest
    fails the ingestion run loudly rather than indexing a partial estate.
    """
    try:
        raw = yaml.safe_load(source)
    except yaml.YAMLError as exc:  # pragma: no cover - passthrough of parser detail
        raise ManifestError(f"manifest is not valid YAML: {exc}") from exc

    if not isinstance(raw, dict) or "services" not in raw:
        raise ManifestError("manifest must be a mapping with a top-level 'services' key")

    try:
        return Manifest.model_validate(raw)
    except ManifestError:
        raise
    except Exception as exc:  # pydantic ValidationError -> ManifestError for a single surface
        raise ManifestError(str(exc)) from exc


def dump_manifest(manifest: Manifest) -> str:
    """Serialize a manifest back to YAML text (the inverse of load_manifest).

    Only non-empty fields are written, and services stay in list order, so a
    round-trip through load→dump stays readable and diff-friendly. Used by the
    Ingestion UI when it appends or removes a repo."""
    services: list[dict[str, str]] = []
    for svc in manifest.services:
        entry: dict[str, str] = {"name": svc.name, "path": svc.path}
        if svc.language is not None:
            entry["language"] = svc.language.value
        for opt in ("art", "criticality", "owner", "git_url"):
            val = getattr(svc, opt)
            if val:
                entry[opt] = val
        services.append(entry)
    return yaml.safe_dump({"services": services}, sort_keys=False)


def save_manifest(manifest: Manifest, path: Path) -> None:
    """Validate then write a manifest to ``path`` (atomically enough for a UI)."""
    load_manifest(dump_manifest(manifest))  # fail loudly before touching disk
    path.write_text(dump_manifest(manifest), encoding="utf-8")
