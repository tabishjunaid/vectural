"""Per-language tree-sitter node-type specifications.

Chunking is deterministic and language-driven: for each supported grammar we
declare which node types are function/method definitions, which are class-like
containers, which represent a call site (for identifier extraction, and the
Phase 3 call graph), and which leaves count as identifiers.

Adding a language is a data change here plus a grammar in the language pack —
never a change to the chunker's control flow (§5.1: one toolchain across all
languages).
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.domain.models import Language


@dataclass(frozen=True)
class LanguageSpec:
    grammar: str  # tree-sitter-language-pack registry key
    func_nodes: frozenset[str]  # function/method definition node types
    class_nodes: frozenset[str]  # class-like container node types
    call_nodes: frozenset[str]  # call-expression node types
    ident_nodes: frozenset[str]  # leaf node types treated as identifiers
    name_field: str = "name"  # field carrying a definition's declared name
    # Ancestor node types a definition's chunk span expands *up* through, so a
    # decorated Python def, an `export function`, or a `const foo = () => {}`
    # is chunked (and named) as the whole statement rather than the bare lambda.
    wrapper_span_nodes: frozenset[str] = frozenset()


_SPECS: dict[Language, LanguageSpec] = {
    Language.PYTHON: LanguageSpec(
        grammar="python",
        func_nodes=frozenset({"function_definition"}),
        class_nodes=frozenset({"class_definition"}),
        call_nodes=frozenset({"call"}),
        ident_nodes=frozenset({"identifier"}),
        wrapper_span_nodes=frozenset({"decorated_definition"}),
    ),
    Language.JAVASCRIPT: LanguageSpec(
        grammar="javascript",
        func_nodes=frozenset(
            {
                "function_declaration",
                "function_expression",
                "generator_function_declaration",
                "method_definition",
                "arrow_function",
            }
        ),
        class_nodes=frozenset({"class_declaration", "class"}),
        call_nodes=frozenset({"call_expression", "new_expression"}),
        ident_nodes=frozenset(
            {"identifier", "property_identifier", "shorthand_property_identifier"}
        ),
        wrapper_span_nodes=frozenset(
            {
                "export_statement",
                "lexical_declaration",
                "variable_declaration",
                "variable_declarator",
                "expression_statement",
            }
        ),
    ),
    Language.TYPESCRIPT: LanguageSpec(
        grammar="typescript",
        func_nodes=frozenset(
            {
                "function_declaration",
                "function_expression",
                "generator_function_declaration",
                "method_definition",
                "method_signature",
                "arrow_function",
            }
        ),
        class_nodes=frozenset(
            {
                "class_declaration",
                "abstract_class_declaration",
                "interface_declaration",
                "enum_declaration",
            }
        ),
        call_nodes=frozenset({"call_expression", "new_expression"}),
        ident_nodes=frozenset(
            {
                "identifier",
                "type_identifier",
                "property_identifier",
                "shorthand_property_identifier",
            }
        ),
        wrapper_span_nodes=frozenset(
            {
                "export_statement",
                "lexical_declaration",
                "variable_declaration",
                "variable_declarator",
                "expression_statement",
                "public_field_definition",
            }
        ),
    ),
    Language.JAVA: LanguageSpec(
        grammar="java",
        func_nodes=frozenset({"method_declaration", "constructor_declaration"}),
        class_nodes=frozenset(
            {"class_declaration", "interface_declaration", "enum_declaration", "record_declaration"}
        ),
        call_nodes=frozenset({"method_invocation", "object_creation_expression"}),
        ident_nodes=frozenset({"identifier", "type_identifier"}),
    ),
    Language.GO: LanguageSpec(
        grammar="go",
        func_nodes=frozenset({"function_declaration", "method_declaration"}),
        class_nodes=frozenset({"type_declaration"}),
        call_nodes=frozenset({"call_expression"}),
        ident_nodes=frozenset({"identifier", "field_identifier", "type_identifier"}),
    ),
    Language.RUBY: LanguageSpec(
        grammar="ruby",
        func_nodes=frozenset({"method", "singleton_method"}),
        class_nodes=frozenset({"class", "module"}),
        call_nodes=frozenset({"call"}),
        ident_nodes=frozenset({"identifier", "constant"}),
    ),
    Language.KOTLIN: LanguageSpec(
        grammar="kotlin",
        func_nodes=frozenset({"function_declaration"}),
        class_nodes=frozenset({"class_declaration", "object_declaration"}),
        call_nodes=frozenset({"call_expression"}),
        ident_nodes=frozenset({"simple_identifier", "type_identifier"}),
    ),
    Language.RUST: LanguageSpec(
        grammar="rust",
        func_nodes=frozenset({"function_item"}),
        class_nodes=frozenset({"struct_item", "enum_item", "trait_item", "impl_item"}),
        call_nodes=frozenset({"call_expression", "macro_invocation"}),
        ident_nodes=frozenset({"identifier", "type_identifier", "field_identifier"}),
    ),
    Language.CSHARP: LanguageSpec(
        grammar="csharp",
        func_nodes=frozenset(
            {"method_declaration", "constructor_declaration", "local_function_statement"}
        ),
        class_nodes=frozenset(
            {
                "class_declaration",
                "interface_declaration",
                "struct_declaration",
                "enum_declaration",
                "record_declaration",
            }
        ),
        call_nodes=frozenset({"invocation_expression", "object_creation_expression"}),
        ident_nodes=frozenset({"identifier"}),
    ),
}

# TSX shares the TypeScript spec but uses the tsx grammar (JSX-aware).
_SPECS[Language.TSX] = LanguageSpec(
    grammar="tsx",
    func_nodes=_SPECS[Language.TYPESCRIPT].func_nodes,
    class_nodes=_SPECS[Language.TYPESCRIPT].class_nodes,
    call_nodes=_SPECS[Language.TYPESCRIPT].call_nodes,
    ident_nodes=_SPECS[Language.TYPESCRIPT].ident_nodes,
    wrapper_span_nodes=_SPECS[Language.TYPESCRIPT].wrapper_span_nodes,
)


def spec_for(language: Language) -> LanguageSpec | None:
    """Return the parsing spec for a language, or ``None`` if unsupported.

    ``None`` is not an error — an unsupported or ``UNKNOWN`` language still gets a
    whole-file module chunk (lexically searchable), it just gets no structural
    (function/class) chunks or graph deltas.
    """
    return _SPECS.get(language)


def supported_languages() -> frozenset[Language]:
    return frozenset(_SPECS)


# Convenience re-export kept for callers that only need the field.
__all__ = ["LanguageSpec", "spec_for", "supported_languages"]
