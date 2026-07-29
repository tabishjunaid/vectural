"""IngestionService — the API-owned control surface for the Ingestion UI.

Owns the interactive per-repo lifecycle the playground exposes: add a repo by URL,
list repos with their index state, estimate token+cost before spending, DROP a
repo's index, and run the async background jobs — a deterministic index (free,
local, pausable) and a model-selectable tier-1-4 summarise (a local Ollama pick
runs on-device for $0) — with cooperative pause/cancel and SSE progress.

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

from backend.config import Settings
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
from backend.llm import catalog
from backend.llm.router import LLMRouter
from backend.orchestration.activities import IndexServiceActivities
from backend.orchestration.work import EstateWork
from backend.persistence.dead_letter import DeadLetterRepo
from backend.persistence.file_ledger import FileLedgerRepo
from backend.quota import QuotaAccountant, QuotaGovernor
from backend.retrieval.base import SearchBackend
from backend.summarise.driver import FileToSummarise
from backend.summarise.store import SummaryStore

_TERMINAL = frozenset({"done", "failed", "cancelled"})

_CLONE_OK = frozenset({"cloned", "updated", "would clone", "would update"})


def _looks_like_git_url(source: str) -> bool:
    """Whether an add-repo source is a Git URL to clone (vs. a local folder name)."""
    return "://" in source or source.startswith("git@") or source.endswith(".git")


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
    # Summarisation deps (opt-in tier-1-4 LLM job). When any is None the summarise
    # endpoint is disabled but everything else (index/estimate/drop) still works.
    router: LLMRouter | None = None
    governor: QuotaGovernor | None = None
    dead_letter: DeadLetterRepo | None = None
    accountant: QuotaAccountant | None = None
    settings: Settings | None = None
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

    def add_repo(self, source: str) -> RepoState:
        """Register a repo. ``source`` is either a **Git URL** (cloned into the
        estate) or the **name of a folder already sitting under the estate root**
        (a local project — nothing is cloned). The latter is how you add a project
        from your machine: drop its folder into the estate directory, then add it by
        name (and it's how you re-add a repo after Drop, since Drop keeps the
        source). The backend only sees the mounted estate, so a local project must
        live under the estate root."""
        source = source.strip()
        if not source:
            raise IngestionError("enter a Git URL or a local folder name")

        if _looks_like_git_url(source):
            name, outcome = clone_repo_url(source, self.estate_root)
            if outcome not in _CLONE_OK:
                raise IngestionError(f"clone failed for {source!r}: {outcome}")
            return self._register(name, git_url=source)

        # A local directory already under the estate root — register it, no clone.
        name = source.strip("/").split("/")[-1]
        if not (self.estate_root / name).is_dir():
            raise IngestionError(
                f"{source!r} is not a Git URL, and no folder {name!r} exists in the "
                "estate. Put the project folder inside the estate directory first, "
                "then add it by name."
            )
        return self._register(name, git_url=None)

    def _register(self, name: str, *, git_url: str | None) -> RepoState:
        """Add (or update) a service entry in the manifest and return its state."""
        manifest = self._load_manifest()
        existing = next((s for s in manifest.services if s.name == name), None)
        if existing is None:
            manifest.services.append(ServiceManifest(name=name, path=name, git_url=git_url))
            save_manifest(manifest, self.manifest_path)
        elif git_url and existing.git_url != git_url:
            # Remember the origin URL on a repo that was previously hand-authored.
            updated = existing.model_copy(update={"git_url": git_url})
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

        # Remove from the manifest FIRST, so a read-only estate (or any write error)
        # fails here before we touch the stores — no half-dropped state. The store
        # deletes are all idempotent, so a re-drop after any later hiccup finishes
        # cleanly.
        remaining = [s for s in manifest.services if s.name != service]
        save_manifest(Manifest(services=remaining), self.manifest_path)

        paths = [p for (svc, p) in self.search.indexed_files() if svc == service]
        chunks = self.search.delete_service(service)
        for path in paths:
            self.graph.delete_file(path)
            self.file_ledger.delete(service, path)
        self.graph.delete_node(NodeKind.SERVICE, service)
        summaries = self.summaries.delete_service(service) if self.summaries is not None else 0
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

    # -- jobs: LLM summarise (opt-in, model-selectable incl. local $0) ------ #

    def start_summarise(self, service: str, *, model: str | None = None) -> dict[str, object]:
        """Start the tier-1-4 LLM summaries for a repo on the chosen model. A local
        Ollama pick runs on-device ($0); a cloud pick spends real tokens (the
        estimate already showed the cost). Quota-gated via the governor."""
        if self.router is None or self.governor is None or self.dead_letter is None:
            raise IngestionError("summarisation is not available (no gateway wired)")
        svc = next((s for s in self._load_manifest().services if s.name == service), None)
        if svc is None:
            raise IngestionError(f"unknown repo {service!r}")
        existing = self._jobs.get(service)
        if existing is not None and existing.active:
            raise IngestionError(f"a job is already running for {service!r}")
        job = IngestionJob(service=service, kind="summarise", phase="summarising")
        job.thread = threading.Thread(
            target=self._run_summarise, args=(job, svc, model), daemon=True
        )
        self._jobs[service] = job
        job.thread.start()
        return job.snapshot()

    def _summarise_router(self, model: str | None) -> LLMRouter:
        """A router whose default tier IS the chosen model, so the tier drivers
        (which route at their default tier) run on it — local models included.
        Falls back to the shared serving router when no/unknown model is given."""
        assert self.router is not None
        entry = catalog.find(model) if model else None
        if entry is None or self.settings is None:
            return self.router
        from backend.llm.anthropic_client import AnthropicGatewayClient
        from backend.llm.openai_client import OpenAIGatewayClient

        s = self.settings
        client: object
        if entry.provider == "anthropic":
            client = AnthropicGatewayClient(
                haiku_model=entry.concrete, sonnet_model=entry.concrete,
                base_url=s.anthropic_base_url or None,
            )
        elif entry.provider == "ollama":
            client = OpenAIGatewayClient(
                base_url=s.ollama_base_url or None, api_key=s.ollama_api_key or "ollama",
                small_model=entry.concrete, large_model=entry.concrete,
            )
        else:
            client = OpenAIGatewayClient(
                base_url=s.openai_base_url or None,
                small_model=entry.concrete, large_model=entry.concrete,
            )
        sinks = [self.accountant] if self.accountant is not None else []
        return LLMRouter(client, sinks=sinks)  # type: ignore[arg-type]

    def _files_to_summarise(self, svc: ServiceManifest) -> list[FileToSummarise]:
        files: list[FileToSummarise] = []
        for wf in walk_estate(self.estate_root, Manifest(services=[svc])):
            try:
                content = wf.abs_path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue  # binary / unreadable — the walker mostly filters these already
            files.append(FileToSummarise(service=wf.service, path=wf.path, content=content))
        return files

    @staticmethod
    def _cost(tokens: int, model: str | None) -> float | None:
        price = catalog.price_of(model) if model else None
        if price is None:
            return None
        in_price, out_price = price
        return round(tokens / 1e6 * (in_price + out_price) / 2, 4)  # blended estimate

    def _run_summarise(self, job: IngestionJob, svc: ServiceManifest, model: str | None) -> None:
        try:
            if job.cancel.is_set():
                job.phase = "cancelled"
                return
            router = self._summarise_router(model)
            activities = IndexServiceActivities(
                work=EstateWork(by_service={}),  # structural already done by the index job
                search=self.search,
                graph=self.graph,
                router=router,
                governor=self.governor,  # type: ignore[arg-type]
                file_ledger=self.file_ledger,
                dead_letter=self.dead_letter,  # type: ignore[arg-type]
                summaries=self.summaries,
            )
            files = self._files_to_summarise(svc)
            job.files_total = len(files)
            report = activities.summarise_service(svc.name, files, datetime.now(UTC))
            job.files_done = len(files)
            job.tokens = report.tokens_spent
            job.cost_usd = self._cost(report.tokens_spent, model)
            job.phase = "done"
        except Exception as exc:
            job.phase = "failed"
            job.error = str(exc)
