"""Tests for Gastown Phase M1 wiring — git-notes dual-write default-on.

Part A of the Gastown bead architecture (bd-2870 / bd-971d).  Phase M0 built
the dual-write machinery but left it unwired (``gastown_dual_write=False``
everywhere).  Phase M1 flips it on at the *runtime construction sites* via the
``BATON_GASTOWN_ENABLED`` env var (default ON), while the ``BeadStore`` kwarg
default stays ``False`` so direct library use is deterministic.

Test matrix:
- ``gastown_dual_write_enabled()`` truth table (default ON; 0/false/no/empty OFF).
- CLI bead-store helpers thread the env gate into ``BeadStore``.
- The kwarg default is still ``False`` (library contract unchanged).
"""
from __future__ import annotations

import importlib

import pytest

from agent_baton.core.engine.bead_store import (
    BeadStore,
    gastown_dual_write_enabled,
)


# ---------------------------------------------------------------------------
# Env-gate truth table
# ---------------------------------------------------------------------------


def test_gastown_enabled_default_on(monkeypatch):
    monkeypatch.delenv("BATON_GASTOWN_ENABLED", raising=False)
    assert gastown_dual_write_enabled() is True


@pytest.mark.parametrize("value", ["0", "false", "False", "no", ""])
def test_gastown_disabled_values(monkeypatch, value):
    monkeypatch.setenv("BATON_GASTOWN_ENABLED", value)
    assert gastown_dual_write_enabled() is False


@pytest.mark.parametrize("value", ["1", "true", "yes", "on", "anything"])
def test_gastown_enabled_values(monkeypatch, value):
    monkeypatch.setenv("BATON_GASTOWN_ENABLED", value)
    assert gastown_dual_write_enabled() is True


# ---------------------------------------------------------------------------
# Library contract: kwarg default stays False (env-independent)
# ---------------------------------------------------------------------------


def test_kwarg_default_is_false(tmp_path, monkeypatch):
    """A bare BeadStore(db) must not silently enable dual-write from the env."""
    monkeypatch.delenv("BATON_GASTOWN_ENABLED", raising=False)  # default would be ON
    db = tmp_path / "baton.db"
    store = BeadStore(db)
    assert store._gastown_dual_write is False


# ---------------------------------------------------------------------------
# CLI bead-store helpers thread the env gate through
# ---------------------------------------------------------------------------


def _make_cli_store(monkeypatch, tmp_path):
    """Build a store via the CLI helper with BATON_DB_PATH pointed at tmp."""
    from agent_baton.cli.commands import bead_cmd

    db = tmp_path / ".claude" / "team-context" / "baton.db"
    monkeypatch.setenv("BATON_DB_PATH", str(db))
    return bead_cmd._get_or_create_bead_store()


def test_cli_store_dual_write_on_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("BATON_GASTOWN_ENABLED", raising=False)
    store = _make_cli_store(monkeypatch, tmp_path)
    assert store._gastown_dual_write is True


def test_cli_store_dual_write_off_when_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("BATON_GASTOWN_ENABLED", "0")
    store = _make_cli_store(monkeypatch, tmp_path)
    assert store._gastown_dual_write is False
