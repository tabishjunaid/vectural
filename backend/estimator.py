"""Token-usage / index-cost estimator (implementation-plan §Phase 0, §5.2.1).

Parses the estate (walk → classify → chunk, **zero gateway tokens**) and
estimates the token workload of indexing it:

- **embedding** ("create vectors"): how many chunks / tokens BGE-M3 must embed,
  and which chunks overflow the model's context window
- **summarisation** (gateway-billed): tiers 1-3 input + output tokens, with the
  fixed **instruction overhead broken out separately** — at tier 1 that overhead
  is multiplied by file count and is ~12-15% of first-pass cost (§5.2.1)

It then bin-packs per-service cost into weekly tranches (§5.7) to produce the
indexing plan the quota governor paces against. Token counts use a
``chars_per_token`` heuristic; a Phase 4 ``--calibration`` file replaces it with
measured figures. This is the ``prompt_overhead_tokens`` lever's home.
"""

from __future__ import annotations

import fnmatch
import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath

from backend.domain.manifest import Manifest, load_manifest
from backend.domain.models import Language
from backend.ingestion.chunker import chunk_source
from backend.ingestion.classify import classify_path
from backend.ingestion.walker import DEFAULT_IGNORE_DIRS, walk_estate
from backend.quota.governor import bin_pack
from backend.quota.ledger import QuotaConfig


@dataclass(frozen=True)
class EstimatorConfig:
    chars_per_token: float = 3.5  # code is denser than prose; tune in Phase 4
    prompt_overhead_tokens: int = 120  # fixed tier-1 instruction template (§5.2.1)
    tier1_output_tokens: int = 150  # per file summary
    tier2_output_tokens: int = 200  # per module summary
    tier3_output_tokens: int = 350  # per service summary
    embed_context_limit: int = 8192  # BGE-M3 context window
    monthly_budget: int = 50_000_000
    serving_reserve_fraction: float = 0.30
    tranche_count: int = 4
    # Scope controls — exclude non-source so the estimate reflects code, not docs
    # / config / data / lockfiles (which inflate the token count).
    source_only: bool = False  # only files with a recognised source language
    exclude_suffixes: frozenset[str] = frozenset()  # e.g. {".md", ".json", ".lock"}
    exclude_globs: tuple[str, ...] = ()  # fnmatch over the repo-relative path

    def includes(self, path: str, language: Language) -> bool:
        suffix = PurePosixPath(path).suffix.lower()
        if suffix in self.exclude_suffixes:
            return False
        if self.source_only and language is Language.UNKNOWN:
            return False
        return not any(fnmatch.fnmatch(path, pattern) for pattern in self.exclude_globs)


@dataclass
class ServiceEstimate:
    service: str
    files: int = 0
    chunks: int = 0
    modules: int = 0
    embed_tokens: int = 0  # sum of chunk content tokens (vector creation input)
    oversized_chunks: int = 0  # chunks exceeding the embed context limit
    tier1_input: int = 0
    tier1_overhead: int = 0  # the instruction-overhead share (§5.2.1)
    tier1_output: int = 0
    tier2_tokens: int = 0
    tier3_tokens: int = 0

    @property
    def summary_gateway_tokens(self) -> int:
        return self.tier1_input + self.tier1_output + self.tier2_tokens + self.tier3_tokens


