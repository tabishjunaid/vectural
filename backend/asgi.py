"""ASGI entrypoint: ``uvicorn backend.asgi:app``.

Builds the runnable app from environment settings (estate root, manifest, CORS)
using the in-memory bootstrap. This is the process the docker-compose ``backend``
service and local ``uvicorn`` both run.
"""

from __future__ import annotations

from backend.bootstrap import build_app_from_env

app = build_app_from_env()
