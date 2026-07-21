"""``vectural-ingest`` — run the Phase 1 deterministic ingestion over an estate.

Prints a coverage-style summary (services, files, chunks, graph nodes/edges).
Spends zero gateway tokens by construction — this command never imports a model
client. Use it as the Phase 1 exit check: "every source file chunked, embedded,
and searchable" starts with "every source file chunked", which this proves.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from backend.domain.manifest import ManifestError, load_manifest
from backend.ingestion.pipeline import ingest_tree


def _resolve_commit_sha(root: Path) -> str:
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
        return out.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "WORKING"  # not a git tree (or no git); still a valid, honest marker


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="vectural-ingest", description=__doc__)
    parser.add_argument("root", type=Path, help="estate root containing the repositories")
    parser.add_argument(
        "-m",
        "--manifest",
        type=Path,
        default=None,
        help="path to manifest.yaml (default: <root>/manifest.yaml)",
    )
    parser.add_argument("--json", action="store_true", help="emit the summary as JSON")
    parser.add_argument(
        "--sample-chunks",
        type=int,
        default=0,
        metavar="N",
        help="also print the first N chunk ids (sanity check)",
    )
    args = parser.parse_args(argv)

    root: Path = args.root
    manifest_path: Path = args.manifest or (root / "manifest.yaml")

    if not root.is_dir():
        parser.error(f"root {root} is not a directory")
    if not manifest_path.is_file():
        parser.error(f"manifest {manifest_path} not found")

    try:
        manifest = load_manifest(manifest_path.read_text(encoding="utf-8"))
    except ManifestError as exc:
        print(f"manifest error: {exc}", file=sys.stderr)
        return 2

    commit_sha = _resolve_commit_sha(root)
    result = ingest_tree(root, manifest, commit_sha=commit_sha)

    summary = {
        "commit_sha": commit_sha,
        "services": len(manifest.services),
        "files_ingested": result.file_count - len(manifest.services),
        "parse_errors": result.parse_error_count,
        "chunks": len(result.chunks),
        "graph_nodes": len(result.nodes),
        "graph_edges": len(result.edges),
        "nodes_by_kind": result.counts_by_kind(),
    }

    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        _print_summary(summary)

    if args.sample_chunks > 0:
        print("\nsample chunks:")
        for chunk in result.chunks[: args.sample_chunks]:
            sym = chunk.symbol or "—"
            print(f"  {chunk.kind.value:8} {chunk.language.value:11} {sym:24} {chunk.chunk_id}")

    return 0


def _print_summary(summary: dict[str, object]) -> None:
    print("Vectural ingestion summary")
    print(f"  commit         {summary['commit_sha']}")
    print(f"  services       {summary['services']}")
    print(f"  files ingested {summary['files_ingested']}")
    print(f"  parse errors   {summary['parse_errors']}")
    print(f"  chunks         {summary['chunks']}")
    print(f"  graph nodes    {summary['graph_nodes']}")
    print(f"  graph edges    {summary['graph_edges']}")
    by_kind = summary["nodes_by_kind"]
    assert isinstance(by_kind, dict)
    for kind, count in sorted(by_kind.items()):
        print(f"    {kind:10} {count}")


if __name__ == "__main__":
    raise SystemExit(main())
