"""Manifest-driven estate walking (§5.1 / §3.4)."""

from __future__ import annotations

from pathlib import Path

from backend.domain.manifest import Manifest
from backend.ingestion.walker import walk_estate


def _walked_paths(estate: Path, manifest: Manifest) -> set[str]:
    return {w.path for w in walk_estate(estate, manifest)}


def test_walks_only_manifested_services(estate: Path, manifest: Manifest) -> None:
    paths = _walked_paths(estate, manifest)
    assert "payments-api/refund.py" in paths
    assert "ledger-svc/index.ts" in paths
    assert "booking-api/src/RefundHandler.java" in paths
    # Unmanifested service directory is never walked.
    assert not any(p.startswith("unmanifested-svc/") for p in paths)


def test_prunes_ignored_dirs_and_binaries(estate: Path, manifest: Manifest) -> None:
    paths = _walked_paths(estate, manifest)
    assert not any("node_modules" in p for p in paths)  # pruned dir
    assert "payments-api/logo.png" not in paths  # binary sniff


def test_attribution_and_uniqueness(estate: Path, manifest: Manifest) -> None:
    walked = list(walk_estate(estate, manifest))
    # Every file attributed to exactly one service, emitted once.
    assert len(walked) == len({w.path for w in walked})
    services = {w.service for w in walked}
    assert services == {"payments-api", "ledger-svc", "booking-api"}


def test_oversized_file_skipped(estate: Path, manifest: Manifest) -> None:
    big = estate / "payments-api" / "huge.py"
    big.write_bytes(b"x = 1\n" * 300_000)  # > default 1.5 MB
    paths = _walked_paths(estate, manifest)
    assert "payments-api/huge.py" not in paths
