"""Clone-estate stage: parent URL parsing + clone/update planning (§4.4)."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.estate import Parent, Repo, clone_or_update, parse_parent


def test_parse_github_org() -> None:
    p = parse_parent("https://github.com/acme")
    assert p == Parent(host="github.com", owner="acme", provider="github")


def test_parse_github_orgs_web_path_is_normalised() -> None:
    # The "orgs/<name>" web URL collapses to just the owner.
    assert parse_parent("https://github.com/orgs/acme").owner == "acme"


def test_parse_adds_scheme_and_strips_trailing_slash() -> None:
    assert parse_parent("github.com/acme/") == parse_parent("https://github.com/acme")


def test_parse_gitlab_subgroup() -> None:
    p = parse_parent("https://gitlab.com/acme/platform")
    assert p.provider == "gitlab"
    assert p.owner == "acme/platform"


def test_parse_self_hosted_gitlab() -> None:
    assert parse_parent("https://gitlab.acme.com/team").provider == "gitlab"


def test_parse_rejects_unsupported_host() -> None:
    with pytest.raises(ValueError, match="unsupported host"):
        parse_parent("https://bitbucket.org/acme")


def test_parse_rejects_missing_owner() -> None:
    with pytest.raises(ValueError, match="could not parse owner"):
        parse_parent("https://github.com")


def test_clone_or_update_dry_run_reports_without_touching_disk(tmp_path: Path) -> None:
    repo = Repo(name="svc", clone_url="https://github.com/acme/svc.git")
    assert clone_or_update(repo, tmp_path, shallow=False, dry_run=True) == "would clone"
    assert not (tmp_path / "svc").exists()  # nothing cloned


def test_clone_or_update_updates_existing_checkout_in_dry_run(tmp_path: Path) -> None:
    (tmp_path / "svc" / ".git").mkdir(parents=True)
    repo = Repo(name="svc", clone_url="https://github.com/acme/svc.git")
    assert clone_or_update(repo, tmp_path, shallow=False, dry_run=True) == "would update"


def test_clone_or_update_skips_non_git_directory(tmp_path: Path) -> None:
    (tmp_path / "svc").mkdir()  # exists but no .git
    repo = Repo(name="svc", clone_url="https://github.com/acme/svc.git")
    out = clone_or_update(repo, tmp_path, shallow=False, dry_run=False)
    assert out == "skipped (exists, not a git repo)"
