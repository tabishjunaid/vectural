"""Thin, cached wrapper over tree-sitter parsers (§5.1).

One parser instance per grammar is reused across files — constructing them is
comparatively expensive and the pipeline parses tens of thousands of files.
tree-sitter is error-tolerant by design (implementation-plan §3), so a file
with syntax errors still yields a usable partial tree rather than throwing;
callers detect that via :func:`has_errors`.
"""

from __future__ import annotations

from functools import cache
from typing import TYPE_CHECKING, cast

from backend.domain.models import Language
from backend.ingestion.languages import spec_for

if TYPE_CHECKING:
    from tree_sitter import Node, Parser, Tree


class UnsupportedLanguageError(ValueError):
    """Raised when asked to parse a language with no tree-sitter spec."""


@cache
def _parser_for(grammar: str) -> Parser:
    from tree_sitter_language_pack import SupportedLanguage, get_parser

    return get_parser(cast("SupportedLanguage", grammar))


def parse(source: bytes, language: Language) -> Tree:
    """Parse ``source`` bytes with the grammar for ``language``.

    Raises :class:`UnsupportedLanguageError` if the language has no spec — callers
    that want the graceful whole-file fallback should check :func:`spec_for`
    first rather than catching this.
    """
    spec = spec_for(language)
    if spec is None:
        raise UnsupportedLanguageError(f"no tree-sitter spec for {language}")
    return _parser_for(spec.grammar).parse(source)


def has_errors(tree: Tree) -> bool:
    """Whether the parse tree contains any ERROR or missing nodes."""
    return bool(tree.root_node.has_error)


def node_text(node: Node, source: bytes) -> str:
    """Decode a node's source span to text, tolerant of invalid UTF-8."""
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")
