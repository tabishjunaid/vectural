#!/usr/bin/env python3
"""index_cost_estimator — the plan's Phase 0/§5.2.1 estimator entrypoint.

Thin wrapper over :func:`backend.estimator.main`. Run it against the estate to
size the indexing token spend before spending anything:

    python scripts/index_cost_estimator.py <estate-root> -m manifest.yaml
    python scripts/index_cost_estimator.py <estate-root> --calibration phase4.json --json
"""

from __future__ import annotations

from backend.estimator import main

if __name__ == "__main__":
    raise SystemExit(main())
