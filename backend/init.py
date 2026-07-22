#!/usr/bin/env python3
"""Vectural initial setup — one guided flow through the three setup stages.

Runs, in order:

  1. Clone     — crawl a Git parent (org / group) and clone every repo into one
                 root (:mod:`backend.estate`).
  2. Manifest  — derive a reviewable ``manifest.yaml`` from the cloned directory
                 layout (:func:`backend.estimator.write_derived_manifest`).
  3. Estimate  — analyse token usage: embedding + gateway-billed summarisation
                 cost, with a weekly indexing plan (:mod:`backend.estimator`).

Interactive by default (prompts for the clone path and the Git parent URL); any
prompt can be pre-answered with a flag for non-interactive / CI use. Every stage
is idempotent, so re-running on an existing estate just refreshes it.

    vectural-init                                   # fully interactive
    vectural-init --path ./estate --parent https://github.com/acme --source-only
    vectural-init --path ./estate --skip-clone      # estate already on disk
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from backend.estate import clone_estate, parse_parent
from backend.estimator import (
    EstimatorConfig,
    estimate_estate,
    format_report,
    parse_suffixes,
    resolve_manifest,
    write_derived_manifest,
)


def _prompt(label: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default else ""
    while True:
        value = input(f"{label}{suffix}: ").strip()
        if value:
            return value
        if default is not None:
            return default


def _stage(number: int, title: str) -> None:
    print(f"\n\033[1m── Stage {number}/3 · {title} ──\033[0m", file=sys.stderr)


# --------------------------------------------------------------------------- #
# Stages
# --------------------------------------------------------------------------- #


def run_clone(
    root: Path,
    parent_url: str,
    *,
    shallow: bool,
    include_archived: bool,
    dry_run: bool,
) -> None:
    """Stage 1 — enumerate + clone every repo under the Git parent."""
    parent = parse_parent(parent_url)
    print(
        f"Enumerating repositories under {parent.owner} on {parent.host} "
        f"({parent.provider})…",
        file=sys.stderr,
    )
    tally = clone_estate(
        root, parent,
        shallow=shallow, include_archived=include_archived, dry_run=dry_run,
        on_progress=lambda name, outcome: print(f"  {name:40} {outcome}", file=sys.stderr),
    )
    print(
        "Clone summary: " + ", ".join(f"{v} {k}" for k, v in sorted(tally.items())),
        file=sys.stderr,
    )


def run_manifest(root: Path) -> Path:
    """Stage 2 — derive a manifest from the directory layout, non-destructively.

    Returns the path of the authoritative ``manifest.yaml`` to estimate against
    (an existing one is left untouched; a refreshed draft is written beside it)."""
    written, was_draft = write_derived_manifest(root)
    if was_draft:
        print(
            f"manifest.yaml already present — wrote a refreshed draft to {written.name} "
            "(review/merge; the manifest is human-owned, §3.4).",
            file=sys.stderr,
        )
    else:
        print(
            f"Derived {written.name} — review it before indexing (the manifest is "
            "human-owned, §3.4).",
            file=sys.stderr,
        )
    return root / "manifest.yaml"


def run_estimate(root: Path, manifest_path: Path, config: EstimatorConfig) -> None:
    """Stage 3 — token-usage / index-cost estimate (zero gateway tokens spent)."""
    manifest, _ = resolve_manifest(root, manifest_path)
    estimate = estimate_estate(root, manifest, config)
    print(format_report(estimate))
    sys.stdout.flush()  # report → stdout, chatter → stderr; keep them ordered


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--path", help="root folder for the estate (prompted if omitted)")
    parser.add_argument("--parent", help="Git parent URL: org / group (prompted if omitted)")
    # Stage 1 options
    parser.add_argument("--shallow", action="store_true", help="shallow clone (--depth 1)")
    parser.add_argument(
        "--include-archived", action="store_true", help="also clone archived repos"
    )
    parser.add_argument(
        "--skip-clone", action="store_true",
        help="estate already on disk — skip stage 1, go straight to manifest + estimate",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="stage 1: list what would happen, clone nothing"
    )
    # Stage 3 options (index-cost estimate)
    parser.add_argument(
        "--source-only", action="store_true",
        help="count only recognised source languages (skip docs/config/data)",
    )
    parser.add_argument(
        "--exclude", default="",
        help="comma-separated extra file extensions to exclude, e.g. '.md,.json'",
    )
    parser.add_argument(
        "--exclude-glob", action="append", default=[],
        help="glob of files to exclude (repeatable), e.g. '*.min.js'",
    )
    parser.add_argument(
        "--monthly-budget", type=int, default=EstimatorConfig().monthly_budget,
        help="gateway token budget/month used to bin-pack the weekly plan",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    path = args.path or _prompt("Path for the estate")
    root = Path(path).expanduser().resolve()

    # Stage 1 — clone (unless the estate is already on disk).
    if not args.skip_clone:
        parent_url = args.parent or _prompt("Git parent URL (org / group)")
        _stage(1, "Cloning the estate")
        try:
            run_clone(
                root, parent_url,
                shallow=args.shallow, include_archived=args.include_archived,
                dry_run=args.dry_run,
            )
        except ValueError as exc:  # bad parent URL
            print(f"error: {exc}", file=sys.stderr)
            return 2
        except (RuntimeError, subprocess.CalledProcessError) as exc:  # enumeration/clone
            print(f"error: {exc}", file=sys.stderr)
            return 1
        if args.dry_run:
            print("\nDry run — stopping before manifest + estimate.", file=sys.stderr)
            return 0
    else:
        _stage(1, "Cloning the estate (skipped)")
        if not root.is_dir():
            print(f"error: --skip-clone but {root} is not a directory", file=sys.stderr)
            return 2

    # Stage 2 — manifest.
    _stage(2, "Deriving the manifest")
    try:
        manifest_path = run_manifest(root)
    except ValueError as exc:  # no service directories under root
        print(f"error: {exc}", file=sys.stderr)
        return 2

    # Stage 3 — token-usage estimate.
    _stage(3, "Analysing token usage")
    config = EstimatorConfig(
        source_only=args.source_only,
        exclude_suffixes=parse_suffixes(args.exclude),
        exclude_globs=tuple(args.exclude_glob),
        monthly_budget=args.monthly_budget,
    )
    run_estimate(root, manifest_path, config)

    _next_steps(root, manifest_path)
    return 0


def _next_steps(root: Path, manifest_path: Path) -> None:
    print(
        "\n\033[1mNext steps\033[0m\n"
        f"  1. Review the manifest:   {manifest_path}\n"
        f"  2. Ingest + index:        vectural-ingest {root} -m {manifest_path}\n"
        f"  3. Serve the API/UI:      VECTURAL_ESTATE_ROOT={root} uvicorn backend.asgi:app",
        file=sys.stderr,
    )


if __name__ == "__main__":
    raise SystemExit(main())
