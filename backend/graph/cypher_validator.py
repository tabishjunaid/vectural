"""Cypher validation layer (§5.2, §3.1 "hop cap enforced at validation time").

Guards every LLM-generated Cypher query (§5.3 step 2) before it touches Neo4j.
Checks run **in order, short-circuiting on the first failure** so the returned
reason is the most specific one — that reason is appended to the prompt on the
single regeneration retry (§5.2). The checks:

1. non-empty and bracket-balanced (a cheap parse gate)
2. every referenced label / relationship type is in the allowed set
3. traversal depth ≤ the configured hop cap (variable-length bounds + fixed hops)
4. read-only — no ``CREATE``/``MERGE``/``DELETE``/``SET``/``REMOVE`` (or ``DETACH``)

This is the hand-rolled-check approach the design's open item #1 assumes; it is
intentionally conservative (it rejects rather than risks) because a false accept
on the read-only or hop-cap check is far costlier than a false reject that falls
back to a templated structural query.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from backend.graph.schema import ALLOWED_LABELS, ALLOWED_REL_TYPES

DEFAULT_HOP_CAP = 5

# Write clauses that make a query non-read-only (§5.2). DETACH accompanies DELETE
# but is listed so `DETACH DELETE` is caught even if split oddly.
_WRITE_KEYWORDS = ("CREATE", "MERGE", "DELETE", "SET", "REMOVE", "DETACH", "DROP")

# Clause keywords used to segment a query so hops are counted per-path, not
# summed across independent MATCH clauses.
_CLAUSE_SPLIT = re.compile(
    r"\b(MATCH|OPTIONAL\s+MATCH|WHERE|RETURN|WITH|UNWIND|ORDER\s+BY|LIMIT|SKIP|CALL)\b",
    re.IGNORECASE,
)

_STRING_LITERAL = re.compile(r"'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"")
_MAP_LITERAL = re.compile(r"\{[^{}]*\}")
_LINE_COMMENT = re.compile(r"//[^\n]*")
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_LABEL_TOKEN = re.compile(r":\s*`?([A-Za-z_][A-Za-z0-9_]*)`?")
_VAR_LENGTH = re.compile(r"\*\s*(\d*)\s*(\.\.)?\s*(\d*)")


class CypherValidationError(ValueError):
    """Raised by :func:`ensure_valid` when a query fails validation."""


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    query: str | None = None
    reason: str | None = None

    @classmethod
    def valid(cls, query: str) -> ValidationResult:
        return cls(ok=True, query=query)

    @classmethod
    def invalid(cls, reason: str) -> ValidationResult:
        return cls(ok=False, reason=reason)


def validate_cypher(
    query: str,
    *,
    allowed_labels: frozenset[str] = ALLOWED_LABELS,
    allowed_rel_types: frozenset[str] = ALLOWED_REL_TYPES,
    hop_cap: int = DEFAULT_HOP_CAP,
) -> ValidationResult:
    """Validate a raw Cypher string. Returns :class:`ValidationResult`."""
    raw = query.strip()
    if not raw:
        return ValidationResult.invalid("query is empty")

    if (reason := _balanced(raw)) is not None:
        return ValidationResult.invalid(reason)

    # Strip comments and string literals once; every subsequent check operates on
    # code, never on text inside a string (so a topic named "delete-me" is fine).
    code = _strip_noise(raw)

    if (reason := _check_read_only(code)) is not None:
        return ValidationResult.invalid(reason)
    if (reason := _check_labels(code, allowed_labels, allowed_rel_types)) is not None:
        return ValidationResult.invalid(reason)
    if (reason := _check_hop_cap(code, hop_cap)) is not None:
        return ValidationResult.invalid(reason)

    return ValidationResult.valid(raw)


def ensure_valid(query: str, **kwargs: object) -> str:
    """Validate and return the query, or raise :class:`CypherValidationError`."""
    result = validate_cypher(query, **kwargs)  # type: ignore[arg-type]
    if not result.ok or result.query is None:
        raise CypherValidationError(result.reason or "invalid cypher")
    return result.query


# --------------------------------------------------------------------------- #
# Individual checks
# --------------------------------------------------------------------------- #


def _balanced(query: str) -> str | None:
    pairs = {")": "(", "]": "[", "}": "{"}
    stack: list[str] = []
    in_string: str | None = None
    prev = ""
    for ch in query:
        if in_string is not None:
            if ch == in_string and prev != "\\":
                in_string = None
            prev = ch
            continue
        if ch in ("'", '"'):
            in_string = ch
        elif ch in "([{":
            stack.append(ch)
        elif ch in ")]}":
            if not stack or stack[-1] != pairs[ch]:
                return f"unbalanced '{ch}'"
            stack.pop()
        prev = ch
    if in_string is not None:
        return "unterminated string literal"
    if stack:
        return f"unclosed '{stack[-1]}'"
    return None


def _strip_noise(query: str) -> str:
    query = _BLOCK_COMMENT.sub(" ", query)
    query = _LINE_COMMENT.sub(" ", query)
    query = _STRING_LITERAL.sub("''", query)
    # Remove map literals so `{status: active}` never reads as a `:Label`.
    while _MAP_LITERAL.search(query):
        query = _MAP_LITERAL.sub(" ", query)
    return query


def _check_read_only(code: str) -> str | None:
    upper = code.upper()
    for kw in _WRITE_KEYWORDS:
        if re.search(rf"\b{kw}\b", upper):
            return f"write clause '{kw}' is not permitted (read-only, §5.2)"
    return None


def _check_labels(
    code: str, allowed_labels: frozenset[str], allowed_rel_types: frozenset[str]
) -> str | None:
    node_labels = _labels_in(code, r"\(([^()]*)\)")
    rel_types = _labels_in(code, r"\[([^\[\]]*)\]")

    bad_labels = node_labels - allowed_labels
    if bad_labels:
        return f"label(s) not in schema: {', '.join(sorted(bad_labels))}"
    bad_rels = rel_types - allowed_rel_types
    if bad_rels:
        return f"relationship type(s) not in schema: {', '.join(sorted(bad_rels))}"

    # Catch labels/types used outside node/relationship patterns, e.g. a
    # `WHERE n:SecretLabel` predicate. After noise-stripping, any remaining
    # `:Ident` token is a label or relationship type and must be in the union.
    allowed_union = allowed_labels | allowed_rel_types
    stray = set(_LABEL_TOKEN.findall(code)) - node_labels - rel_types - allowed_union
    if stray:
        return f"label(s) not in schema: {', '.join(sorted(stray))}"
    return None


def _labels_in(code: str, container_pattern: str) -> set[str]:
    found: set[str] = set()
    for block in re.findall(container_pattern, code):
        found.update(_LABEL_TOKEN.findall(block))
    return found


def _check_hop_cap(code: str, hop_cap: int) -> str | None:
    if (reason := _check_var_length(code, hop_cap)) is not None:
        return reason
    # Fixed-length hops: count relationship connectors per path segment.
    for segment in _CLAUSE_SPLIT.split(code):
        hops = _count_fixed_hops(segment)
        if hops > hop_cap:
            return f"traversal depth {hops} exceeds hop cap {hop_cap}"
    return None


def _check_var_length(code: str, hop_cap: int) -> str | None:
    for block in re.findall(r"\[([^\[\]]*)\]", code):
        if "*" not in block:
            continue
        m = _VAR_LENGTH.search(block)
        if m is None:
            continue
        low_s, dots, high_s = m.group(1), m.group(2), m.group(3)
        if not dots:
            # `*` (unbounded) or `*n` (exact n).
            if low_s == "":
                return "unbounded variable-length relationship '*' exceeds hop cap"
            depth = int(low_s)
        else:
            # `*n..m`, `*..m`, `*n..`.
            if high_s == "":
                return "unbounded variable-length relationship (no upper bound) exceeds hop cap"
            depth = int(high_s)
        if depth > hop_cap:
            return f"variable-length depth {depth} exceeds hop cap {hop_cap}"
    return None


def _count_fixed_hops(segment: str) -> int:
    # Collapse bracketed relationship detail so `-[:CALLS]->` becomes `-->`, then
    # count relationship connectors. Variable-length (`*`) hops are handled above.
    collapsed = re.sub(r"\[[^\[\]]*\]", "", segment)
    return len(re.findall(r"<--|-->|<-|->|--", collapsed))
