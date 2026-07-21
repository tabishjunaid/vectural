"""Incremental reindex, cascade delete, rename carry-forward, reconcile (§5.9)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest

from backend.answer import AnswerService, RetrievalPlanner, SemanticAnswerCache
from backend.answer.models import AnswerMode
from backend.domain.manifest import Manifest
from backend.domain.models import NodeKind
from backend.embedding import HashingEmbedder
from backend.flows import FlowNarrativeService, InMemoryFlowStore, identify_flows
from backend.freshness import (
    ChangeStatus,
    FreshnessState,
    Reindexer,
    parse_name_status,
    reconcile,
    reindex,
)
from backend.graph import StructuralQueries, build_graph
from backend.graph.store import InMemoryGraphStore
from backend.llm import FakeGatewayClient, LLMRouter
from backend.persistence import InMemoryFileLedger
from backend.persistence.file_ledger import FileLedgerEntry, FileStatus
from backend.retrieval import InMemorySearchBackend
from backend.summarise.tiers import content_hash
from tests.conftest import AnswerEnv


@dataclass
class Rig:
    root: Path
    manifest: Manifest
    search: InMemorySearchBackend
    graph: InMemoryGraphStore
    ledger: InMemoryFileLedger
    reindexer: Reindexer
    flows: FlowNarrativeService


def _rig(estate: Path, manifest: Manifest) -> Rig:
    built = build_graph(estate, manifest, commit_sha="c1")
    embedder = HashingEmbedder()
    search = InMemorySearchBackend(embedder=embedder)
    search.index(built.chunks)
    graph = built.store()
    ledger = InMemoryFileLedger()
    reindexer = Reindexer(
        root=estate, manifest=manifest, search_backend=search, graph_store=graph,
        file_ledger=ledger, commit_sha="c2",
    )
    flows = FlowNarrativeService(store=InMemoryFlowStore(), router=LLMRouter(FakeGatewayClient()))
    flows.generate(identify_flows(graph, StructuralQueries(graph)))
    return Rig(estate, manifest, search, graph, ledger, reindexer, flows)


# --- diff parser ------------------------------------------------------------ #


def test_parse_name_status() -> None:
    changes = parse_name_status(
        "M\ta.py\nA\tb.py\nD\tc.py\nR096\told.py\tnew.py\nC075\tsrc.py\tcopy.py\nT\tx.py\n\n"
    )
    kinds = [(c.status, c.path, c.old_path) for c in changes]
    assert (ChangeStatus.MODIFIED, "a.py", None) in kinds
    assert (ChangeStatus.ADDED, "b.py", None) in kinds
    assert (ChangeStatus.DELETED, "c.py", None) in kinds
    assert (ChangeStatus.RENAMED, "new.py", "old.py") in kinds
    assert (ChangeStatus.RENAMED, "copy.py", "src.py") in kinds  # copy modelled as rename
    assert len(changes) == 5  # the unknown 'T' line is skipped


# --- reindex ---------------------------------------------------------------- #


def test_modified_file_is_rechunked(graph_estate: Path, graph_manifest: Manifest) -> None:
    rig = _rig(graph_estate, graph_manifest)
    (graph_estate / "payments/pay.py").write_text("def charge(a):\n    return newlogic(a)\n")
    result = reindex(parse_name_status("M\tpayments/pay.py"), rig.reindexer)
    assert "payments/pay.py" in result.reindexed
    assert ("payments", "payments/pay.py") in result.resummarise
    assert "payments" in result.affected_services


def test_deleted_file_cascades(graph_estate: Path, graph_manifest: Manifest) -> None:
    rig = _rig(graph_estate, graph_manifest)
    assert ("ledger", "ledger/ledger.py") in rig.search.indexed_files()

    reindex(parse_name_status("D\tledger/ledger.py"), rig.reindexer)

    assert ("ledger", "ledger/ledger.py") not in rig.search.indexed_files()
    assert "ledger/ledger.py" not in rig.graph.file_keys()
    # Function nodes for the file and their edges are gone (no dangling edges).
    fn_keys = {n.key for n in rig.graph.nodes(NodeKind.FUNCTION)}
    assert not any(k.startswith("ledger/ledger.py#") for k in fn_keys)


def test_added_file_is_indexed(graph_estate: Path, graph_manifest: Manifest) -> None:
    rig = _rig(graph_estate, graph_manifest)
    (graph_estate / "ledger/audit.py").write_text("def audit(x):\n    return log(x)\n")
    reindex(parse_name_status("A\tledger/audit.py"), rig.reindexer)
    assert ("ledger", "ledger/audit.py") in rig.search.indexed_files()


def test_unchanged_content_is_skipped(graph_estate: Path, graph_manifest: Manifest) -> None:
    rig = _rig(graph_estate, graph_manifest)
    path = "payments/pay.py"
    text = (graph_estate / path).read_text()
    rig.ledger.upsert(
        FileLedgerEntry(
            service="payments", path=path, content_hash=content_hash(text),
            prompt_version="file-v1", tier=1, status=FileStatus.SUMMARISED,
            updated_at=datetime.now(UTC),
        )
    )
    result = reindex(parse_name_status(f"M\t{path}"), rig.reindexer)
    assert path in result.skipped_unchanged
    assert path not in result.reindexed


def test_rename_carries_summary_forward_when_unchanged(
    graph_estate: Path, graph_manifest: Manifest
) -> None:
    rig = _rig(graph_estate, graph_manifest)
    old, new = "ledger/ledger.py", "ledger/core.py"
    text = (graph_estate / old).read_text()
    rig.ledger.upsert(
        FileLedgerEntry(
            service="ledger", path=old, content_hash=content_hash(text),
            prompt_version="file-v1", tier=1, status=FileStatus.SUMMARISED,
            updated_at=datetime.now(UTC),
        )
    )
    (graph_estate / new).write_text(text)  # same content at the new path
    (graph_estate / old).unlink()

    result = reindex(parse_name_status(f"R100\t{old}\t{new}"), rig.reindexer)
    assert new in result.carried_forward
    assert (rig.ledger.get("ledger", new) or FileLedgerEntry.model_construct()).status is (
        FileStatus.SUMMARISED
    )
    assert ("ledger", new) not in result.resummarise  # no re-spend on a pure move


def test_flows_flagged_and_services_marked_stale(
    graph_estate: Path, graph_manifest: Manifest
) -> None:
    rig = _rig(graph_estate, graph_manifest)
    gwflow = next(f for f in rig.flows.queue() if "gateway" in f.services)
    rig.flows.approve(gwflow.id, "A. Chen")

    (graph_estate / "payments/pay.py").write_text("def charge(a):\n    return 1\n")
    freshness = FreshnessState()
    result = reindex(
        parse_name_status("M\tpayments/pay.py"), rig.reindexer, flows=rig.flows, freshness=freshness
    )
    assert gwflow.id in result.flows_flagged  # touches payments -> needs_review
    assert rig.flows.get(gwflow.id).status.value == "needs_review"
    assert freshness.is_stale("payments")


# --- reconcile -------------------------------------------------------------- #


def test_reconcile_deletes_orphans(graph_estate: Path, graph_manifest: Manifest) -> None:
    rig = _rig(graph_estate, graph_manifest)
    # A file present in the index but removed from the tree becomes an orphan.
    (graph_estate / "notifications/notify.py").unlink()
    report = reconcile(
        graph_estate, graph_manifest, search_backend=rig.search,
        graph_store=rig.graph, file_ledger=rig.ledger,
    )
    assert "notifications/notify.py" in report.orphan_files
    assert ("notifications", "notifications/notify.py") not in rig.search.indexed_files()


def test_reconcile_noop_when_in_sync(graph_estate: Path, graph_manifest: Manifest) -> None:
    rig = _rig(graph_estate, graph_manifest)
    report = reconcile(
        graph_estate, graph_manifest, search_backend=rig.search,
        graph_store=rig.graph, file_ledger=rig.ledger,
    )
    assert report.orphan_files == []


# --- staleness on the answer path ------------------------------------------- #


def test_answer_flagged_stale_during_reindex(answer_env: AnswerEnv) -> None:
    freshness = FreshnessState()
    freshness.mark_stale({"gateway", "payments", "ledger"})
    router = LLMRouter(FakeGatewayClient())
    planner = RetrievalPlanner(answer_env.structural, router, answer_env.services)
    service = AnswerService(
        retrieval=answer_env.retrieval, planner=planner, router=router,
        cache=SemanticAnswerCache(answer_env.embedder), freshness=freshness,
        commit_sha=answer_env.commit_sha,
    )
    answer = service.answer("how does gateway charge via payments and ledger")
    if answer.mode is AnswerMode.SYNTHESIZED:
        assert answer.stale  # served, but visibly flagged (§4.4)


def test_answer_not_stale_without_reindex(answer_env: AnswerEnv) -> None:
    router = LLMRouter(FakeGatewayClient())
    planner = RetrievalPlanner(answer_env.structural, router, answer_env.services)
    service = AnswerService(
        retrieval=answer_env.retrieval, planner=planner, router=router,
        freshness=FreshnessState(), commit_sha=answer_env.commit_sha,
    )
    answer = service.answer("how does gateway charge via payments and ledger")
    assert not answer.stale


@pytest.mark.parametrize("bad", ["", "   ", "\n\n"])
def test_parse_empty_diff(bad: str) -> None:
    assert parse_name_status(bad) == []
