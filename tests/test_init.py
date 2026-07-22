"""Guided initial setup: stage wiring for clone → manifest → estimate."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend import init as init_mod
from backend.estate import Parent
from backend.estimator import EstimatorConfig
from backend.init import main, run_clone, run_estimate, run_manifest


def _tiny_estate(root: Path) -> None:
    (root / "svc-a").mkdir()
    (root / "svc-a" / "main.py").write_text("def handler():\n    return compute()\n")
    (root / "svc-b").mkdir()
    (root / "svc-b" / "app.ts").write_text("export const x = 1;\n")


def test_run_manifest_writes_manifest_then_draft(tmp_path: Path) -> None:
    _tiny_estate(tmp_path)
    path = run_manifest(tmp_path)
    assert path == tmp_path / "manifest.yaml"
    assert path.exists()

    # Re-running does not clobber the (possibly hand-edited) manifest.
    original = path.read_text()
    again = run_manifest(tmp_path)
    assert again == tmp_path / "manifest.yaml"
    assert path.read_text() == original
    assert (tmp_path / "manifest.draft.yaml").exists()


def test_run_estimate_reports(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _tiny_estate(tmp_path)
    manifest_path = run_manifest(tmp_path)
    run_estimate(tmp_path, manifest_path, EstimatorConfig())
    out = capsys.readouterr().out
    assert "Vectural index-cost estimate" in out
    assert "total gateway tokens" in out


def test_run_clone_passes_flags_through(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_clone_estate(root, parent, **kwargs):  # type: ignore[no-untyped-def]
        captured.update(root=root, parent=parent, **kwargs)
        return {"cloned": 2}

    monkeypatch.setattr(init_mod, "clone_estate", fake_clone_estate)
    run_clone(
        Path("/estate"), "https://github.com/acme",
        shallow=True, include_archived=True, dry_run=False,
    )
    assert captured["parent"] == Parent(host="github.com", owner="acme", provider="github")
    assert captured["shallow"] is True
    assert captured["include_archived"] is True


def test_main_skip_clone_runs_manifest_and_estimate(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _tiny_estate(tmp_path)
    rc = main(["--path", str(tmp_path), "--skip-clone"])
    assert rc == 0
    assert (tmp_path / "manifest.yaml").exists()
    assert "Vectural index-cost estimate" in capsys.readouterr().out


def test_main_skip_clone_rejects_missing_dir(tmp_path: Path) -> None:
    missing = tmp_path / "nope"
    assert main(["--path", str(missing), "--skip-clone"]) == 2


def test_main_source_only_flag_reaches_estimate(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _tiny_estate(tmp_path)
    (tmp_path / "svc-a" / "README.md").write_text("# docs\n" + "word " * 100)
    rc = main(["--path", str(tmp_path), "--skip-clone", "--source-only"])
    assert rc == 0
    # With --source-only the .md is excluded, so exactly one file is dropped.
    assert f"excluded files        {1:>12,}   (non-source" in capsys.readouterr().out
