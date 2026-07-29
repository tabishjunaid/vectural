"""IngestionService — the API-owned control surface for the Ingestion UI.

Owns the interactive per-repo lifecycle the playground exposes: add a repo by URL,
list repos with their index state, estimate token+cost before spending, and DROP a
repo's index. The deterministic-index and (model-selectable) summarise *jobs* — the
async background runner with pause/cancel + SSE progress — build on this core in a
following slice; this module is the synchronous, unit-testable foundation.

Reuses the existing primitives rather than reinventing: `clone_repo_url` for
add-by-URL, `estimate_repo` for the model-aware cost, and per-file/-service store
deletes for the drop cascade. Deliberately store-typed (not tied to Postgres) so it
runs against the in-memory backends in tests.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel

from backend.domain.manifest import (
    Manifest,
    ServiceManifest,
    load_manifest,
    save_manifest,
)
from backend.domain.models import NodeKind
from backend.estate import clone_repo_url
from backend.graph.store import GraphStore
from backend.ingestion.estimate import estimate_repo
from backend.persistence.file_ledger import FileLedgerRepo
from backend.retrieval.base import SearchBackend
from backend.summarise.store import SummaryStore

_CLONE_OK = frozenset({"cloned", "updated", "would clone", "would update"})


class IngestionError(RuntimeError):
    """A repo add / drop could not complete (bad URL, clone failure, unknown repo)."""


class RepoState(BaseModel):
    """One repo's identity + current index state, for the UI table."""

    service: str
    path: str
    git_url: str | None = None
    indexed: bool = False  # deterministic chunks present (searchable)
    chunks: int = 0
    summary_tier: int = 0  # 0 none · 1 files · 2 modules · 3 service summary
    phase: str = "idle"  # live job phase (idle until the runner slice lands)


@dataclass
class IngestionService:
    estate_root: Path
    manifest_path: Path
    search: SearchBackend
    graph: GraphStore
    file_ledger: FileLedgerRepo
    summaries: SummaryStore | None = None

    # -- read -------------------------------------------------------------- #

    def _load_manifest(self) -> Manifest:
        return load_manifest(self.manifest_path.read_text(encoding="utf-8"))

    def _indexed_services(self) -> dict[str, int]:
        """service -> indexed chunk-file count, from the search index."""
        counts: dict[str, int] = {}
        for svc, _path in self.search.indexed_files():
            counts[svc] = counts.get(svc, 0) + 1
        return counts

    def _summary_tier(self, service: str) -> int:
        if self.summaries is None:
            return 0
        if any(r.key == service for r in self.summaries.all(3)):
            return 3
        if any(r.key.startswith(f"{service}") for r in self.summaries.all(2)):
            return 2
        return 0

    def _repo_state(self, svc: ServiceManifest, indexed: dict[str, int], phase: str) -> RepoState:
        files = indexed.get(svc.name, 0)
        tier = self._summary_tier(svc.name)
        return RepoState(
            service=svc.name,
            path=svc.path,
            git_url=svc.git_url,
            indexed=files > 0,
            chunks=files,
            summary_tier=max(tier, 1 if files > 0 else 0),
            phase=phase,
        )

    def list_repos(self) -> list[RepoState]:
        indexed = self._indexed_services()
        return [
            self._repo_state(svc, indexed, phase="idle")
            for svc in self._load_manifest().services
        ]

    def estimate(self, service: str, *, model: str | None = None) -> dict[str, object]:
        return estimate_repo(self.estate_root, self._load_manifest(), service, model=model)

    # -- mutate ------------------------------------------------------------ #

    def add_repo(self, url: str) -> RepoState:
        """Clone a repo by URL into the estate and register it in the manifest."""
        url = url.strip()
        if not url:
            raise IngestionError("a Git URL is required")
        name, outcome = clone_repo_url(url, self.estate_root)
        if outcome not in _CLONE_OK:
            raise IngestionError(f"clone failed for {url!r}: {outcome}")

        manifest = self._load_manifest()
        existing = next((s for s in manifest.services if s.name == name), None)
        if existing is None:
            manifest.services.append(ServiceManifest(name=name, path=name, git_url=url))
            save_manifest(manifest, self.manifest_path)
        elif existing.git_url != url:
            # Remember the origin URL on a repo that was previously hand-authored.
            updated = existing.model_copy(update={"git_url": url})
            manifest = Manifest(
                services=[updated if s.name == name else s for s in manifest.services]
            )
            save_manifest(manifest, self.manifest_path)

        svc = next(s for s in self._load_manifest().services if s.name == name)
        return self._repo_state(svc, self._indexed_services(), phase="idle")

    def drop(self, service: str) -> dict[str, int]:
        """Drop a repo's INDEX (not its cloned source): chunks (OpenSearch), its
        File/Function graph nodes + the Service node (Neo4j), tier summaries and
        file-ledger rows (Postgres), and the manifest entry. Cross-service shared
        nodes (topics/capabilities) are intentionally left — safely removing those
        needs reference counting (out of scope). Fail-safe: a re-index rebuilds
        cleanly since every write is idempotent."""
        manifest = self._load_manifest()
        if not any(s.name == service for s in manifest.services):
            raise IngestionError(f"unknown repo {service!r}")

        paths = [p for (svc, p) in self.search.indexed_files() if svc == service]
        chunks = self.search.delete_service(service)
        for path in paths:
            self.graph.delete_file(path)
            self.file_ledger.delete(service, path)
        self.graph.delete_node(NodeKind.SERVICE, service)
        summaries = self.summaries.delete_service(service) if self.summaries is not None else 0

        remaining = [s for s in manifest.services if s.name != service]
        save_manifest(Manifest(services=remaining), self.manifest_path)
        return {"chunks": chunks, "files": len(paths), "summaries": summaries}
