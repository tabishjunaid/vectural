"""Endpoint extraction from OpenAPI specs (§Phase 3: endpoints from OpenAPI).

OpenAPI is the reliable source for a service's HTTP surface — more so than
scraping route decorators across frameworks — so ``Service -EXPOSES-> Endpoint``
edges come from here. JSON specs parse fine via the YAML loader (JSON ⊂ YAML).
"""

from __future__ import annotations

from typing import Any

import yaml

_HTTP_METHODS = frozenset({"get", "put", "post", "delete", "patch", "head", "options", "trace"})


def looks_like_openapi(path: str, source: bytes) -> bool:
    """Cheap sniff before a full parse: right extension + telltale keys."""
    lower = path.lower()
    if not lower.endswith((".yaml", ".yml", ".json")):
        return False
    head = source[:4096].lower()
    return (b"openapi" in head or b"swagger" in head) and b"paths" in head


def parse_openapi(source: bytes) -> dict[str, Any] | None:
    """Parse a spec, returning the document only if it has a ``paths`` mapping."""
    try:
        doc = yaml.safe_load(source)
    except yaml.YAMLError:
        return None
    if isinstance(doc, dict) and isinstance(doc.get("paths"), dict):
        return doc
    return None


def endpoints(doc: dict[str, Any]) -> list[tuple[str, str]]:
    """``(METHOD, route)`` pairs declared in the spec, sorted deterministically."""
    out: list[tuple[str, str]] = []
    paths = doc.get("paths", {})
    if not isinstance(paths, dict):
        return out
    for route, item in paths.items():
        if not isinstance(item, dict):
            continue
        for method in item:
            if isinstance(method, str) and method.lower() in _HTTP_METHODS:
                out.append((method.upper(), str(route)))
    return sorted(set(out))
