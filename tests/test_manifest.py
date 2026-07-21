"""Manifest loader and path resolution (§4.4 / §3.4)."""

from __future__ import annotations

import pytest

from backend.domain.manifest import ManifestError, load_manifest
from backend.domain.models import Language


def test_loads_and_coerces_language_alias() -> None:
    m = load_manifest(
        """
        services:
          - name: booking-api
            path: booking-api
            language: java
          - name: growth-web
            path: growth/web
            language: ts
        """
    )
    assert len(m.services) == 2
    assert m.services[0].language is Language.JAVA
    assert m.services[1].language is Language.TYPESCRIPT


def test_service_for_path_longest_prefix_wins() -> None:
    m = load_manifest(
        """
        services:
          - name: platform
            path: platform
          - name: platform-auth
            path: platform/auth
        """
    )
    assert m.service_for_path("platform/auth/login.py").name == "platform-auth"
    assert m.service_for_path("platform/billing/x.py").name == "platform"
    assert m.service_for_path("other/thing.py") is None


def test_duplicate_name_rejected() -> None:
    with pytest.raises(ManifestError, match="duplicate service name"):
        load_manifest(
            """
            services:
              - name: dup
                path: a
              - name: dup
                path: b
            """
        )


def test_duplicate_path_rejected() -> None:
    with pytest.raises(ManifestError, match="duplicate service path"):
        load_manifest(
            """
            services:
              - name: a
                path: same
              - name: b
                path: same
            """
        )


def test_unknown_language_rejected() -> None:
    with pytest.raises(ManifestError, match="unknown language"):
        load_manifest(
            """
            services:
              - name: a
                path: a
                language: cobol
            """
        )


def test_path_escape_rejected() -> None:
    with pytest.raises(ManifestError, match="within root"):
        load_manifest(
            """
            services:
              - name: a
                path: ../outside
            """
        )


def test_missing_services_key_rejected() -> None:
    with pytest.raises(ManifestError, match="services"):
        load_manifest("nope: true")
