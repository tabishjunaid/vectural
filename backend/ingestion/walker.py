"""Repo walker (§5.1: walk repos per manifest.yaml).

Walks only what the manifest sanctions: a file under no manifested service is
never yielded, upholding the design-doc §3.4 guarantee that an unmanifested
directory "does not silently appear in the graph". Each file is attributed to
its **most specific** owning service, so nested services resolve correctly and
each file is emitted exactly once.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from backend.domain.manifest import Manifest

# Directories never worth indexing — build output, dependency caches, VCS
# internals. Pruned during the walk so we never descend into a node_modules tree.
DEFAULT_IGNORE_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        "dist",
        "build",
        "out",
        "target",
        "bin",
        "obj",
        "vendor",
        "__pycache__",
        ".venv",
        "venv",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".gradle",
        ".idea",
        ".vscode",
        "coverage",
        ".next",
        ".turbo",
        "graphify-out",  # graphify's own output (graph.json/.html, GRAPH_REPORT.md)
    }
)

# Files that are text, and small enough to pass every other check, but carry no
# answerable meaning about the estate. Excluded by exact name (case-insensitive).
#
# This is a retrieval-quality lever, not just a cost one: a `.gitignore` is mostly
# path fragments that happen to match many queries lexically, so it out-ranks real
# source for questions it cannot possibly answer. Generated dependency manifests
# (lock files) are the same problem an order of magnitude larger — `uv.lock` alone
# is 178 KB (~60k tokens) of transitive pins that no one will ever ask about.
DEFAULT_IGNORE_FILENAMES: frozenset[str] = frozenset(
    {
        # VCS / tooling ignore lists — path fragments, no semantics.
        ".gitignore",
        ".gitattributes",
        ".gitmodules",
        ".dockerignore",
        ".npmignore",
        ".eslintignore",
        ".prettierignore",
        ".editorconfig",
        # Generated dependency lock files — machine-written, never authored.
        "package-lock.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "poetry.lock",
        "uv.lock",
        "cargo.lock",
        "gemfile.lock",
        "composer.lock",
        "go.sum",
        # IDE / build-tool metadata and generated wrapper scripts.
        ".classpath",
        ".project",
        "gradlew",
        "gradlew.bat",
        "mvnw",
        "mvnw.cmd",
        # Legal boilerplate — identical across repos, answers nothing about the code.
        "license",
        "license.txt",
        "license.md",
        "notice",
    }
)

# Suffixes that survive the binary sniff (they are text) but are assets or generated
# artefacts rather than source: vector images, source maps, minified bundles.
DEFAULT_IGNORE_SUFFIXES: tuple[str, ...] = (
    ".svg",
    ".map",
    ".min.js",
    ".min.css",
    ".lock",
)

# Files above this size are dead-lettered as "oversized" content failures (§5.8)
# rather than parsed — a multi-megabyte generated file is not source worth chunking.
#
# This bound is tied to the SUMMARISER's context window, not just to disk size: a
# file that survives the walk is later rendered into one tier-1 prompt. At the
# guard's pessimistic ~3 chars/token (see summarise.tiers.GUARD_CHARS_PER_TOKEN),
# 300 KB ≈ 100k tokens, which fits the smallest context we target (128k) with room
# for the instructions and the completion. Raising this without raising that budget
# reintroduces the failure it prevents: the previous 1.5 MB (~500k tokens) let a
# 730 KB generated graph.json through, whose 214k-token prompt aborted the run.
DEFAULT_MAX_BYTES = 300_000


@dataclass(frozen=True)
class WalkedFile:
    service: str
    path: str  # repo-relative, posix
    abs_path: Path


def walk_estate(
    root: Path,
    manifest: Manifest,
    *,
    ignore_dirs: frozenset[str] = DEFAULT_IGNORE_DIRS,
    ignore_filenames: frozenset[str] = DEFAULT_IGNORE_FILENAMES,
    ignore_suffixes: tuple[str, ...] = DEFAULT_IGNORE_SUFFIXES,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> Iterator[WalkedFile]:
    """Yield every manifest-owned regular file under ``root``, once each.

    Skips ignored directories, symlinks, binary files (null-byte sniff), oversized
    files, and files that are text but carry no answerable meaning (ignore lists,
    lock files, assets — see :data:`DEFAULT_IGNORE_FILENAMES`). Ordering is
    deterministic (sorted) so ingestion runs are reproducible and diffable.
    """
    root = root.resolve()
    seen: set[str] = set()

    for svc in manifest.services:
        svc_dir = (root / svc.path).resolve()
        if not svc_dir.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(svc_dir):
            # Prune ignored dirs in place; sort for deterministic traversal.
            dirnames[:] = sorted(d for d in dirnames if d not in ignore_dirs)
            for filename in sorted(filenames):
                if _is_noise(filename, ignore_filenames, ignore_suffixes):
                    continue
                abs_path = Path(dirpath) / filename
                if abs_path.is_symlink() or not abs_path.is_file():
                    continue
                rel = _rel_posix(abs_path, root)
                if rel in seen:
                    continue
                owner = manifest.service_for_path(rel)
                if owner is None or owner.name != svc.name:
                    # Belongs to a more specific (nested) service; handled there.
                    continue
                if not _is_indexable(abs_path, max_bytes):
                    continue
                seen.add(rel)
                yield WalkedFile(service=owner.name, path=rel, abs_path=abs_path)


def _is_noise(
    filename: str, ignore_filenames: frozenset[str], ignore_suffixes: tuple[str, ...]
) -> bool:
    """Whether a file is text but not worth indexing. Matched case-insensitively so
    LICENSE/License/license and Cargo.lock/cargo.lock behave identically."""
    lowered = filename.lower()
    return lowered in ignore_filenames or lowered.endswith(ignore_suffixes)


def _rel_posix(abs_path: Path, root: Path) -> str:
    return str(PurePosixPath(abs_path.resolve().relative_to(root)))


def _is_indexable(path: Path, max_bytes: int) -> bool:
    try:
        size = path.stat().st_size
    except OSError:
        return False
    if size == 0 or size > max_bytes:
        return False
    # Binary sniff: a null byte in the first 8 KiB means not source text.
    try:
        with path.open("rb") as fh:
            head = fh.read(8192)
    except OSError:
        return False
    return b"\x00" not in head
