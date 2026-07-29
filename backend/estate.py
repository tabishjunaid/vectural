#!/usr/bin/env python3
"""Clone every repository under a Git parent (org / group) into one root.

Stage 1 of estate setup (implementation-plan §4.4: "All repositories under one
root, one directory per repository"). Enumerates every project under a Git
parent and clones each into ``<root>/<repo-name>`` — pulling instead of failing
if it already exists, so re-runs are idempotent.

Supported parents (auto-detected from the URL host):
  * GitHub  — via the `gh` CLI if present, else the REST API (set GITHUB_TOKEN
              for private repos / higher rate limits).
  * GitLab  — via the REST API, including subgroups (set GITLAB_TOKEN for
              private groups). Works for gitlab.com and self-hosted.

Used both as a standalone CLI (``vectural-clone``) and as the first stage of the
guided setup (``vectural-init`` — see :mod:`backend.init`).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Repo:
    name: str  # local directory name (last path segment)
    clone_url: str
    archived: bool = False


@dataclass(frozen=True)
class Parent:
    host: str  # e.g. github.com, gitlab.com, gitlab.acme.com
    owner: str  # org / user / group path (may contain '/')
    provider: str  # "github" | "gitlab"


# --------------------------------------------------------------------------- #
# Parent URL parsing + enumeration
# --------------------------------------------------------------------------- #


def parse_parent(url: str) -> Parent:
    raw = url.strip().rstrip("/")
    if "://" not in raw:
        raw = "https://" + raw
    parts = urllib.parse.urlparse(raw)
    host = parts.netloc
    owner = parts.path.strip("/")
    # Normalise GitHub "orgs/<name>" web paths to just the owner.
    if owner.startswith("orgs/"):
        owner = owner[len("orgs/"):]
    if not host or not owner:
        raise ValueError(f"could not parse owner/group from URL: {url!r}")
    provider = "github" if host == "github.com" else "gitlab" if "gitlab" in host else ""
    if not provider:
        raise ValueError(
            f"unsupported host {host!r}; supported: github.com and *gitlab* hosts"
        )
    return Parent(host=host, owner=owner, provider=provider)


def enumerate_repos(parent: Parent) -> list[Repo]:
    if parent.provider == "github":
        return _github_repos(parent)
    return _gitlab_repos(parent)


def _github_repos(parent: Parent) -> list[Repo]:
    if _have("gh"):
        return _github_repos_via_gh(parent)
    return _github_repos_via_api(parent)


def _github_repos_via_gh(parent: Parent) -> list[Repo]:
    out = subprocess.run(
        ["gh", "repo", "list", parent.owner, "--limit", "4000",
         "--json", "name,isArchived,sshUrl,url"],
        capture_output=True, text=True, check=True,
    )
    repos: list[Repo] = []
    for item in json.loads(out.stdout or "[]"):
        name = str(item["name"])
        repos.append(Repo(
            name=name,
            clone_url=f"https://{parent.host}/{parent.owner}/{name}.git",
            archived=bool(item.get("isArchived")),
        ))
    return repos


def _github_repos_via_api(parent: Parent) -> list[Repo]:
    token = os.environ.get("GITHUB_TOKEN")
    repos: list[Repo] = []
    for kind in ("orgs", "users"):  # try org first, fall back to user
        page = 1
        found_any = False
        while True:
            url = f"https://api.github.com/{kind}/{parent.owner}/repos?per_page=100&page={page}"
            data = _get_json(url, headers=_gh_headers(token))
            if not data:  # None (404 for this kind) or empty page
                break
            found_any = True
            for item in data:
                repos.append(Repo(name=str(item["name"]), clone_url=str(item["clone_url"]),
                                   archived=bool(item.get("archived"))))
            page += 1
        if found_any:
            break
    if not repos:
        raise RuntimeError(
            f"no repos found for {parent.owner!r} on GitHub "
            "(private? set GITHUB_TOKEN, or install the `gh` CLI)"
        )
    return repos


def _gh_headers(token: str | None) -> dict[str, str]:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "vectural-clone"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _gitlab_repos(parent: Parent) -> list[Repo]:
    token = os.environ.get("GITLAB_TOKEN")
    headers = {"User-Agent": "vectural-clone"}
    if token:
        headers["PRIVATE-TOKEN"] = token
    group = urllib.parse.quote(parent.owner, safe="")
    repos: list[Repo] = []
    page = 1
    while True:
        url = (
            f"https://{parent.host}/api/v4/groups/{group}/projects"
            f"?include_subgroups=true&archived=false&per_page=100&page={page}"
        )
        data = _get_json(url, headers=headers)
        if not data:
            break
        for item in data:
            repos.append(Repo(
                name=str(item["path"]),
                clone_url=str(item["http_url_to_repo"]),
                archived=bool(item.get("archived")),
            ))
        page += 1
    if not repos:
        raise RuntimeError(
            f"no projects found for group {parent.owner!r} on {parent.host} "
            "(private? set GITLAB_TOKEN)"
        )
    return repos


def _get_json(url: str, *, headers: dict[str, str]) -> list[dict[str, Any]] | None:
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload: Any = json.loads(resp.read().decode("utf-8"))
            return payload if isinstance(payload, list) else []
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise RuntimeError(f"API error {exc.code} for {url}: {exc.reason}") from exc


# --------------------------------------------------------------------------- #
# Cloning
# --------------------------------------------------------------------------- #


def clone_or_update(repo: Repo, root: Path, *, shallow: bool, dry_run: bool) -> str:
    dest = root / repo.name
    if dest.exists():
        if not (dest / ".git").is_dir():
            return "skipped (exists, not a git repo)"
        if dry_run:
            return "would update"
        ok = _run_git(["-C", str(dest), "remote", "update", "--prune"])
        return "updated" if ok else "update failed"
    if dry_run:
        return "would clone"
    cmd = ["clone", "--quiet"]
    if shallow:
        cmd += ["--depth", "1"]
    cmd += [repo.clone_url, str(dest)]
    return "cloned" if _run_git(cmd) else "clone failed"


def repo_name_from_url(url: str) -> str:
    """The local directory / service name for a Git URL: the last path segment,
    minus a trailing ``.git``. Handles https and scp-style (``git@host:owner/repo``)."""
    tail = url.strip().rstrip("/").rsplit("/", 1)[-1]
    tail = tail.rsplit(":", 1)[-1]  # scp-style with no path, e.g. git@host:repo.git
    if tail.endswith(".git"):
        tail = tail[:-4]
    return tail


def clone_repo_url(
    url: str, root: Path, *, shallow: bool = True, dry_run: bool = False
) -> tuple[str, str]:
    """Clone a single repo by URL into ``root/<name>``. Returns ``(name, outcome)``.

    The Ingestion UI's "add repo by URL" path — bypasses parent enumeration and
    reuses :func:`clone_or_update`. Shallow by default (indexing only needs the
    current tree, not history)."""
    name = repo_name_from_url(url)
    if not name:
        raise ValueError(f"could not derive a repo name from URL {url!r}")
    repo = Repo(name=name, clone_url=url)
    outcome = clone_or_update(repo, root, shallow=shallow, dry_run=dry_run)
    return name, outcome


def clone_estate(
    root: Path,
    parent: Parent,
    *,
    shallow: bool = False,
    include_archived: bool = False,
    dry_run: bool = False,
    on_progress: Any = None,
) -> dict[str, int]:
    """Clone/update every repo under ``parent`` into ``root``; return an outcome tally.

    ``on_progress`` (optional) is called ``(repo_name, outcome)`` after each repo so
    callers can stream progress. Enumeration errors propagate to the caller."""
    root.mkdir(parents=True, exist_ok=True)
    repos = enumerate_repos(parent)
    if not include_archived:
        repos = [r for r in repos if not r.archived]
    tally: dict[str, int] = {}
    for repo in sorted(repos, key=lambda r: r.name):
        outcome = clone_or_update(repo, root, shallow=shallow, dry_run=dry_run)
        tally[outcome] = tally.get(outcome, 0) + 1
        if on_progress is not None:
            on_progress(repo.name, outcome)
    return tally


def _run_git(args: list[str]) -> bool:
    return subprocess.run(["git", *args], capture_output=True, text=True).returncode == 0


def _have(binary: str) -> bool:
    return subprocess.run(["which", binary], capture_output=True).returncode == 0


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _prompt(label: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default else ""
    while True:
        value = input(f"{label}{suffix}: ").strip()
        if value:
            return value
        if default is not None:
            return default


def clone_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--path", help="root folder to clone into (prompted if omitted)")
    parser.add_argument("--parent", help="Git parent URL: org/group (prompted if omitted)")
    parser.add_argument("--shallow", action="store_true", help="shallow clone (--depth 1)")
    parser.add_argument(
        "--include-archived", action="store_true", help="also clone archived repos"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="list what would happen, clone nothing"
    )
    args = parser.parse_args(argv)

    path = args.path or _prompt("Path to clone into")
    parent_url = args.parent or _prompt("Git parent URL (org / group)")

    try:
        parent = parse_parent(parent_url)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    root = Path(path).expanduser().resolve()
    print(f"Enumerating repositories under {parent.owner} on {parent.host} ({parent.provider})…")
    try:
        tally = clone_estate(
            root, parent,
            shallow=args.shallow, include_archived=args.include_archived, dry_run=args.dry_run,
            on_progress=lambda name, outcome: print(f"  {name:40} {outcome}"),
        )
    except (RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print("\nSummary: " + ", ".join(f"{v} {k}" for k, v in sorted(tally.items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(clone_main())