@dataclass
class EstateEstimate:
    config: EstimatorConfig
    services: list[ServiceEstimate] = field(default_factory=list)
    excluded_files: int = 0  # files skipped by --source-only / --exclude(-glob)

    @property
    def total_files(self) -> int:
        return sum(s.files for s in self.services)

    @property
    def total_chunks(self) -> int:
        return sum(s.chunks for s in self.services)

    @property
    def total_embed_tokens(self) -> int:
        return sum(s.embed_tokens for s in self.services)

    @property
    def total_oversized(self) -> int:
        return sum(s.oversized_chunks for s in self.services)

    @property
    def total_instruction_overhead(self) -> int:
        return sum(s.tier1_overhead for s in self.services)

    @property
    def total_gateway_tokens(self) -> int:
        return sum(s.summary_gateway_tokens for s in self.services)

    @property
    def overhead_fraction(self) -> float:
        tier1 = sum(s.tier1_input for s in self.services)
        return self.total_instruction_overhead / tier1 if tier1 else 0.0

    def weekly_plan(self) -> dict[int, list[str]]:
        cfg = QuotaConfig(
            self.config.monthly_budget,
            self.config.serving_reserve_fraction,
            self.config.tranche_count,
        )
        items = [(s.service, s.summary_gateway_tokens) for s in self.services]
        result = bin_pack(items, cfg.weekly_tranche, cfg.tranche_count)
        plan = {week: services for week, services in result.schedule.items()}
        if result.unscheduled:
            plan[-1] = result.unscheduled  # over-budget: rolls to a later month
        return plan

    def as_dict(self) -> dict[str, object]:
        config = asdict(self.config)
        config["exclude_suffixes"] = sorted(config["exclude_suffixes"])  # frozenset → JSON list
        config["exclude_globs"] = list(config["exclude_globs"])
        return {
            "config": config,
            "totals": {
                "files": self.total_files,
                "excluded_files": self.excluded_files,
                "chunks": self.total_chunks,
                "embed_tokens": self.total_embed_tokens,
                "oversized_chunks": self.total_oversized,
                "instruction_overhead_tokens": self.total_instruction_overhead,
                "overhead_fraction": round(self.overhead_fraction, 4),
                "gateway_tokens": self.total_gateway_tokens,
                "fits_monthly_indexing_budget": self.total_gateway_tokens
                <= QuotaConfig(
                    self.config.monthly_budget, self.config.serving_reserve_fraction
                ).indexing_budget,
            },
            "services": [asdict(s) for s in self.services],
            "weekly_plan": {str(k): v for k, v in self.weekly_plan().items()},
        }


def count_tokens(text: str, chars_per_token: float) -> int:
    return math.ceil(len(text) / chars_per_token) if text else 0


def derive_manifest(root: Path) -> Manifest:
    """Build a manifest from the estate's top-level directories — one service per
    directory (design §4.4 layout: one directory per repository).

    Used when a freshly-cloned estate has no ``manifest.yaml`` yet. The result is
    a best-effort draft (language guessed from file extensions); the real manifest
    is human-reviewed (§3.4)."""
    services: list[dict[str, str]] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir() or child.name.startswith(".") or child.name in DEFAULT_IGNORE_DIRS:
            continue
        entry = {"name": child.name, "path": child.name}
        language = _dominant_language(child)
        if language is not None:
            entry["language"] = language.value
        services.append(entry)
    if not services:
        raise ValueError(f"no service directories found under {root}")
    return Manifest.model_validate({"services": services})


def _dominant_language(directory: Path) -> Language | None:
    counts: dict[Language, int] = {}
    for file in directory.rglob("*"):
        if not file.is_file() or any(part in DEFAULT_IGNORE_DIRS for part in file.parts):
            continue
        language = classify_path(file.name)
        if language is not Language.UNKNOWN:
            counts[language] = counts.get(language, 0) + 1
    return max(counts, key=lambda k: counts[k]) if counts else None


def resolve_manifest(root: Path, manifest_path: Path | None) -> tuple[Manifest, bool]:
    """Load the manifest, deriving one from directories if none exists.

    Returns ``(manifest, was_derived)``."""
    if manifest_path is not None:
        return load_manifest(manifest_path.read_text(encoding="utf-8")), False
    default = root / "manifest.yaml"
    if default.is_file():
        return load_manifest(default.read_text(encoding="utf-8")), False
    return derive_manifest(root), True


def manifest_to_yaml(manifest: Manifest) -> str:
    """Serialise a (derived) manifest to reviewable YAML."""
    lines = [
        "# manifest.yaml — DERIVED from directory layout. Review like code (§3.4).",
        "services:",
    ]
    for svc in manifest.services:
        lines.append(f"  - name: {svc.name}")
        lines.append(f"    path: {svc.path}")
        if svc.language is not None:
            lines.append(f"    language: {svc.language.value}")
    return "\n".join(lines) + "\n"


def write_derived_manifest(root: Path) -> tuple[Path, bool]:
    """Derive a manifest from ``root``'s directories and write it, non-destructively.

    Writes ``manifest.yaml`` if none exists; otherwise writes ``manifest.draft.yaml``
    so an existing (possibly hand-edited) manifest is never clobbered. Returns
    ``(path_written, was_draft)``."""
    manifest = derive_manifest(root)
    target = root / "manifest.yaml"
    was_draft = target.exists()
    if was_draft:
        target = root / "manifest.draft.yaml"
    target.write_text(manifest_to_yaml(manifest), encoding="utf-8")
    return target, was_draft


