"""Shared fixtures for tests/manager — hermetic ``Path.home()`` isolation.

``ManagerConfig.load()`` always checks ``~/.baton/config.yaml`` regardless
of the ``start_dir`` passed in (see ``agent_baton/core/config/manager.py``),
so any test in this package could otherwise pick up a real, developer-
machine-specific global config and produce non-deterministic results. This
autouse fixture redirects ``Path.home()`` to a fresh per-test fake home
directory so no real host file can leak into a tests/manager test.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest


@pytest.fixture(autouse=True)
def fake_home(tmp_path_factory: pytest.TempPathFactory, monkeypatch: Any) -> Path:
    """Redirect ``Path.home()`` to a fresh fake home directory for every test.

    Applies to every test collected under ``tests/manager/`` (autouse).
    Returns the fake home path so a test can populate it directly (e.g.
    to write a decoy ``~/.baton/config.yaml`` and prove the redirect is
    effective, not vacuous).
    """
    fake_home_dir = tmp_path_factory.mktemp("fake_home")
    monkeypatch.setattr(Path, "home", lambda: fake_home_dir)
    return fake_home_dir
