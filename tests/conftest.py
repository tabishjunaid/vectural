"""Shared fixtures: a small synthetic estate on disk plus its manifest.

Everything here is offline — no gateway, no datastore — matching the Phase 1
property that the deterministic pipeline is fully testable in isolation.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.domain.manifest import Manifest, load_manifest
from backend.embedding import HashingEmbedder
from backend.graph import StructuralQueries, build_graph
from backend.graph.builder import GraphBuildResult
from backend.ingestion.pipeline import IngestionResult, ingest_tree
from backend.retrieval import (
    InMemorySearchBackend,
    RetrievalService,
    TokenOverlapReranker,
)

MANIFEST_YAML = """
services:
  - name: payments-api
    path: payments-api
    language: python
    art: payments-art
    criticality: tier-1
    owner: payments-team
  - name: ledger-svc
    path: ledger-svc
    language: typescript
  - name: booking-api
    path: booking-api
    language: java
"""

# path (relative to estate root) -> file bytes
ESTATE_FILES: dict[str, bytes] = {
    "payments-api/refund.py": b"""import os

@route(\"/refunds\")
def reverse_refund(refund_id):
    refund = repo.get(refund_id)
    return publish(\"payments.events\", refund_id)


class RefundRepo:
    def get(self, refund_id):
        return self.db.find(refund_id)
""",
    "payments-api/util/helpers.py": b"""def clamp(value, lo, hi):
    return max(lo, min(value, hi))
""",
    "ledger-svc/index.ts": b"""import { db } from \"./db\";

export const applyCharge = async (amount: number): Promise<void> => {
  await db.save(amount);
};

export class Ledger {
  reverse(id: string) {
    return this.repo.reverse(id);
  }
}
""",
    "booking-api/src/RefundHandler.java": b"""package com.acme;

public class RefundHandler {
    public void reverse(String refundId) {
        publisher.publish(new RefundReversed(refundId));
    }
}
""",
    # Not in any manifested service -> must be ignored by the walker.
    "unmanifested-svc/secret.py": b"def leak():\n    return 1\n",
    # Ignored directory content -> must be pruned.
    "payments-api/node_modules/dep/index.js": b"module.exports = 1;\n",
    # Binary file -> must be skipped.
    "payments-api/logo.png": b"\x89PNG\x00\x00binary",
}


# --------------------------------------------------------------------------- #
# A second estate purpose-built for graph tests: explicit cross-service calls,
# messaging topics, and an OpenAPI spec. Kept separate so the Phase 1/2 estate
# above keeps its exact chunk/file expectations.
# --------------------------------------------------------------------------- #

GRAPH_MANIFEST_YAML = """
services:
  - name: gateway
    path: gateway
    language: python
  - name: payments
    path: payments
    language: python
  - name: ledger
    path: ledger
    language: python
  - name: notifications
    path: notifications
    language: python
"""

GRAPH_ESTATE_FILES: dict[str, bytes] = {
    # gateway -> payments (calls charge, defined in payments)
    "gateway/api.py": b"""def handle_request(req):
    return charge(req.amount)
""",
    # payments -> ledger (calls applyCharge) and publishes a topic
    "payments/pay.py": b"""def charge(amount):
    applyCharge(amount)
    publish("payments.events", amount)
    return True
""",
    "payments/openapi.yaml": b"""openapi: 3.0.0
info:
  title: payments
  version: 1.0.0
paths:
  /charge:
    post:
      summary: charge a customer
  /refunds/{id}/reverse:
    post:
      summary: reverse a refund
""",
    # ledger defines the sink symbol
    "ledger/ledger.py": b"""def applyCharge(amount):
    return _save(amount)


def _save(amount):
    return amount
""",
    # notifications consumes the topic payments publishes
    "notifications/notify.py": b"""def worker():
    subscribe("payments.events", on_event)


def on_event(evt):
    return evt
""",
}


@pytest.fixture
def manifest() -> Manifest:
    return load_manifest(MANIFEST_YAML)


@pytest.fixture
def graph_manifest() -> Manifest:
    return load_manifest(GRAPH_MANIFEST_YAML)


@pytest.fixture
def graph_estate(tmp_path: Path) -> Path:
    for rel, content in GRAPH_ESTATE_FILES.items():
        dest = tmp_path / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content)
    return tmp_path


@pytest.fixture
def graph_build(graph_estate: Path, graph_manifest: Manifest) -> GraphBuildResult:
    return build_graph(graph_estate, graph_manifest, commit_sha="c0ffee")


@pytest.fixture
def structural(graph_build: GraphBuildResult) -> StructuralQueries:
    return StructuralQueries(graph_build.store())


@pytest.fixture
def estate(tmp_path: Path) -> Path:
    for rel, content in ESTATE_FILES.items():
        dest = tmp_path / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content)
    return tmp_path


@pytest.fixture
def ingested(estate: Path, manifest: Manifest) -> IngestionResult:
    return ingest_tree(estate, manifest, commit_sha="c0ffee")


@pytest.fixture
def retrieval_service(ingested: IngestionResult) -> RetrievalService:
    """A fully offline retrieval service over the synthetic estate: hashing
    embedder + in-memory hybrid backend + token-overlap reranker."""
    embedder = HashingEmbedder()
    backend = InMemorySearchBackend(embedder=embedder)
    backend.index(ingested.chunks)
    return RetrievalService(
        backend=backend, embedder=embedder, reranker=TokenOverlapReranker()
    )