def estimate_estate(
    root: Path, manifest: Manifest, config: EstimatorConfig | None = None
) -> EstateEstimate:
    cfg = config or EstimatorConfig()
    per_service: dict[str, ServiceEstimate] = {
        svc.name: ServiceEstimate(service=svc.name) for svc in manifest.services
    }
    modules_seen: dict[str, set[str]] = {svc.name: set() for svc in manifest.services}
    excluded = 0

    for walked in walk_estate(root, manifest):
        est = per_service.setdefault(walked.service, ServiceEstimate(service=walked.service))
        language = classify_path(walked.path)
        if not cfg.includes(walked.path, language):
            excluded += 1
            continue
        source = walked.abs_path.read_bytes()
        text = source.decode("utf-8", errors="replace")
        chunks = chunk_source(
            source, language=language, service=walked.service, path=walked.path, commit_sha="EST"
        )

        est.files += 1
        modules_seen.setdefault(walked.service, set()).add(_module_key(walked.path))
        for chunk in chunks:
            chunk_tokens = count_tokens(chunk.content, cfg.chars_per_token)
            est.chunks += 1
            est.embed_tokens += chunk_tokens
            if chunk_tokens > cfg.embed_context_limit:
                est.oversized_chunks += 1

        # Tier 1: the prompt sees the file (+imports); overhead is billed per file.
        file_tokens = count_tokens(text, cfg.chars_per_token)
        est.tier1_input += file_tokens + cfg.prompt_overhead_tokens
        est.tier1_overhead += cfg.prompt_overhead_tokens
        est.tier1_output += cfg.tier1_output_tokens

    # Tiers 2-3: three orders of magnitude lower volume (§5.2.1). Approximate from
    # module / service counts, each with its own instruction overhead.
    for name, est in per_service.items():
        est.modules = len(modules_seen.get(name, set()))
        est.tier2_tokens = est.modules * (cfg.prompt_overhead_tokens + cfg.tier2_output_tokens)
        est.tier3_tokens = cfg.prompt_overhead_tokens + cfg.tier3_output_tokens if est.files else 0

    ordered = sorted(per_service.values(), key=lambda s: s.service)
    return EstateEstimate(config=cfg, services=ordered, excluded_files=excluded)


def _module_key(path: str) -> str:
    parent = PurePosixPath(path).parent
    return str(parent)


# --------------------------------------------------------------------------- #
# Calibration + CLI
# --------------------------------------------------------------------------- #


