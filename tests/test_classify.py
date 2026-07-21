"""Language classification (§5.1)."""

from __future__ import annotations

import pytest

from backend.domain.models import Language
from backend.ingestion.classify import classify_path


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("payments-api/refund.py", Language.PYTHON),
        ("web/app.tsx", Language.TSX),
        ("web/api.ts", Language.TYPESCRIPT),
        ("web/types.d.ts", Language.TYPESCRIPT),
        ("web/legacy.js", Language.JAVASCRIPT),
        ("svc/Main.java", Language.JAVA),
        ("svc/main.go", Language.GO),
        ("svc/model.rb", Language.RUBY),
        ("svc/Program.cs", Language.CSHARP),
        ("svc/App.kt", Language.KOTLIN),
        ("svc/lib.rs", Language.RUST),
        ("README.md", Language.UNKNOWN),
        ("Dockerfile", Language.UNKNOWN),
        ("config.YAML", Language.UNKNOWN),
    ],
)
def test_classify_path(path: str, expected: Language) -> None:
    assert classify_path(path) is expected
