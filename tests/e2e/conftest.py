"""Shared fixtures for tests/e2e -- hermetic ``Path.home()`` isolation.

Mirrors ``tests/manager/conftest.py``'s ``fake_home`` fixture: both
``ManagerConfig.load()`` and ``IntelligentPlanner``'s collaborators
(``AgentRegistry``/``KnowledgeRegistry``/``PatternLearner``/...) read from
``Path.home()`` regardless of the working directory, so any test here
could otherwise pick up a real, developer-machine-specific global config
or agent/knowledge pack and produce non-deterministic results.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest


@pytest.fixture(autouse=True)
def fake_home(tmp_path_factory: pytest.TempPathFactory, monkeypatch: Any) -> Path:
    """Redirect ``Path.home()`` to a fresh fake home directory for every
    test collected under ``tests/e2e/`` (autouse)."""
    fake_home_dir = tmp_path_factory.mktemp("fake_home_e2e")
    monkeypatch.setattr(Path, "home", lambda: fake_home_dir)
    return fake_home_dir