def load_calibration(path: Path, base: EstimatorConfig) -> EstimatorConfig:
    """Override defaults with measured Phase-4 figures from a JSON file."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return EstimatorConfig(**{**asdict(base), **data})


def parse_suffixes(raw: str) -> frozenset[str]:
    out: set[str] = set()
    for token in raw.split(","):
        s = token.strip().lower()
        if s:
            out.add(s if s.startswith(".") else "." + s)
    return frozenset(out)


def format_report(est: EstateEstimate) -> str:
    cfg = est.config
    lines = [
        "Vectural index-cost estimate (zero gateway tokens spent)",
        f"  chars/token={cfg.chars_per_token}  prompt_overhead={cfg.prompt_overhead_tokens}",
        "",
        f"  files                 {est.total_files:>12,}",
        f"  excluded files        {est.excluded_files:>12,}   (non-source / --exclude)",
        f"  chunks                {est.total_chunks:>12,}",
        "",
        "  embedding (create vectors — local, BGE-M3):",
        f"    embed tokens        {est.total_embed_tokens:>12,}",
        f"    oversized chunks    {est.total_oversized:>12,}   (> {cfg.embed_context_limit} tok)",
        "",
        "  summarisation (gateway-billed):",
        f"    instruction overhead{est.total_instruction_overhead:>12,}"
        f"   ({est.overhead_fraction:.1%} of tier-1 input, §5.2.1)",
        f"    total gateway tokens{est.total_gateway_tokens:>12,}",
        "",
        "  per service (gateway tokens):",
    ]
    for s in est.services:
        lines.append(
            f"    {s.service:24} files={s.files:>5}  chunks={s.chunks:>6}  "
            f"gateway={s.summary_gateway_tokens:>12,}"
        )
    lines.append("")
    lines.append("  weekly indexing plan (bin-packed into tranches, §5.7):")
    for week, services in sorted(est.weekly_plan().items()):
        label = "unscheduled (over budget)" if week == -1 else f"week {week + 1}"
        lines.append(f"    {label:<26} {', '.join(services) or '—'}")
    return "\n".join(lines)


def _prompt(label: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default else ""
    while True:
        value = input(f"{label}{suffix}: ").strip()
        if value:
            return value
        if default is not None:
            return default


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    parser = argparse.ArgumentParser(prog="vectural-estimate", description=__doc__)
    parser.add_argument("root", type=Path, nargs="?", help="estate root (prompted if omitted)")
    parser.add_argument(
        "-m", "--manifest", type=Path, default=None,
        help="manifest.yaml (default: <root>/manifest.yaml, else derived from dirs)",
    )
    parser.add_argument("--chars-per-token", type=float, default=3.5)
    parser.add_argument("--prompt-overhead", type=int, default=120)
    parser.add_argument("--tier1-output", type=int, default=150)
    parser.add_argument("--monthly-budget", type=int, default=50_000_000)
    parser.add_argument("--reserve", type=float, default=0.30)
    parser.add_argument(
        "--source-only", action="store_true",
        help="count only files with a recognised source language (skip docs/config/data)",
    )
    parser.add_argument(
        "--exclude", default="",
        help="comma-separated extensions to exclude, e.g. '.md,.json,.lock'",
    )
    parser.add_argument(
        "--exclude-glob", default="",
        help="comma-separated path globs to exclude (fnmatch; '*' spans dirs), "
        "e.g. '*generated*,*.min.js,dist/*'",
    )
    parser.add_argument(
        "--calibration", type=Path, default=None,
        help="JSON of measured figures (Phase 4) overriding the heuristic defaults",
    )
    parser.add_argument(
        "--write-manifest", action="store_true",
        help="regenerate a manifest from the directory layout and write it to "
        "<root>/manifest.yaml (or manifest.draft.yaml if one already exists)",
    )
    parser.add_argument("--json", action="store_true", help="emit the full estimate as JSON")
    args = parser.parse_args(argv)

    root = args.root or Path(_prompt("Estate root (folder to analyse)")).expanduser()
    if not root.is_dir():
        print(f"error: {root} is not a directory", file=sys.stderr)
        return 2
    if args.manifest is not None and not args.manifest.is_file():
        print(f"error: manifest {args.manifest} not found", file=sys.stderr)
        return 2

    # --write-manifest regenerates a manifest from the directory layout, whether
    # or not one already exists (writing a non-clobbering draft if it does).
    if args.write_manifest:
        try:
            written, was_draft = write_derived_manifest(root)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        if was_draft:
            print(
                f"manifest.yaml already exists — wrote a regenerated draft to {written} "
                "(review/merge; the manifest is human-owned, §3.4).",
                file=sys.stderr,
            )
        else:
            print(f"Wrote {written} (review it — the manifest is human-owned, §3.4).",
                  file=sys.stderr)

    try:
        manifest, derived = resolve_manifest(root, args.manifest)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if derived:
        print(
            f"No manifest.yaml found under {root} — derived one service per top-level "
            f"directory ({len(manifest.services)} services).",
            file=sys.stderr,
        )

    config = EstimatorConfig(
        chars_per_token=args.chars_per_token,
        prompt_overhead_tokens=args.prompt_overhead,
        tier1_output_tokens=args.tier1_output,
        monthly_budget=args.monthly_budget,
        serving_reserve_fraction=args.reserve,
        source_only=args.source_only,
        exclude_suffixes=parse_suffixes(args.exclude),
        exclude_globs=tuple(g.strip() for g in args.exclude_glob.split(",") if g.strip()),
    )
    if args.calibration is not None:
        config = load_calibration(args.calibration, config)

    estimate = estimate_estate(root, manifest, config)

    if args.json:
        print(json.dumps(estimate.as_dict(), indent=2))
    else:
        print(format_report(estimate))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
