"""AST fact extraction for graph construction (§Phase 3: call graph from AST).

Walks a file once and pulls out the facts the graph needs beyond the Phase-1
skeleton: which symbols it defines, which call sites it contains (caller →
callee name), and which topics it publishes/consumes. Symbol *resolution*
(name → owning service) happens later in :mod:`backend.graph.builder`, once all
files' facts are known — a callee can live in a different repository.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from backend.domain.models import Language
from backend.ingestion.languages import LanguageSpec, spec_for
from backend.ingestion.parser import parse

if TYPE_CHECKING:
    from tree_sitter import Node

# Heuristic messaging call names → edge direction. Conservative on purpose: a
# false topic edge is worse than a missed one (it invents a cross-service claim).
_PUBLISH_CALLS = frozenset({"publish", "emit", "produce"})
_CONSUME_CALLS = frozenset({"consume", "subscribe"})


@dataclass
class FileFacts:
    service: str
    path: str
    defined_symbols: set[str] = field(default_factory=set)
    calls: list[tuple[str | None, str]] = field(default_factory=list)  # (caller, callee)
    publishes: set[str] = field(default_factory=set)
    consumes: set[str] = field(default_factory=set)


def analyze_file(*, service: str, path: str, source: bytes, language: Language) -> FileFacts:
    """Extract graph facts from one file. Unsupported languages yield empty facts
    (they are still chunked and lexically searchable, just not graph-linked)."""
    facts = FileFacts(service=service, path=path)
    spec = spec_for(language)
    if spec is None:
        return facts
    tree = parse(source, language)
    _walk(tree.root_node, spec, facts, current_func=None)
    return facts


def _walk(node: Node, spec: LanguageSpec, facts: FileFacts, *, current_func: str | None) -> None:
    for child in node.named_children:
        if child.type in spec.func_nodes or child.type in spec.class_nodes:
            name = _declared_name(child, spec)
            if name:
                facts.defined_symbols.add(name)
            next_func = name if child.type in spec.func_nodes else current_func
            _walk(child, spec, facts, current_func=next_func)
        elif child.type in spec.call_nodes:
            _record_call(child, spec, facts, current_func)
            _walk(child, spec, facts, current_func=current_func)
        else:
            _walk(child, spec, facts, current_func=current_func)


def _record_call(
    call_node: Node, spec: LanguageSpec, facts: FileFacts, current_func: str | None
) -> None:
    callee = _callee_name(call_node, spec)
    if callee is None:
        return
    facts.calls.append((current_func, callee))
    if callee in _PUBLISH_CALLS or callee in _CONSUME_CALLS:
        topic = _first_string_arg(call_node)
        if topic:
            (facts.publishes if callee in _PUBLISH_CALLS else facts.consumes).add(topic)


# --------------------------------------------------------------------------- #
# Node helpers
# --------------------------------------------------------------------------- #


def _declared_name(node: Node, spec: LanguageSpec) -> str | None:
    field_node = node.child_by_field_name(spec.name_field)
    if field_node is not None and field_node.text:
        return field_node.text.decode("utf-8", errors="replace")
    return None


def _callee_name(call_node: Node, spec: LanguageSpec) -> str | None:
    target = call_node.child_by_field_name("function") or call_node.child_by_field_name("name")
    if target is None:
        target = _first_non_argument_child(call_node)
    if target is None:
        return None
    return _last_identifier(target, spec)


def _first_non_argument_child(call_node: Node) -> Node | None:
    for child in call_node.named_children:
        if not _is_argument_container(child.type):
            return child
    return None


def _last_identifier(node: Node, spec: LanguageSpec) -> str | None:
    """Rightmost identifier in a callee expression: ``a.b.c`` → ``c``."""
    result: str | None = None
    stack: list[Node] = [node]
    order: list[Node] = []
    while stack:
        n = stack.pop()
        order.append(n)
        stack.extend(n.named_children)
    for n in order:
        if n.type in spec.ident_nodes and n.child_count == 0 and n.text:
            result = n.text.decode("utf-8", errors="replace")
    return result


def _first_string_arg(call_node: Node) -> str | None:
    args = call_node.child_by_field_name("arguments")
    if args is None:
        for child in call_node.named_children:
            if _is_argument_container(child.type):
                args = child
                break
    if args is None:
        return None
    for arg in args.named_children:
        if "string" in arg.type and arg.text:
            return _string_value(arg.text.decode("utf-8", errors="replace"))
    return None


def _is_argument_container(node_type: str) -> bool:
    return "argument" in node_type or node_type == "arguments"


def _string_value(text: str) -> str:
    return text.strip().strip("'\"`").strip()
