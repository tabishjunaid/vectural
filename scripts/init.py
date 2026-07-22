#!/usr/bin/env python3
"""Thin shim for the guided initial setup (see :mod:`backend.init`).

Runs the three setup stages in order — clone → manifest → estimate. Kept so
`python scripts/init.py …` works; also exposed as the `vectural-init` console
script."""

from __future__ import annotations

from backend.init import main

if __name__ == "__main__":
    raise SystemExit(main())
