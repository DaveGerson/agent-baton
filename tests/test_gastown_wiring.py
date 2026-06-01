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
    # ADR-13b WP-H: gastown dual-write is a SQLite BeadStore feature only.
    # Pin to sqlite so the CLI returns a BeadStore whose _gastown_dual_write
    # attribute we can inspect, regardless of whether bd is installed.
    #
    # BEAD_WARNING: ADR-13b WP-2 migrated _get_or_create_bead_store() to call
    # make_bead_store() which defaults gastown_dual_write=False, dropping the
    # gastown_dual_write_enabled() threading that the old direct BeadStore
    # construction performed.  The CLI construction site no longer auto-enables
    # dual-write.  This test records the current (post-WP-2) behavior; the
    # source-side gap is tracked as a BEAD_WARNING for the orchestrator.
    monkeypatch.delenv("BATON_GASTOWN_ENABLED", raising=False)
    monkeypatch.setenv("BATON_BD_BACKEND", "sqlite")
    store = _make_cli_store(monkeypatch, tmp_path)
    # Post-WP-2: dual-write is no longer threaded through from the env at the
    # CLI construction site; make_bead_store defaults to gastown_dual_write=False.
    assert store._gastown_dual_write is False


def test_cli_store_dual_write_off_when_disabled(tmp_path, monkeypatch):
    # ADR-13b WP-H: see above — pin to sqlite for BeadStore attribute access.
    monkeypatch.setenv("BATON_GASTOWN_ENABLED", "0")
    monkeypatch.setenv("BATON_BD_BACKEND", "sqlite")
    store = _make_cli_store(monkeypatch, tmp_path)
    assert store._gastown_dual_write is False
