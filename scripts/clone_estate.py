#!/usr/bin/env python3
"""Thin shim for the clone-estate stage (see :mod:`backend.estate`).

Kept so `python scripts/clone_estate.py …` keeps working; the logic lives in the
packaged module and is also exposed as the `vectural-clone` console script."""

from __future__ import annotations

from backend.estate import clone_main

if __name__ == "__main__":
    raise SystemExit(clone_main())
