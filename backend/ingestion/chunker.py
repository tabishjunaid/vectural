"""Chunking at function/class boundaries (§5.1).

This is the single most consequence-laden deterministic step: "bad chunk
boundaries corrupt every downstream tier" (design-document §2.1). The strategy
is intentionally boundary-aligned and non-overlapping:

- each top-level function → one ``FUNCTION`` chunk
- each method inside a class → one ``METHOD`` chunk
- a class with methods → a ``CLASS`` header chunk (declaration + fields, up to
  the first method) plus the method chunks; a class with no methods → one
  ``CLASS`` chunk covering the whole class
- top-of-file residue not covered by any definition (imports, module-level
  config) → ``MODULE`` chunk(s)

Definition spans expand up through language wrappers (Python decorators,
``export``/``const foo = () =>``) so the chunk is the whole statement, not the
bare callable, and carries the declared name.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from backend.domain.models import Chunk, ChunkKind, Language, Span
from backend.ingestion.languages import LanguageSpec, spec_for
from backend.ingestion.parser import parse

if TYPE_CHECKING:
    from tree_sitter import Node

# Module chunks (imports, whole unsupported files) are windowed so a giant
# generated file cannot produce one enormous chunk. Definition chunks are never
# split — a function stays whole regardless of length.
DEFAULT_MODULE_WINDOW = 200


@dataclass
class _Def:
    """An intermediate definition to be emitted, in 0-indexed row space."""

    kind: ChunkKind
    start_row: int
    end_row: int  # inclusive
    symbol: str | None
    identifiers: list[str] = field(default_factory=list)


def chunk_source(
    source: bytes,
    *,
    language: Language,
    service: str,
    path: str,
    commit_sha: str,
    module_window: int = DEFAULT_MODULE_WINDOW,
) -> list[Chunk]:
    """Chunk one file's ``source`` bytes into retrievable :class:`Chunk` objects.

    A language with no tree-sitter spec (or ``UNKNOWN``) still yields whole-file
    ``MODULE`` chunks so the file remains lexically searchable — it simply has no
    structural chunks. Empty / whitespace-only files yield no chunks.
    """
    lines = source.split(b"\n")
    spec = spec_for(language)
    if spec is None:
        drafts = _whole_file_module(lines, module_window)
    else:
        drafts, container_spans = _structural_defs(source, language, spec)
        drafts += _module_residue(lines, drafts, container_spans, module_window)
        drafts.sort(key=lambda d: (d.start_row, d.end_row))

    chunks: list[Chunk] = []
    seen_spans: set[tuple[int, int]] = set()
    for d in drafts:
        span_key = (d.start_row, d.end_row)
        if span_key in seen_spans:  # dedupe promoted spans (multi-declarator, etc.)
            continue
        seen_spans.add(span_key)
        content = _slice_lines(lines, d.start_row, d.end_row)
        if not content.strip():
            continue
        chunks.append(
            _build_chunk(
                content=content,
                kind=d.kind,
                start_row=d.start_row,
                end_row=d.end_row,
                symbol=d.symbol,
                identifiers=d.identifiers,
                language=language,
                service=service,
                path=path,
                commit_sha=commit_sha,
            )
        )
    return chunks


# --------------------------------------------------------------------------- #
# Structural (function/class) chunking
# --------------------------------------------------------------------------- #


def _structural_defs(
    source: bytes, language: Language, spec: LanguageSpec
) -> tuple[list[_Def], list[tuple[int, int]]]:
    tree = parse(source, language)
    containers: list[tuple[int, int]] = []
    defs = _collect(tree.root_node, source, spec, inside_class=False, containers=containers)
    return defs, containers


def _collect(
    node: Node,
    source: bytes,
    spec: LanguageSpec,
    *,
    inside_class: bool,
    containers: list[tuple[int, int]],
) -> list[_Def]:
    defs: list[_Def] = []
    for child in node.named_children:
        role = _role(child.type, spec)
        if role is None:
            # Not itself a definition — descend to find definitions nested under
            # wrappers (export statements, namespaces, decorated blocks, bodies).
            defs.extend(
                _collect(child, source, spec, inside_class=inside_class, containers=containers)
            )
            continue

        span_node, symbol = _resolve_span_and_name(child, spec)
        if role == "func":
            kind = ChunkKind.METHOD if inside_class else ChunkKind.FUNCTION
            defs.append(
                _Def(
                    kind=kind,
                    start_row=span_node.start_point[0],
                    end_row=span_node.end_point[0],
                    symbol=symbol,
                    identifiers=_identifiers(span_node, source, spec),
                )
            )
            # Do not recurse into a function body; nested defs stay part of it.
        else:  # class-like container
            # Record the whole container span so residue never reclaims its
            # braces/footer, even though we only chunk the header + members.
            containers.append((span_node.start_point[0], span_node.end_point[0]))
            nested = _collect(child, source, spec, inside_class=True, containers=containers)
            if nested:
                first_member = min(d.start_row for d in nested)
                header_end = first_member - 1
                if header_end >= span_node.start_point[0]:
                    defs.append(
                        _Def(
                            kind=ChunkKind.CLASS,
                            start_row=span_node.start_point[0],
                            end_row=header_end,
                            symbol=symbol,
                            identifiers=_identifiers(
                                span_node, source, spec, max_row=header_end
                            ),
                        )
                    )
                defs.extend(nested)
            else:
                defs.append(
                    _Def(
                        kind=ChunkKind.CLASS,
                        start_row=span_node.start_point[0],
                        end_row=span_node.end_point[0],
                        symbol=symbol,
                        identifiers=_identifiers(span_node, source, spec),
                    )
                )
    return defs


def _role(node_type: str, spec: LanguageSpec) -> str | None:
    if node_type in spec.func_nodes:
        return "func"
    if node_type in spec.class_nodes:
        return "class"
    return None


def _resolve_span_and_name(def_node: Node, spec: LanguageSpec) -> tuple[Node, str | None]:
    """Expand a definition's span up through wrapper nodes and resolve its name.

    Anonymous callables (``() => {}``) borrow the name of the nearest naming
    wrapper (``const foo = ...``), and the span grows to the whole statement.
    """
    name = _declared_name(def_node, spec)
    span_node = def_node
    cur = def_node
    while cur.parent is not None and cur.parent.type in spec.wrapper_span_nodes:
        cur = cur.parent
        span_node = cur
        if name is None:
            name = _declared_name(cur, spec)
    return span_node, name


def _declared_name(node: Node, spec: LanguageSpec) -> str | None:
    field_node = node.child_by_field_name(spec.name_field)
    if field_node is not None:
        return field_node.text.decode("utf-8", errors="replace") if field_node.text else None
    return None


def _identifiers(
    node: Node, source: bytes, spec: LanguageSpec, *, max_row: int | None = None
) -> list[str]:
    """Ordered, de-duplicated identifier tokens within a node's subtree.

    Feeds the boosted ``identifiers`` field (§3.2) so a search for
    ``processRefundReversal`` matches even when it never appears in prose. When
    ``max_row`` is set (class header extraction), identifiers below that row —
    i.e. inside method bodies — are excluded.
    """
    out: list[str] = []
    seen: set[str] = set()
    stack: list[Node] = [node]
    while stack:
        n = stack.pop()
        if max_row is not None and n.start_point[0] > max_row:
            continue
        if n.type in spec.ident_nodes and n.child_count == 0:
            text = n.text.decode("utf-8", errors="replace") if n.text else ""
            if text and text not in seen:
                seen.add(text)
                out.append(text)
        stack.extend(reversed(n.children))
    return out


# --------------------------------------------------------------------------- #
# Module residue (uncovered top-level lines) and whole-file fallback
# --------------------------------------------------------------------------- #


def _module_residue(
    lines: list[bytes],
    defs: list[_Def],
    container_spans: list[tuple[int, int]],
    window: int,
) -> list[_Def]:
    """Emit ``MODULE`` chunks for maximal runs of lines no definition covers.

    Blank-only runs are dropped; a leading import block or a whole no-definition
    file becomes one (windowed) module chunk. ``container_spans`` mark class/
    interface bodies as fully covered so a class's closing brace never leaks out
    as a one-line residue chunk.
    """
    covered = bytearray(len(lines))
    spans: list[tuple[int, int]] = [(d.start_row, d.end_row) for d in defs]
    spans.extend(container_spans)
    for start, end in spans:
        for row in range(start, min(end, len(lines) - 1) + 1):
            covered[row] = 1

    out: list[_Def] = []
    row = 0
    n = len(lines)
    while row < n:
        if covered[row]:
            row += 1
            continue
        start = row
        while row < n and not covered[row]:
            row += 1
        end = row - 1
        # Trim leading/trailing blank lines from the uncovered block.
        while start <= end and not lines[start].strip():
            start += 1
        while end >= start and not lines[end].strip():
            end -= 1
        if start <= end:
            out.extend(_window_module(start, end, window))
    return out


def _whole_file_module(lines: list[bytes], window: int) -> list[_Def]:
    start, end = 0, len(lines) - 1
    while start <= end and not lines[start].strip():
        start += 1
    while end >= start and not lines[end].strip():
        end -= 1
    if start > end:
        return []
    return _window_module(start, end, window)


def _window_module(start_row: int, end_row: int, window: int) -> list[_Def]:
    if window <= 0 or (end_row - start_row + 1) <= window:
        return [_Def(kind=ChunkKind.MODULE, start_row=start_row, end_row=end_row, symbol=None)]
    out: list[_Def] = []
    row = start_row
    while row <= end_row:
        win_end = min(row + window - 1, end_row)
        out.append(_Def(kind=ChunkKind.MODULE, start_row=row, end_row=win_end, symbol=None))
        row = win_end + 1
    return out


# --------------------------------------------------------------------------- #
# Chunk assembly
# --------------------------------------------------------------------------- #


def _slice_lines(lines: list[bytes], start_row: int, end_row: int) -> str:
    end_row = min(end_row, len(lines) - 1)
    return b"\n".join(lines[start_row : end_row + 1]).decode("utf-8", errors="replace")


def _build_chunk(
    *,
    content: str,
    kind: ChunkKind,
    start_row: int,
    end_row: int,
    symbol: str | None,
    identifiers: list[str],
    language: Language,
    service: str,
    path: str,
    commit_sha: str,
) -> Chunk:
    span = Span(start=start_row + 1, end=end_row + 1)  # tree-sitter rows are 0-indexed
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
    chunk_id = f"{service}:{path}:{span}:{content_hash[:8]}"
    return Chunk(
        chunk_id=chunk_id,
        service=service,
        path=path,
        language=language,
        kind=kind,
        span=span,
        content=content,
        identifiers=identifiers,
        symbol=symbol,
        commit_sha=commit_sha,
        content_hash=content_hash,
        doc_type="code",
    )
