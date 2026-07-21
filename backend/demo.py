"""End-to-end offline demo: ingest, build the graph, index, search, and query.

Uses the in-memory backend and the deterministic embedder, so it runs with no
OpenSearch, Neo4j, or model-serving pod — proving the Phase 1-3 chain holds
together end to end. Not a production entrypoint (that serves FastAPI over
OpenSearch and Neo4j); this is the "does the whole chain hold together" check.

    python -m backend.demo <estate-root> -m manifest.yaml -q "how do refunds reverse"
    python -m backend.demo <estate-root> -m manifest.yaml --depends-on ledger
"""

from __future__ import annotations

import argparse
from pathlib import Path

from backend.domain.manifest import load_manifest
from backend.embedding import HashingEmbedder
from backend.graph import StructuralQueries, build_graph
from backend.retrieval import InMemorySearchBackend, RetrievalService, TokenOverlapReranker


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="vectural-demo", description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("-m", "--manifest", type=Path, default=None)
    parser.add_argument("-q", "--query", default="how does a refund reversal propagate")
    parser.add_argument("-n", "--top-n", type=int, default=5)
    parser.add_argument(
        "--depends-on",
        metavar="SERVICE",
        default=None,
        help="print the services that depend on SERVICE (structural, no LLM)",
    )
    parser.add_argument("--hops", type=int, default=3)
    args = parser.parse_args(argv)

    manifest_path = args.manifest or (args.root / "manifest.yaml")
    manifest = load_manifest(manifest_path.read_text(encoding="utf-8"))

    graph = build_graph(args.root, manifest, commit_sha="DEMO")
    print(
        f"built graph: {len(graph.chunks)} chunks, "
        f"{len(graph.nodes)} nodes, {len(graph.edges)} edges"
    )
    print(f"  nodes: {graph.counts_by_node_kind()}")
    print(f"  edges: {graph.counts_by_edge_kind()}\n")

    # Structural fast path (§5.5) — no gateway call.
    if args.depends_on:
        queries = StructuralQueries(graph.store())
        dependents = queries.service_dependents(args.depends_on, max_hops=args.hops)
        print(f'what depends on "{args.depends_on}" (<= {args.hops} hops):')
        if not dependents:
            print("  (nothing, or service not in graph)")
        for hop, services in dependents.items():
            print(f"  {hop} hop(s): {', '.join(services)}")
        print()

    # Hybrid retrieval (§5.3 steps 5-6).
    embedder = HashingEmbedder()
    backend = InMemorySearchBackend(embedder=embedder)
    backend.index(graph.chunks)
    service = RetrievalService(backend=backend, embedder=embedder, reranker=TokenOverlapReranker())

    print(f'query: "{args.query}"')
    for i, hit in enumerate(service.search(args.query, top_n=args.top_n), start=1):
        sym = hit.symbol or "—"
        print(f"  {i}. score={hit.score:.4f}  {hit.path}:{hit.span}  [{sym}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
