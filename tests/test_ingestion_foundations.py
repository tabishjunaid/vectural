"""Ingestion-UI backend foundations: manifest git_url round-trip, single-repo
clone name derivation, and the per-repo model-aware cost estimate."""

from __future__ import annotations

from pathlib import Path

from backend.domain.manifest import (
    Manifest,
    ServiceManifest,
    dump_manifest,
    load_manifest,
    save_manifest,
)
from backend.estate import repo_name_from_url
from backend.ingestion.estimate import estimate_repo
from backend.llm import catalog
from backend.llm.catalog import SelectableModel

# ---- manifest git_url + save/round-trip ----------------------------------


def test_git_url_round_trips_through_dump_and_load() -> None:
    m = Manifest(
        services=[
            ServiceManifest(name="payments", path="payments", git_url="https://git/acme/payments.git"),
            ServiceManifest(name="ledger", path="ledger"),  # no url — hand-authored
        ]
    )
    reloaded = load_manifest(dump_manifest(m))
    by_name = {s.name: s for s in reloaded.services}
    assert by_name["payments"].git_url == "https://git/acme/payments.git"
    assert by_name["ledger"].git_url is None


def test_save_manifest_writes_valid_yaml(tmp_path: Path) -> None:
    path = tmp_path / "manifest.yaml"
    save_manifest(Manifest(services=[ServiceManifest(name="svc", path="svc")]), path)
    assert load_manifest(path.read_text()).services[0].name == "svc"


# ---- single-repo clone name derivation ------------------------------------


def test_repo_name_from_url_variants() -> None:
    assert repo_name_from_url("https://github.com/acme/payments.git") == "payments"
    assert repo_name_from_url("https://github.com/acme/payments") == "payments"
    assert repo_name_from_url("https://github.com/acme/payments/") == "payments"
    assert repo_name_from_url("git@github.com:acme/payments.git") == "payments"


# ---- per-repo estimate + model-aware cost ---------------------------------


def _write_repo(root: Path, name: str, body: str) -> None:
    d = root / name
    d.mkdir(parents=True)
    (d / "main.py").write_text(body)


def test_estimate_repo_scopes_to_one_service_and_prices_by_model(tmp_path: Path) -> None:
    _write_repo(tmp_path, "alpha", "def a():\n    return 1\n" * 40)
    _write_repo(tmp_path, "beta", "def b():\n    return 2\n" * 40)
    manifest = Manifest(
        services=[
            ServiceManifest(name="alpha", path="alpha"),
            ServiceManifest(name="beta", path="beta"),
        ]
    )
    # Only 'alpha' is walked → its files, not beta's.
    est = estimate_repo(tmp_path, manifest, "alpha", model="gpt-4o-mini")
    assert [s["service"] for s in est["services"]] == ["alpha"]  # type: ignore[index]
    assert est["totals"]["files"] >= 1  # type: ignore[index]
    # A priced cloud model → a positive dollar figure.
    assert isinstance(est["cost_usd"], float) and est["cost_usd"] > 0.0

    # A local Ollama model prices at $0 (nothing leaves the machine).
    catalog.register_dynamic_models([SelectableModel("q7b", "q7b", "ollama", "q7b", 8192)])
    local = estimate_repo(tmp_path, manifest, "alpha", model="q7b")
    assert local["cost_usd"] == 0.0


def test_estimate_repo_unknown_service_raises(tmp_path: Path) -> None:
    manifest = Manifest(services=[ServiceManifest(name="alpha", path="alpha")])
    try:
        estimate_repo(tmp_path, manifest, "nope", model="gpt-4o")
    except KeyError:
        return
    raise AssertionError("expected KeyError for unknown service")
