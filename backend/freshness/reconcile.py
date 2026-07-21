"""Reconciliation sweep (§5.9 backstop).

Compares the git tree (what the manifest-driven walk currently sees) against the
index, and deletes **orphans** — anything indexed that no longer exists in the
tree. This is the safety net for missed webhooks: even if an incremental reindex
was dropped, the periodic sweep eventually converges the index back to the tree
(R3: no orphaned data after a full delete cycle).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from backend.domain.manifest import Manifest
from backend.graph.store import InMemoryGraphStore
from backend.ingestion.walker import walk_estate
from backend.persistence.file_ledger import FileLedgerRepo
from backend.retrieval.inmemory import InMemorySearchBackend


@dataclass
class ReconcileReport:
    orphan_files: list[str] = field(default_factory=list)  # (service, path) collapsed to path
    chunks_deleted: int = 0
    nodes_deleted: int = 0


def reconcile(
    root: Path,
    manifest: Manifest,
    *,
    search_backend: InMemorySearchBackend,
    graph_store: InMemoryGraphStore,
    file_ledger: FileLedgerRepo,
) -> ReconcileReport:
    """Delete index entries whose files are no longer in the git tree."""
    on_disk = {(w.service, w.path) for w in walk_estate(root, manifest)}
    report = ReconcileReport()

    for service, path in sorted(search_backend.indexed_files()):
        if (service, path) in on_disk:
            continue
        report.chunks_deleted += search_backend.delete_by_file(service, path)
        report.nodes_deleted += graph_store.delete_file(path)
        file_ledger.delete(service, path)
        report.orphan_files.append(path)

    # A File node with no chunks (e.g. graph-only orphan) is also swept.
    on_disk_paths = {path for _, path in on_disk}
    for path in sorted(graph_store.file_keys() - on_disk_paths):
        report.nodes_deleted += graph_store.delete_file(path)
        if path not in report.orphan_files:
            report.orphan_files.append(path)

    return report
