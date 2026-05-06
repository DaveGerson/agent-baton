"""Tests for F0.2 tenancy context resolution and identity.yaml integration."""
from __future__ import annotations

import os
import pytest
from pathlib import Path
from unittest.mock import patch


def test_resolve_tenancy_defaults() -> None:
    """Without identity.yaml or env vars, defaults are returned."""
    from agent_baton.models.tenancy import resolve_tenancy_context

    with patch.dict(os.environ, {}, clear=False):
        # Ensure BATON env vars are absent for this test
        env = {k: v for k, v in os.environ.items()
               if not k.startswith("BATON_")}
        with patch.dict(os.environ, env, clear=True):
            # Patch the identity file to not exist
            with patch("agent_baton.models.tenancy._IDENTITY_FILE", Path("/nonexistent/identity.yaml")):
                ctx = resolve_tenancy_context()
    assert ctx.org_id == "default"
    assert ctx.team_id == "default"
    assert ctx.user_id == "local-user"
    assert ctx.cost_center == ""


def test_resolve_tenancy_from_env() -> None:
    """Env vars override defaults."""
    from agent_baton.models.tenancy import resolve_tenancy_context

    with patch("agent_baton.models.tenancy._IDENTITY_FILE", Path("/nonexistent/identity.yaml")):
        with patch.dict(os.environ, {
            "BATON_ORG_ID": "my-org",
            "BATON_TEAM_ID": "my-team",
            "BATON_USER_ID": "alice",
            "BATON_COST_CENTER": "cc-eng",
        }):
            ctx = resolve_tenancy_context()
    assert ctx.org_id == "my-org"
    assert ctx.team_id == "my-team"
    assert ctx.user_id == "alice"
    assert ctx.cost_center == "cc-eng"


def test_resolve_tenancy_from_identity_yaml(tmp_path: Path) -> None:
    """Identity file overrides env vars (identity file wins)."""
    from agent_baton.models.tenancy import resolve_tenancy_context

    identity_file = tmp_path / "identity.yaml"
    identity_file.write_text(
        "org_id: file-org\nteam_id: file-team\nuser_id: bob\ncost_center: cc-data\n",
        encoding="utf-8",
    )
    with patch("agent_baton.models.tenancy._IDENTITY_FILE", identity_file):
        with patch.dict(os.environ, {
            "BATON_ORG_ID": "env-org",
            "BATON_TEAM_ID": "env-team",
        }):
            ctx = resolve_tenancy_context()
    assert ctx.org_id == "file-org"
    assert ctx.team_id == "file-team"
    assert ctx.user_id == "bob"
    assert ctx.cost_center == "cc-data"


def test_tenancy_context_to_dict() -> None:
    from agent_baton.models.tenancy import TenancyContext
    ctx = TenancyContext(org_id="o", team_id="t", user_id="u", cost_center="cc")
    d = ctx.to_dict()
    assert d == {"org_id": "o", "team_id": "t", "user_id": "u", "cost_center": "cc"}


def test_write_identity_creates_file(tmp_path: Path) -> None:
    from agent_baton.models.tenancy import TenancyStore

    identity_file = tmp_path / "identity.yaml"
    with patch("agent_baton.models.tenancy._IDENTITY_FILE", identity_file):
        path = TenancyStore.write_identity(org_id="acme", team_id="platform")
    assert path == identity_file
    assert identity_file.exists()
    content = identity_file.read_text(encoding="utf-8")
    assert "acme" in content
    assert "platform" in content


def test_write_identity_merges_existing(tmp_path: Path) -> None:
    from agent_baton.models.tenancy import TenancyStore

    identity_file = tmp_path / "identity.yaml"
    identity_file.write_text("org_id: old-org\nuser_id: alice\n", encoding="utf-8")
    with patch("agent_baton.models.tenancy._IDENTITY_FILE", identity_file):
        TenancyStore.write_identity(team_id="new-team")
    content = identity_file.read_text(encoding="utf-8")
    # org_id must be preserved
    assert "old-org" in content
    # new team must be present
    assert "new-team" in content
    # user_id must be preserved
    assert "alice" in content
