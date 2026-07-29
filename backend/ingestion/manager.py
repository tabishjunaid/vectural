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

import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
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
from backend.graph.builder import _service_node
from backend.graph.store import GraphStore
from backend.ingestion.estimate import estimate_repo
from backend.ingestion.pipeline import ingest_file
from backend.ingestion.walker import walk_estate
from backend.persistence.file_ledger import FileLedgerRepo
from backend.retrieval.base import SearchBackend
from backend.summarise.store import SummaryStore

_TERMINAL = frozenset({"done", "failed", "cancelled"})

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


class _Cancelled(Exception):
    """Internal signal: a job was cancelled at a control checkpoint."""


@dataclass
class IngestionJob:
    """A running (or finished) index/summarise job for one repo. The worker thread
    mutates the progress fields in place; the SSE endpoint polls ``snapshot()``.
    Control is cooperative: ``pause``/``cancel`` are checked between files/tiers."""

    service: str
    kind: str  # "index" | "summarise"
    phase: str = "indexing"  # indexing | summarising | done | failed | cancelled
    files_done: int = 0
    files_total: int = 0
    chunks: int = 0
    tokens: int = 0
    cost_usd: float | None = None
    error: str | None = None
    pause: threading.Event = field(default_factory=threading.Event)  # SET = paused
    cancel: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None

    @property
    def active(self) -> bool:
        return self.phase not in _TERMINAL

    def snapshot(self) -> dict[str, object]:
        return {
            "service": self.service,
            "kind": self.kind,
            "phase": self.phase,
            "paused": self.pause.is_set(),
            "files_done": self.files_done,
            "files_total": self.files_total,
            "chunks": self.chunks,
            "tokens": self.tokens,
            "cost_usd": self.cost_usd,
            "error": self.error,
        }


@dataclass
class IngestionService:
    estate_root: Path
    manifest_path: Path
    search: SearchBackend
    graph: GraphStore
    file_ledger: FileLedgerRepo
    summaries: SummaryStore | None = None
    commit_sha: str = "INGEST"
    _jobs: dict[str, IngestionJob] = field(default_factory=dict)

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

    def _phase_of(self, service: str) -> str:
        job = self._jobs.get(service)
        if job is None or not job.active:
            return "idle"
        return "paused" if job.pause.is_set() else job.phase

    def list_repos(self) -> list[RepoState]:
        indexed = self._indexed_services()
        return [
            self._repo_state(svc, indexed, phase=self._phase_of(svc.name))
            for svc in self._load_manifest().services
        ]

    def job_snapshot(self, service: str) -> dict[str, object] | None:
        job = self._jobs.get(service)
        return job.snapshot() if job is not None else None

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

    # -- jobs: deterministic index (async, pausable) ----------------------- #

    def start_index(self, service: str) -> dict[str, object]:
        """Start the deterministic index for a repo in a background thread: walk →
        chunk → local embed (OpenSearch) → graph. Free/local, no gateway. Idempotent
        (already-indexed files are re-written harmlessly), so a cancel leaves a
        clean, re-runnable state."""
        svc = next((s for s in self._load_manifest().services if s.name == service), None)
        if svc is None:
            raise IngestionError(f"unknown repo {service!r}")
        existing = self._jobs.get(service)
        if existing is not None and existing.active:
            raise IngestionError(f"a job is already running for {service!r}")
        job = IngestionJob(service=service, kind="index", phase="indexing")
        job.thread = threading.Thread(target=self._run_index, args=(job, svc), daemon=True)
        self._jobs[service] = job
        job.thread.start()
        return job.snapshot()

    def pause(self, service: str) -> dict[str, object]:
        self._active_job(service).pause.set()
        return self._active_job(service).snapshot()

    def resume(self, service: str) -> dict[str, object]:
        self._active_job(service).pause.clear()
        return self._active_job(service).snapshot()

    def cancel(self, service: str) -> dict[str, object]:
        job = self._active_job(service)
        job.cancel.set()
        job.pause.clear()  # unblock a paused worker so it can observe the cancel
        return job.snapshot()

    def _active_job(self, service: str) -> IngestionJob:
        job = self._jobs.get(service)
        if job is None or not job.active:
            raise IngestionError(f"no running job for {service!r}")
        return job

    @staticmethod
    def _await_control(job: IngestionJob) -> None:
        """Cooperative checkpoint: block while paused, raise on cancel."""
        while job.pause.is_set():
            if job.cancel.is_set():
                raise _Cancelled
            time.sleep(0.1)
        if job.cancel.is_set():
            raise _Cancelled

    def _run_index(self, job: IngestionJob, svc: ServiceManifest) -> None:
        try:
            now = datetime.now(UTC)
            self.graph.upsert_nodes([_service_node(job.service, self.commit_sha, now)])
            walked = list(walk_estate(self.estate_root, Manifest(services=[svc])))
            job.files_total = len(walked)
            for i, wf in enumerate(walked):
                self._await_control(job)
                result = ingest_file(
                    source=wf.abs_path.read_bytes(),
                    service=wf.service,
                    path=wf.path,
                    commit_sha=self.commit_sha,
                    indexed_at=now,
                )
                self.search.index(result.chunks)
                self.graph.upsert_nodes(result.delta.nodes)
                self.graph.upsert_edges(result.delta.edges)
                job.files_done = i + 1
                job.chunks += len(result.chunks)
            job.phase = "done"
        except _Cancelled:
            job.phase = "cancelled"
        except Exception as exc:  # a worker thread must never crash silently
            job.phase = "failed"
            job.error = str(exc)
