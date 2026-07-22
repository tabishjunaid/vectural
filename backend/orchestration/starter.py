"""Start / resume the durable indexing run (``vectural-index``).

Computes the estate's tranche partition (the estimator's weekly plan), flattens it
into a priority-ordered service list, and starts the Temporal
:class:`IndexingWorkflow` under a **stable workflow id per estate** — so re-running
attaches to the in-flight run (resume) rather than duplicating it. The worker
(``vectural-indexer-worker``) does the actual indexing.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from backend.config import Settings, load_settings
from backend.domain.manifest import Manifest, load_manifest
from backend.estimator import EstimatorConfig, estimate_estate


def _partition(cfg: Settings) -> tuple[list[str], dict[int, list[str]], Manifest]:
    """(ordered services, weekly plan, manifest) from the estimator's tranche plan."""
    manifest = load_manifest(cfg.manifest_path.read_text(encoding="utf-8"))
    estimate = estimate_estate(
        cfg.estate_root, manifest,
        EstimatorConfig(
            monthly_budget=cfg.monthly_budget,
            serving_reserve_fraction=cfg.serving_reserve_fraction,
            tranche_count=cfg.tranche_count,
        ),
    )
    plan = estimate.weekly_plan()
    ordered: list[str] = []
    for week in sorted(w for w in plan if w >= 0):  # -1 = over-budget, handled below
        ordered.extend(plan[week])
    return ordered, plan, manifest


def _workflow_id(cfg: Settings) -> str:
    name = cfg.estate_root.resolve().name or "estate"
    return f"index-{name}"


def _print_plan(ordered: list[str], plan: dict[int, list[str]]) -> None:
    print("Indexing partition (estimator weekly plan):", file=sys.stderr)
    for week in sorted(plan):
        label = "over-budget (deferred)" if week == -1 else f"week {week + 1}"
        print(f"  {label:<24} {', '.join(plan[week]) or '—'}", file=sys.stderr)
    if -1 in plan:
        print(f"  ! {len(plan[-1])} service(s) exceed the monthly budget and are NOT scheduled: "
              f"{', '.join(plan[-1])}", file=sys.stderr)
    print(f"\nOrdered service queue ({len(ordered)}): {', '.join(ordered)}", file=sys.stderr)


async def _start(cfg: Settings, ordered: list[str], tranche_size: int,
                 park_backoff: int, wait: bool) -> int:
    from temporalio.client import Client, WorkflowExecutionStatus
    from temporalio.service import RPCError

    client = await Client.connect(cfg.temporal_target, namespace=cfg.temporal_namespace)
    wid = _workflow_id(cfg)
    try:
        handle = await client.start_workflow(
            "IndexingWorkflow",
            args=[ordered, tranche_size, park_backoff],
            id=wid,
            task_queue=cfg.temporal_task_queue,
        )
        action = "Started"
    except RPCError:
        # A run with this id is already in flight — attach to it (resume).
        handle = client.get_workflow_handle(wid)
        action = "Attached to running"

    ui = cfg.temporal_target.split(":")[0]
    print(f"{action} workflow {wid!r} on {cfg.temporal_task_queue!r}. "
          f"Track it in the Temporal UI (http://{ui}:8080).", file=sys.stderr)

    if not wait:
        return 0
    result = await handle.result()
    desc = await handle.describe()
    ok = desc.status == WorkflowExecutionStatus.COMPLETED
    print(f"Workflow finished: {result}", file=sys.stderr)
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="vectural-index",
        description="Start/resume the durable, quota-partitioned estate indexing run.",
    )
    parser.add_argument("--tranche-size", type=int, default=0, metavar="N",
                        help="continue-as-new every N services (default: first week's size)")
    parser.add_argument("--park-backoff", type=int, default=21_600, metavar="SECONDS",
                        help="wait on a quota hold before re-checking (default 6h)")
    parser.add_argument("--wait", action="store_true", help="block until the run finishes")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the tranche partition and exit (no Temporal)")
    args = parser.parse_args(argv)

    cfg = load_settings()
    ordered, plan, _ = _partition(cfg)
    _print_plan(ordered, plan)
    if not ordered:
        print("Nothing to index (no scheduled services).", file=sys.stderr)
        return 0
    if args.dry_run:
        return 0

    first_week = next((plan[w] for w in sorted(plan) if w >= 0 and plan[w]), ordered)
    tranche_size = args.tranche_size or len(first_week)
    return asyncio.run(_start(cfg, ordered, tranche_size, args.park_backoff, args.wait))


if __name__ == "__main__":
    raise SystemExit(main())
