"""Runnable-app bootstrap + HTTP endpoints (integration serving path)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.bootstrap import build_services

ESTATE = Path("sample-estate")
MANIFEST = ESTATE / "manifest.yaml"

pytestmark = pytest.mark.skipif(
    not MANIFEST.is_file(), reason="sample-estate not present"
)


@pytest.fixture(scope="module")
def client() -> TestClient:
    services = build_services(ESTATE, MANIFEST, summarise_on_boot=True)
    return TestClient(services.app)


def test_healthz(client: TestClient) -> None:
    assert client.get("/healthz").json()["status"] == "ok"


def test_coverage_shape_matches_frontend(client: TestClient) -> None:
    rows = client.get("/coverage").json()
    assert rows
    row = rows[0]
    # camelCase keys the React CoverageRow consumes.
    assert {"service", "tier", "tierLabel", "lastIndexed", "nextScheduled", "status"} <= set(row)
    assert row["status"] in {"indexed", "partial", "not-indexed"}


def test_ask_returns_cited_answer(client: TestClient) -> None:
    resp = client.post(
        "/ask",
        json={
            "question": "how does the gateway charge via payments and ledger",
            "persona": "engineer",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] in {"synthesized", "instant", "refusal"}
    if body["mode"] != "refusal":
        assert body["citations"]
        assert body["citations"][0]["chunk_id"]


def test_review_approve_bumps_coverage_to_tier4(client: TestClient) -> None:
    queue = client.get("/review/queue").json()
    assert queue
    flow = next(f for f in queue if "gateway" in f["services"])

    approved = client.post(
        f"/review/{flow['id']}/approve", json={"architect": "A. Chen", "persona": "architect"}
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"

    # The approved flow's services are now tier 4 in coverage (§5.4 one source).
    coverage = {r["service"]: r for r in client.get("/coverage").json()}
    assert coverage["gateway"]["tier"] == 4
    assert coverage["gateway"]["status"] == "indexed"


def test_review_approve_is_architect_only(client: TestClient) -> None:
    queue = client.get("/review/queue").json()
    if not queue:
        pytest.skip("queue drained by prior test")
    resp = client.post(
        f"/review/{queue[0]['id']}/approve", json={"architect": "X", "persona": "engineer"}
    )
    assert resp.status_code == 403


def test_metrics_endpoint(client: TestClient) -> None:
    client.post("/ask", json={"question": "what does payments call", "persona": "po"})
    snap = client.get("/metrics").json()
    assert snap["total_calls"] > 0
    assert "tokens_by_task" in snap
