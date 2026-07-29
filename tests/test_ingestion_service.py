"""IngestionService synchronous core: list repos, and the DROP cascade removing
exactly one service's index (chunks + graph + summaries + manifest entry)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from backend.domain.manifest import Manifest, ServiceManifest, save_manifest
from backend.domain.models import NodeKind
from backend.embedding.hashing import HashingEmbedder
from backend.graph.store import InMemoryGraphStore
from backend.ingestion.manager import IngestionError, IngestionService
from backend.ingestion.pipeline import ingest_tree
from backend.persistence.file_ledger import InMemoryFileLedger
from backend.retrieval.inmemory import InMemorySearchBackend
from backend.summarise.store import InMemorySummaryStore, SummaryRecord


def _svc_summary(service: str) -> SummaryRecord:
    return SummaryRecord(
        tier=3, kind="service", key=service, text=f"{service} does things",
        content_hash="h", prompt_version="v", updated_at=datetime.now(UTC),
    )


def _seed(tmp_path: Path) -> IngestionService:
    for name in ("alpha", "beta"):
        d = tmp_path / name
        d.mkdir()
        (d / "main.py").write_text(f"def {name}():\n    return 1\n")
    manifest = Manifest(
        services=[
            ServiceManifest(name="alpha", path="alpha"),
            ServiceManifest(name="beta", path="beta"),
        ]
    )
    manifest_path = tmp_path / "manifest.yaml"
    save_manifest(manifest, manifest_path)

    result = ingest_tree(tmp_path, manifest, commit_sha="TEST")
    search = InMemorySearchBackend(embedder=HashingEmbedder())
    search.index(result.chunks)
    graph = InMemoryGraphStore.from_graph(result.nodes, result.edges)
    summaries = InMemorySummaryStore()
    summaries.upsert(_svc_summary("alpha"))
    summaries.upsert(_svc_summary("beta"))
    return IngestionService(
        estate_root=tmp_path, manifest_path=manifest_path,
        search=search, graph=graph, file_ledger=InMemoryFileLedger(), summaries=summaries,
    )


def test_list_repos_reports_indexed_state(tmp_path: Path) -> None:
    svc = _seed(tmp_path)
    repos = {r.service: r for r in svc.list_repos()}
    assert set(repos) == {"alpha", "beta"}
    assert repos["alpha"].indexed and repos["alpha"].chunks > 0
    assert repos["alpha"].summary_tier == 3


def test_drop_removes_exactly_one_service(tmp_path: Path) -> None:
    svc = _seed(tmp_path)
    assert svc.graph.has_node(NodeKind.SERVICE, "alpha")

    out = svc.drop("alpha")
    assert out["chunks"] > 0

    # alpha is gone everywhere...
    assert not any(s == "alpha" for s, _ in svc.search.indexed_files())
    assert not svc.graph.has_node(NodeKind.SERVICE, "alpha")
    assert svc.summaries is not None
    assert not any(r.key == "alpha" for r in svc.summaries.all(3))
    assert "alpha" not in {s.service for s in svc.list_repos()}

    # ...and beta is untouched.
    assert any(s == "beta" for s, _ in svc.search.indexed_files())
    assert svc.graph.has_node(NodeKind.SERVICE, "beta")
    assert any(r.key == "beta" for r in svc.summaries.all(3))


def test_drop_unknown_repo_raises(tmp_path: Path) -> None:
    svc = _seed(tmp_path)
    try:
        svc.drop("nope")
    except IngestionError:
        return
    raise AssertionError("expected IngestionError")


def _wait_terminal(svc: IngestionService, service: str, timeout: float = 10.0) -> dict:
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snap = svc.job_snapshot(service)
        assert snap is not None
        if snap["phase"] in ("done", "failed", "cancelled"):
            return snap
        time.sleep(0.02)
    raise AssertionError(f"job for {service} did not finish in {timeout}s")


def test_start_index_makes_a_fresh_repo_searchable(tmp_path: Path) -> None:
    (tmp_path / "gamma").mkdir()
    (tmp_path / "gamma" / "m.py").write_text("def g():\n    return 1\n")
    manifest = Manifest(services=[ServiceManifest(name="gamma", path="gamma")])
    save_manifest(manifest, tmp_path / "manifest.yaml")

    search = InMemorySearchBackend(embedder=HashingEmbedder())
    graph = InMemoryGraphStore.from_graph([], [])
    svc = IngestionService(
        estate_root=tmp_path, manifest_path=tmp_path / "manifest.yaml",
        search=search, graph=graph, file_ledger=InMemoryFileLedger(),
        summaries=InMemorySummaryStore(),
    )
    assert not any(s == "gamma" for s, _ in search.indexed_files())

    started = svc.start_index("gamma")
    assert started["kind"] == "index"
    final = _wait_terminal(svc, "gamma")
    assert final["phase"] == "done" and final["files_done"] >= 1 and final["chunks"] >= 1
    # It is now searchable, and the graph carries its Service node.
    assert any(s == "gamma" for s, _ in search.indexed_files())
    assert graph.has_node(NodeKind.SERVICE, "gamma")
    # list_repos reflects it back to idle once the job is terminal.
    assert next(r for r in svc.list_repos() if r.service == "gamma").phase == "idle"


def test_control_on_no_running_job_raises(tmp_path: Path) -> None:
    svc = _seed(tmp_path)
    for op in (svc.pause, svc.resume, svc.cancel):
        try:
            op("alpha")
        except IngestionError:
            continue
        raise AssertionError(f"{op.__name__} should raise when no job is running")
