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
    }
)

# Files above this size are dead-lettered as "oversized" content failures (§5.8)
# rather than parsed — a multi-megabyte generated file is not source worth chunking.
DEFAULT_MAX_BYTES = 1_500_000


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
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> Iterator[WalkedFile]:
    """Yield every manifest-owned regular file under ``root``, once each.

    Skips ignored directories, symlinks, binary files (null-byte sniff), and
    oversized files. Ordering is deterministic (sorted) so ingestion runs are
    reproducible and diffable.
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
