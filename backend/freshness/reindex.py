"""Incremental reindex from a git diff (§5.9, §4.4).

Applies a set of :class:`FileChange` to the index + graph, honouring the three
change kinds and the cascade rules. Produces a :class:`ReindexResult` describing
what changed — the affected services, the files whose summaries are now stale
(to be re-summarised by the Phase 5 driver), and the flow narratives flagged
``needs_review`` (never regenerated here — §4.4).

Typed against the in-memory backend/graph for the offline path; the production
adapters delete via OpenSearch delete-by-query and Neo4j's cascading detach.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from backend.domain.manifest import Manifest
from backend.freshness.diff import ChangeStatus, FileChange
from backend.freshness.state import FreshnessState
from backend.graph.store import InMemoryGraphStore
from backend.ingestion.pipeline import ingest_file
from backend.persistence.file_ledger import FileLedgerEntry, FileLedgerRepo, FileStatus
from backend.retrieval.inmemory import InMemorySearchBackend
from backend.summarise.tiers import TIER1_PROMPT_VERSION, content_hash


@dataclass
class ReindexResult:
    reindexed: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    renamed: list[str] = field(default_factory=list)
    skipped_unchanged: list[str] = field(default_factory=list)
    skipped_unmanifested: list[str] = field(default_factory=list)
    carried_forward: list[str] = field(default_factory=list)
    resummarise: list[tuple[str, str]] = field(default_factory=list)
    affected_services: set[str] = field(default_factory=set)
    flows_flagged: list[str] = field(default_factory=list)


class _FlowInvalidator(Protocol):  # FlowNarrativeService satisfies this structurally
    def invalidate_on_change(self, changed_services: set[str]) -> list[str]: ...


@dataclass
class Reindexer:
    root: Path
    manifest: Manifest
    search_backend: InMemorySearchBackend
    graph_store: InMemoryGraphStore
    file_ledger: FileLedgerRepo
    commit_sha: str
    prompt_version: str = TIER1_PROMPT_VERSION

    def upsert_file(
        self, path: str, result: ReindexResult, *, carry_entry: FileLedgerEntry | None = None
    ) -> None:
        owner = self.manifest.service_for_path(path)
        if owner is None:
            result.skipped_unmanifested.append(path)
            return
        abs_path = self.root / path
        if not abs_path.is_file():  # modified→gone: treat as a delete
            self.delete_file(path, result)
            return

        source = abs_path.read_bytes()
        new_hash = content_hash(source.decode("utf-8", errors="replace"))
        existing = self.file_ledger.get(owner.name, path)
        already_indexed = (owner.name, path) in self.search_backend.indexed_files()
        if existing is not None and existing.content_hash == new_hash and already_indexed:
            result.skipped_unchanged.append(path)  # unchanged content — skip re-embed (§5.9)
            return

        # Replace the file's chunks + graph nodes (delete-then-add).
        self.search_backend.delete_by_file(owner.name, path)
        self.graph_store.delete_file(path)
        ingested = ingest_file(
            source=source, service=owner.name, path=path,
            commit_sha=self.commit_sha, indexed_at=datetime.now(UTC),
        )
        self.search_backend.index(ingested.chunks)
        for node in ingested.delta.nodes:
            self.graph_store.add_node(node)
        for edge in ingested.delta.edges:
            self.graph_store.add_edge(edge)
        result.affected_services.add(owner.name)
        result.reindexed.append(path)

        if _can_carry(carry_entry, new_hash):
            assert carry_entry is not None
            self.file_ledger.upsert(
                carry_entry.model_copy(
                    update={"service": owner.name, "path": path, "updated_at": datetime.now(UTC)}
                )
            )
            result.carried_forward.append(path)
        else:
            # Summary is now stale — mark it for re-summarisation (§5.9 upward cascade).
            self.file_ledger.upsert(
                FileLedgerEntry(
                    service=owner.name, path=path, content_hash=new_hash,
                    prompt_version=self.prompt_version, tier=0,
                    status=FileStatus.PENDING, updated_at=datetime.now(UTC),
                )
            )
            result.resummarise.append((owner.name, path))

    def delete_file(self, path: str, result: ReindexResult) -> None:
        owner = self.manifest.service_for_path(path)
        if owner is not None:
            self.search_backend.delete_by_file(owner.name, path)
            self.file_ledger.delete(owner.name, path)
            result.affected_services.add(owner.name)
        self.graph_store.delete_file(path)
        if path not in result.deleted:
            result.deleted.append(path)

    def rename_file(self, old_path: str, new_path: str, result: ReindexResult) -> None:
        # Capture the old summary *before* the delete removes its ledger row.
        old_owner = self.manifest.service_for_path(old_path)
        carry = self.file_ledger.get(old_owner.name, old_path) if old_owner else None
        self.delete_file(old_path, result)
        self.upsert_file(new_path, result, carry_entry=carry)
        result.renamed.append(f"{old_path} -> {new_path}")


def _can_carry(entry: FileLedgerEntry | None, new_hash: str) -> bool:
    return (
        entry is not None
        and entry.status is FileStatus.SUMMARISED
        and entry.content_hash == new_hash
    )


def reindex(
    changes: list[FileChange],
    reindexer: Reindexer,
    *,
    flows: _FlowInvalidator | None = None,
    freshness: FreshnessState | None = None,
) -> ReindexResult:
    """Apply a diff. Flow narratives touching a changed service are flagged
    ``needs_review`` (never regenerated); affected services are marked stale."""
    result = ReindexResult()
    for change in changes:
        if change.status in (ChangeStatus.ADDED, ChangeStatus.MODIFIED):
            reindexer.upsert_file(change.path, result)
        elif change.status is ChangeStatus.DELETED:
            reindexer.delete_file(change.path, result)
        elif change.status is ChangeStatus.RENAMED and change.old_path is not None:
            reindexer.rename_file(change.old_path, change.path, result)

    if freshness is not None:
        freshness.mark_stale(result.affected_services)
    if flows is not None and result.affected_services:
        result.flows_flagged = flows.invalidate_on_change(result.affected_services)
    return result
