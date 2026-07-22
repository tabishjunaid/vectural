"""Index-cost / token-usage estimator (§Phase 0, §5.2.1)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.domain.manifest import Manifest
from backend.domain.models import Language
from backend.estimator import (
    EstimatorConfig,
    count_tokens,
    derive_manifest,
    estimate_estate,
    load_calibration,
    resolve_manifest,
    write_derived_manifest,
)


def test_count_tokens_heuristic() -> None:
    assert count_tokens("", 3.5) == 0
    assert count_tokens("a" * 35, 3.5) == 10
    assert count_tokens("abc", 2.0) == 2  # ceil(3/2)


def test_estimate_over_estate(estate: Path, manifest: Manifest) -> None:
    est = estimate_estate(estate, manifest)
    services = {s.service for s in est.services}
    assert {"payments-api", "ledger-svc", "booking-api"} <= services
    assert est.total_files > 0
    assert est.total_chunks > 0
    assert est.total_embed_tokens > 0
    assert est.total_gateway_tokens > 0


def test_instruction_overhead_is_per_file(estate: Path, manifest: Manifest) -> None:
    cfg = EstimatorConfig(prompt_overhead_tokens=100)
    est = estimate_estate(estate, manifest, cfg)
    # Overhead is billed once per file (§5.2.1).
    assert est.total_instruction_overhead == est.total_files * 100
    assert 0.0 < est.overhead_fraction < 1.0


def test_weekly_plan_schedules_all_services(estate: Path, manifest: Manifest) -> None:
    est = estimate_estate(estate, manifest, EstimatorConfig(monthly_budget=100_000_000))
    scheduled = {svc for services in est.weekly_plan().values() for svc in services}
    assert {s.service for s in est.services if s.files > 0} <= scheduled


def test_tiny_budget_leaves_services_unscheduled(estate: Path, manifest: Manifest) -> None:
    # A service costing more than a whole weekly tranche can't be packed.
    est = estimate_estate(estate, manifest, EstimatorConfig(monthly_budget=8))
    assert -1 in est.weekly_plan()  # the "unscheduled / over budget" bucket


def test_oversized_chunk_flagged(estate: Path, manifest: Manifest) -> None:
    cfg = EstimatorConfig(embed_context_limit=1)  # force everything oversized
    est = estimate_estate(estate, manifest, cfg)
    assert est.total_oversized > 0


def test_as_dict_is_json_serialisable(estate: Path, manifest: Manifest) -> None:
    payload = estimate_estate(estate, manifest).as_dict()
    text = json.dumps(payload)  # must not raise
    assert "totals" in json.loads(text)


def test_derive_manifest_from_dirs(tmp_path: Path) -> None:
    (tmp_path / "svc-a").mkdir()
    (tmp_path / "svc-a" / "main.py").write_text("def f(): pass\n")
    (tmp_path / "svc-b").mkdir()
    (tmp_path / "svc-b" / "app.ts").write_text("export const x = 1;\n")
    (tmp_path / ".git").mkdir()  # ignored
    (tmp_path / "node_modules").mkdir()  # ignored

    manifest = derive_manifest(tmp_path)
    services = {s.name: s for s in manifest.services}
    assert set(services) == {"svc-a", "svc-b"}  # hidden/ignored dirs excluded
    assert services["svc-a"].language is Language.PYTHON  # guessed from extensions
    assert services["svc-b"].language is Language.TYPESCRIPT


def test_derive_manifest_errors_on_empty(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="no service directories"):
        derive_manifest(tmp_path)


def test_resolve_manifest_falls_back_to_derivation(tmp_path: Path) -> None:
    (tmp_path / "svc").mkdir()
    (tmp_path / "svc" / "m.py").write_text("x = 1\n")

    # No manifest present → derived.
    manifest, derived = resolve_manifest(tmp_path, None)
    assert derived is True
    assert [s.name for s in manifest.services] == ["svc"]

    # A real manifest.yaml at the root is used verbatim (not derived).
    (tmp_path / "manifest.yaml").write_text("services:\n  - name: svc\n    path: svc\n")
    _manifest2, derived2 = resolve_manifest(tmp_path, None)
    assert derived2 is False


def test_estimate_works_without_a_manifest(tmp_path: Path) -> None:
    # The "just cloned, no manifest yet" case: derive, then estimate.
    (tmp_path / "svc-a").mkdir()
    (tmp_path / "svc-a" / "main.py").write_text("def handler():\n    return compute()\n")
    manifest, _ = resolve_manifest(tmp_path, None)
    est = estimate_estate(tmp_path, manifest)
    assert est.total_files == 1
    assert est.total_gateway_tokens > 0


def _mixed_estate(tmp_path: Path) -> tuple[Path, Manifest]:
    (tmp_path / "svc").mkdir()
    (tmp_path / "svc" / "app.py").write_text("def handler():\n    return compute()\n")
    (tmp_path / "svc" / "README.md").write_text("# docs\n" + "words " * 200)
    (tmp_path / "svc" / "data.json").write_text('{"k": ' + "1," * 500 + "0}")
    (tmp_path / "svc" / "yarn.lock").write_text("lockfile " * 300)
    manifest, _ = resolve_manifest(tmp_path, None)
    return tmp_path, manifest


def test_source_only_counts_only_recognised_languages(tmp_path: Path) -> None:
    root, manifest = _mixed_estate(tmp_path)
    full = estimate_estate(root, manifest)
    src = estimate_estate(root, manifest, EstimatorConfig(source_only=True))
    assert full.total_files == 4  # py + md + json + lock
    assert src.total_files == 1  # only app.py
    assert src.excluded_files == 3
    assert src.total_gateway_tokens < full.total_gateway_tokens  # docs/data no longer counted


def test_exclude_suffixes(tmp_path: Path) -> None:
    root, manifest = _mixed_estate(tmp_path)
    cfg = EstimatorConfig(exclude_suffixes=frozenset({".md", ".json", ".lock"}))
    est = estimate_estate(root, manifest, cfg)
    assert est.total_files == 1
    assert est.excluded_files == 3


def test_exclude_glob(tmp_path: Path) -> None:
    (tmp_path / "svc").mkdir()
    (tmp_path / "svc" / "app.py").write_text("x = 1\n")
    (tmp_path / "svc" / "app.min.js").write_text("var a=1;\n")
    manifest, _ = resolve_manifest(tmp_path, None)
    est = estimate_estate(root=tmp_path, manifest=manifest, config=EstimatorConfig(
        exclude_globs=("*.min.js",)
    ))
    assert est.excluded_files == 1
    assert est.total_files == 1


def test_write_manifest_creates_and_does_not_clobber(tmp_path: Path) -> None:
    (tmp_path / "svc-a").mkdir()
    (tmp_path / "svc-a" / "main.py").write_text("x = 1\n")

    # No manifest yet → writes manifest.yaml.
    path, was_draft = write_derived_manifest(tmp_path)
    assert path == tmp_path / "manifest.yaml"
    assert was_draft is False
    assert "svc-a" in path.read_text()

    # Manifest exists → writes a non-clobbering draft, leaving the original intact.
    original = (tmp_path / "manifest.yaml").read_text()
    (tmp_path / "svc-b").mkdir()
    (tmp_path / "svc-b" / "app.ts").write_text("export const x = 1;\n")
    draft_path, was_draft2 = write_derived_manifest(tmp_path)
    assert draft_path == tmp_path / "manifest.draft.yaml"
    assert was_draft2 is True
    assert (tmp_path / "manifest.yaml").read_text() == original  # untouched
    assert "svc-b" in draft_path.read_text()  # draft reflects the new dir


def test_calibration_overrides_defaults(tmp_path: Path) -> None:
    calib = tmp_path / "phase4.json"
    calib.write_text(json.dumps({"chars_per_token": 4.2, "prompt_overhead_tokens": 400}))
    cfg = load_calibration(calib, EstimatorConfig())
    assert cfg.chars_per_token == 4.2
    assert cfg.prompt_overhead_tokens == 400
    assert cfg.tier1_output_tokens == EstimatorConfig().tier1_output_tokens  # untouched
