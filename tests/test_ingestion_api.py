"""Ingestion HTTP endpoints (read-only, against the sample estate). Mutating
endpoints (add/drop) are covered in isolation by test_ingestion_service.py so the
shared sample-estate manifest is never rewritten here."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.bootstrap import build_services

ESTATE = Path("sample-estate")
MANIFEST = ESTATE / "manifest.yaml"

pytestmark = pytest.mark.skipif(not MANIFEST.is_file(), reason="sample-estate not present")


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(build_services(ESTATE, MANIFEST, summarise_on_boot=True).app)


def test_ingest_list_reports_repos(client: TestClient) -> None:
    repos = client.get("/ingest/repos").json()
    assert repos
    row = repos[0]
    assert {"service", "path", "indexed", "chunks", "summary_tier", "phase"} <= set(row)
    # The sample estate is indexed on boot, so at least one repo is searchable.
    assert any(r["indexed"] for r in repos)


def test_ingest_estimate_is_model_aware(client: TestClient) -> None:
    svc = client.get("/ingest/repos").json()[0]["service"]
    est = client.post(f"/ingest/repos/{svc}/estimate", json={"model": "gpt-4o-mini"}).json()
    assert "totals" in est and "gateway_tokens" in est["totals"]
    assert est["model"] == "gpt-4o-mini"
    # A priced cloud model yields a dollar figure (embedding is local/$0).
    assert isinstance(est["cost_usd"], (int, float))


def test_ingest_estimate_unknown_repo_404(client: TestClient) -> None:
    assert client.post("/ingest/repos/nope-xyz/estimate", json={}).status_code == 404
