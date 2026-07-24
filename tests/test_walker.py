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
    big.write_bytes(b"x = 1\n" * 300_000)  # 1.8 MB, far over the cap
    paths = _walked_paths(estate, manifest)
    assert "payments-api/huge.py" not in paths


def test_file_too_large_for_the_summariser_context_is_skipped(
    estate: Path, manifest: Manifest
) -> None:
    """The regression: a 400 KB generated file passed the old 1.5 MB cap but rendered a
    ~130k-token tier-1 prompt, over the 128k context, and the provider's permanent 400
    aborted the whole indexing run. The walk bound now tracks the context window."""
    generated = estate / "payments-api" / "generated.json"
    generated.write_bytes(b'{"k":"v"},' * 40_000)  # ~400 KB of dense JSON
    assert "payments-api/generated.json" not in _walked_paths(estate, manifest)


def test_skips_ignore_lists_and_legal_boilerplate(estate: Path, manifest: Manifest) -> None:
    """A .gitignore is path fragments that match many queries lexically while being
    unable to answer any of them — it once out-ranked real source for "main modules
    of vectural"."""
    svc = estate / "payments-api"
    (svc / ".gitignore").write_text("*.pyc\nnode_modules/\n")
    (svc / ".dockerignore").write_text(".git\n")
    (svc / "LICENSE").write_text("MIT License ...")

    paths = _walked_paths(estate, manifest)
    for noise in (".gitignore", ".dockerignore", "LICENSE"):
        assert f"payments-api/{noise}" not in paths


def test_skips_generated_lock_files(estate: Path, manifest: Manifest) -> None:
    """Machine-written dependency pins — never authored, never asked about."""
    svc = estate / "payments-api"
    (svc / "uv.lock").write_text("# locked\n")
    (svc / "package-lock.json").write_text("{}")
    (svc / "Cargo.lock").write_text("[[package]]")

    paths = _walked_paths(estate, manifest)
    assert not any(p.endswith(("uv.lock", "package-lock.json", "Cargo.lock")) for p in paths)


def test_skips_assets_but_keeps_real_config(estate: Path, manifest: Manifest) -> None:
    """The exclusions must be surgical: package.json and tsconfig.json describe the
    service and stay, while the logo next to them goes."""
    svc = estate / "payments-api"
    (svc / "logo.svg").write_text("<svg></svg>")
    (svc / "bundle.min.js").write_text("!function(){}()")
    (svc / "package.json").write_text('{"name": "payments"}')
    (svc / "tsconfig.json").write_text("{}")

    paths = _walked_paths(estate, manifest)
    assert "payments-api/logo.svg" not in paths
    assert "payments-api/bundle.min.js" not in paths
    assert "payments-api/package.json" in paths       # real config is kept
    assert "payments-api/tsconfig.json" in paths


def test_noise_matching_is_case_insensitive(estate: Path, manifest: Manifest) -> None:
    svc = estate / "payments-api"
    (svc / "License").write_text("MIT")
    (svc / "Cargo.lock").write_text("[[package]]")
    paths = _walked_paths(estate, manifest)
    assert "payments-api/License" not in paths
    assert "payments-api/Cargo.lock" not in paths


def test_prunes_generated_graphify_output(estate: Path, manifest: Manifest) -> None:
    """``graphify-out/`` holds a tool's own emitted graph, not source worth indexing."""
    out = estate / "payments-api" / "graphify-out"
    out.mkdir()
    (out / "graph.json").write_text('{"nodes": []}')
    (out / "GRAPH_REPORT.md").write_text("# report")

    paths = _walked_paths(estate, manifest)
    assert not any("graphify-out" in p for p in paths)
