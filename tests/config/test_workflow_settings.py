"""Tests for :mod:`agent_baton.core.config.workflow`.

Spec: docs/internal/adversarial-tdd-workflow-design.md §4 — a Pydantic
settings section under the top-level ``workflow:`` key in baton.yaml, layered
with ``manager.py``'s loader conventions (defaults < ``~/.baton/config.yaml``
< project ``baton.yaml``), plus decision #7's requirement that
``ManagerConfig`` stop warning about the sibling-owned ``workflow`` key.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from agent_baton.core.config.manager import ManagerConfig
from agent_baton.core.config.workflow import (
    StageOverride,
    WorkflowSettings,
    load_workflow_settings,
)

_SPEC_YAML = """\
workflow:
  stages:
    spec:
      agent: subject-matter-expert
      model: opus
    implementation:
      model: haiku
  external_command: "gemini -p 'verify this slice against the spec'"
  external_timeout_seconds: 600
  final_review_fanout_divisor: 2
  final_review_max_reviewers: 5
"""


@pytest.fixture(autouse=True)
def fake_home(tmp_path_factory: pytest.TempPathFactory, monkeypatch: Any) -> Path:
    """Redirect ``Path.home()`` so the developer's real ``~/.baton/config.yaml``
    can never leak into these tests (mirrors ``tests/manager/conftest.py``).

    Returns the fake home so a test can plant a decoy user config in it.
    """
    home = tmp_path_factory.mktemp("fake_home")
    monkeypatch.setattr(Path, "home", lambda: home)
    return home


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# Defaults (spec §4)
# ---------------------------------------------------------------------------

class TestDefaults:
    def test_missing_file_yields_defaults(self, tmp_path: Path) -> None:
        settings = load_workflow_settings(tmp_path)
        assert settings.stages == {}
        assert settings.external_command == ""
        assert settings.external_timeout_seconds == 1800
        assert settings.final_review_fanout_divisor == 4
        assert settings.final_review_max_reviewers == 3

    def test_direct_construction_matches_loaded_defaults(self, tmp_path: Path) -> None:
        assert load_workflow_settings(tmp_path) == WorkflowSettings()

    def test_stage_override_defaults_are_unset(self) -> None:
        override = StageOverride()
        assert override.agent is None
        assert override.model is None

    def test_baton_yaml_without_a_workflow_section_yields_defaults(
        self, tmp_path: Path
    ) -> None:
        _write(tmp_path / "baton.yaml", "manager_mode:\n  enabled_by_default: true\n")
        assert load_workflow_settings(tmp_path) == WorkflowSettings()


# ---------------------------------------------------------------------------
# Project config overrides
# ---------------------------------------------------------------------------

class TestProjectConfigOverrides:
    def test_full_workflow_section_is_read(self, tmp_path: Path) -> None:
        _write(tmp_path / ".claude" / "baton.yaml", _SPEC_YAML)

        settings = load_workflow_settings(tmp_path)

        assert settings.external_command == (
            "gemini -p 'verify this slice against the spec'"
        )
        assert settings.external_timeout_seconds == 600
        assert settings.final_review_fanout_divisor == 2
        assert settings.final_review_max_reviewers == 5

    def test_stage_agent_and_model_overrides(self, tmp_path: Path) -> None:
        _write(tmp_path / ".claude" / "baton.yaml", _SPEC_YAML)

        settings = load_workflow_settings(tmp_path)

        assert settings.stages["spec"].agent == "subject-matter-expert"
        assert settings.stages["spec"].model == "opus"

    def test_stage_override_may_set_model_only(self, tmp_path: Path) -> None:
        _write(tmp_path / ".claude" / "baton.yaml", _SPEC_YAML)

        settings = load_workflow_settings(tmp_path)

        assert settings.stages["implementation"].model == "haiku"
        assert settings.stages["implementation"].agent is None

    def test_root_baton_yaml_is_read(self, tmp_path: Path) -> None:
        _write(tmp_path / "baton.yaml", "workflow:\n  external_command: codex verify\n")
        assert load_workflow_settings(tmp_path).external_command == "codex verify"

    def test_claude_dir_takes_precedence_over_root(self, tmp_path: Path) -> None:
        _write(
            tmp_path / ".claude" / "baton.yaml",
            "workflow:\n  final_review_max_reviewers: 1\n",
        )
        _write(
            tmp_path / "baton.yaml",
            "workflow:\n  final_review_max_reviewers: 9\n",
        )
        assert load_workflow_settings(tmp_path).final_review_max_reviewers == 1

    def test_project_config_beats_user_config(
        self, tmp_path: Path, fake_home: Path
    ) -> None:
        _write(
            fake_home / ".baton" / "config.yaml",
            "workflow:\n  external_timeout_seconds: 60\n",
        )
        _write(
            tmp_path / ".claude" / "baton.yaml",
            "workflow:\n  external_timeout_seconds: 120\n",
        )
        assert load_workflow_settings(tmp_path).external_timeout_seconds == 120

    def test_user_config_beats_defaults(self, tmp_path: Path, fake_home: Path) -> None:
        _write(
            fake_home / ".baton" / "config.yaml",
            "workflow:\n  external_timeout_seconds: 60\n",
        )
        assert load_workflow_settings(tmp_path).external_timeout_seconds == 60

    def test_sibling_top_level_keys_are_ignored(self, tmp_path: Path) -> None:
        _write(
            tmp_path / ".claude" / "baton.yaml",
            "default_risk_level: LOW\n"
            "manager_mode:\n"
            "  enabled_by_default: true\n"
            "workflow:\n"
            "  external_command: codex verify\n",
        )
        assert load_workflow_settings(tmp_path).external_command == "codex verify"

    def test_unknown_nested_key_is_ignored(self, tmp_path: Path) -> None:
        # §4: extra="ignore" — unknown nested KEYS are forward-compat, not errors.
        _write(
            tmp_path / ".claude" / "baton.yaml",
            "workflow:\n  frobnicate: true\n  external_command: codex verify\n",
        )
        assert load_workflow_settings(tmp_path).external_command == "codex verify"


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

class TestValidation:
    @pytest.mark.parametrize("tier", ["haiku", "sonnet", "opus", "fable"])
    def test_every_valid_tier_is_accepted(self, tmp_path: Path, tier: str) -> None:
        _write(
            tmp_path / ".claude" / "baton.yaml",
            f"workflow:\n  stages:\n    test_authoring:\n      model: {tier}\n",
        )
        settings = load_workflow_settings(tmp_path)
        assert settings.stages["test_authoring"].model == tier

    def test_invalid_tier_raises_an_actionable_error(self, tmp_path: Path) -> None:
        _write(
            tmp_path / ".claude" / "baton.yaml",
            "workflow:\n  stages:\n    spec:\n      model: gpt-9\n",
        )

        # ValueError is the common base of both a Pydantic ValidationError and
        # manager.py's ManagerConfigError, so this pins "loud + actionable"
        # without over-pinning which of the two the loader raises.
        with pytest.raises(ValueError) as exc_info:
            load_workflow_settings(tmp_path)

        message = str(exc_info.value)
        assert "gpt-9" in message
        for tier in ("haiku", "sonnet", "opus", "fable"):
            assert tier in message

    def test_invalid_tier_on_direct_construction_raises(self) -> None:
        with pytest.raises(ValueError):
            StageOverride(model="gpt-9")


# ---------------------------------------------------------------------------
# ManagerConfig coexistence (decision #7 / §4)
# ---------------------------------------------------------------------------

class TestManagerConfigCoexistence:
    def test_manager_config_does_not_warn_about_the_workflow_key(
        self, tmp_path: Path
    ) -> None:
        _write(tmp_path / ".claude" / "baton.yaml", _SPEC_YAML)

        config = ManagerConfig.load(tmp_path)

        assert not any("workflow" in warning for warning in config.warnings)

    def test_manager_config_still_warns_about_genuinely_unknown_keys(
        self, tmp_path: Path
    ) -> None:
        # Guards against "silence every unknown key" as the fix.
        _write(
            tmp_path / ".claude" / "baton.yaml",
            "frobnicate: 1\nworkflow:\n  external_command: codex verify\n",
        )

        config = ManagerConfig.load(tmp_path)

        assert any("frobnicate" in warning for warning in config.warnings)

    def test_manager_config_loads_normally_alongside_a_workflow_section(
        self, tmp_path: Path
    ) -> None:
        _write(
            tmp_path / ".claude" / "baton.yaml",
            "manager_mode:\n"
            "  enabled_by_default: true\n"
            "workflow:\n"
            "  external_command: codex verify\n",
        )

        config = ManagerConfig.load(tmp_path)

        assert config.manager_mode.enabled_by_default is True
