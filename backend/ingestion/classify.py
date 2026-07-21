"""Language classification (§5.1: walk → classify → parse).

Classification is by file extension, with a small set of well-known exact
filenames. It is deliberately conservative: a file it cannot classify becomes
:data:`Language.UNKNOWN` rather than being guessed at, and the pipeline still
lexically indexes it. Being wrong here would hand the wrong grammar to the
parser and corrupt chunk boundaries (§2.1 failure mode).
"""

from __future__ import annotations

from pathlib import PurePosixPath

from backend.domain.models import Language

_EXTENSION_MAP: dict[str, Language] = {
    ".py": Language.PYTHON,
    ".pyi": Language.PYTHON,
    ".js": Language.JAVASCRIPT,
    ".jsx": Language.JAVASCRIPT,
    ".mjs": Language.JAVASCRIPT,
    ".cjs": Language.JAVASCRIPT,
    ".ts": Language.TYPESCRIPT,
    ".mts": Language.TYPESCRIPT,
    ".cts": Language.TYPESCRIPT,
    ".tsx": Language.TSX,
    ".java": Language.JAVA,
    ".go": Language.GO,
    ".rb": Language.RUBY,
    ".cs": Language.CSHARP,
    ".kt": Language.KOTLIN,
    ".kts": Language.KOTLIN,
    ".rs": Language.RUST,
}


def classify_path(path: str) -> Language:
    """Classify a repo-relative (or bare) path to a :class:`Language`.

    ``.d.ts`` declaration files are treated as TypeScript. Case is normalised on
    the extension only — real source trees mix ``.PY`` on case-insensitive
    filesystems.
    """
    name = PurePosixPath(path).name.lower()
    if name.endswith(".d.ts"):
        return Language.TYPESCRIPT
    suffix = PurePosixPath(name).suffix
    return _EXTENSION_MAP.get(suffix, Language.UNKNOWN)
