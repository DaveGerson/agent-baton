"""Tests for :mod:`agent_baton.core.config.manager` (M1 — manager config foundation).

See docs/internal/manager-mode-pmo-plan.md Wave 0 / Task 1 and
docs/specs/agent-baton-claude-code-middle-manager-prd-tdd.md §9.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agent_baton.core.config.manager import ManagerConfig, ManagerConfigError

# Spec §9.1 example .claude/baton.yaml, verbatim.
_SPEC_YAML = """\
version: 1

manager_mode:
  enabled_by_default: false
  project_size_default: medium
  manager_decision_threshold: medium
  assumptions_policy: record_and_continue
  ambiguity_policy: ask_when_high_impact

team:
  max_agents_by_complexity:
    light: 2
    medium: 5
    heavy: 8
  require_role_cards: true
  require_workstream_owners: true
  prefer_specialists_over_generalists: true
  allow_talent_builder: true
  default_roles:
    - architect
    - backend-engineer
    - test-engineer

scoping:
  require_scope_contracts: true
  require_allowed_paths: true
  allow_cross_scope_edits: manager_approval
  scope_expansion_policy: queue_for_manager
  out_of_scope_policy: block_or_escalate

context:
  default_step_token_budget: 12000
  max_knowledge_docs_per_step: 6
  include_prior_phase_handoff: true
  include_full_prior_outputs: false
  summarize_prior_outputs: true
  dedupe_knowledge_across_session: true
  context_bundle_format: json

knowledge_packs:
  discovery_paths:
    - .claude/knowledge
    - docs
    - .
  default_packs:
    - repo-architecture
    - coding-conventions
    - testing-strategy
  required_for_code_steps:
    - coding-conventions
    - testing-strategy
  stale_after_days: 90
  missing_pack_policy: propose

policies:
  phase_completion:
    adversarial_review: always
    handoff_required: true
    gates: project_configured
  project_completion:
    adversarial_review: always
    manager_report: required
    retrospective: required
  review_agents:
    adversarial_review: code-reviewer
    project_review: auditor

gates:
  mode: project_configured
  gate_scope: focused
  allow_smoke_fallback: true
  missing_gate_policy: warn_and_request_manager_decision

reporting:
  write_manager_brief: true
  write_manager_report: true
  decision_log: true
  include_raw_logs_by_default: false
"""


def test_defaults_when_no_config_file(tmp_path: Path) -> None:
    config = ManagerConfig.load(tmp_path)
    assert config.manager_mode.enabled_by_default is False
    assert config.context.default_step_token_budget == 12000
    assert config.policies.phase_completion.adversarial_review == "always"


def test_loads_claude_baton_yaml(tmp_path: Path) -> None:
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    (claude_dir / "baton.yaml").write_text(_SPEC_YAML, encoding="utf-8")

    config = ManagerConfig.load(tmp_path)

    assert config.team.max_agents_by_complexity["medium"] == 5
    assert config.scoping.scope_expansion_policy == "queue_for_manager"


def test_claude_dir_takes_precedence_over_root(tmp_path: Path) -> None:
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    (claude_dir / "baton.yaml").write_text(
        "context:\n  default_step_token_budget: 5000\n", encoding="utf-8"
    )
    (tmp_path / "baton.yaml").write_text(
        "context:\n  default_step_token_budget: 9000\n", encoding="utf-8"
    )

    config = ManagerConfig.load(tmp_path)

    assert config.context.default_step_token_budget == 5000


def test_cli_overrides_beat_project_config(tmp_path: Path) -> None:
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    (claude_dir / "baton.yaml").write_text(
        "gates:\n  gate_scope: smoke\n  mode: full\n", encoding="utf-8"
    )

    config = ManagerConfig.load(
        tmp_path, cli_overrides={"gates": {"gate_scope": "full"}}
    )

    assert config.gates.gate_scope == "full"
    # Deep-merge preserves sibling keys not touched by the override.
    assert config.gates.mode == "full"


def test_invalid_policy_value_raises(tmp_path: Path) -> None:
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    (claude_dir / "baton.yaml").write_text(
        "policies:\n  phase_completion:\n    adversarial_review: sometimes\n",
        encoding="utf-8",
    )

    with pytest.raises(ManagerConfigError) as exc_info:
        ManagerConfig.load(tmp_path)

    message = str(exc_info.value)
    assert "sometimes" in message
    assert "always" in message
    assert "risk_based" in message
    assert "off" in message


def test_unknown_top_level_key_warns_not_crashes(tmp_path: Path) -> None:
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    (claude_dir / "baton.yaml").write_text(
        "frobnicate: 1\n"
        "default_agents:\n"
        "  backend: backend-engineer\n"
        "default_gates:\n"
        "  - pytest\n"
        "default_risk_level: LOW\n"
        "auto_route_rules:\n"
        "  - path_glob: '*.py'\n"
        "    agent: backend-engineer\n"
        "excluded_paths:\n"
        "  - node_modules\n"
        "default_isolation: worktree\n",
        encoding="utf-8",
    )

    config = ManagerConfig.load(tmp_path)

    assert any("frobnicate" in w for w in config.warnings)
    assert not any("default_agents" in w for w in config.warnings)
    assert not any("default_gates" in w for w in config.warnings)
    assert not any("default_risk_level" in w for w in config.warnings)
    assert not any("auto_route_rules" in w for w in config.warnings)
    assert not any("excluded_paths" in w for w in config.warnings)
    assert not any("default_isolation" in w for w in config.warnings)


def test_enabled_by_default_flag(tmp_path: Path) -> None:
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    (claude_dir / "baton.yaml").write_text(
        "manager_mode:\n  enabled_by_default: true\n", encoding="utf-8"
    )

    config = ManagerConfig.load(tmp_path)

    assert config.manager_mode.enabled_by_default is True


def test_round_trip() -> None:
    from agent_baton.core.config.manager import ContextConfig, ManagerModeConfig

    cfg = ManagerConfig(
        manager_mode=ManagerModeConfig(enabled_by_default=True),
        context=ContextConfig(default_step_token_budget=5000),
    )

    assert ManagerConfig.from_dict(cfg.to_dict()) == cfg


# ---------------------------------------------------------------------------
# talent_factory section (P5, docs/internal/talent-factory-contract.md §4)
# ---------------------------------------------------------------------------


def test_talent_factory_defaults_when_no_config_file(tmp_path: Path) -> None:
    """Backward compatible: a project with no talent_factory section (or no
    baton.yaml at all) still gets the documented conservative defaults."""
    config = ManagerConfig.load(tmp_path)
    tf = config.talent_factory

    assert tf.default_permitted_artifacts == ["agent", "knowledge_pack"]
    assert tf.retry_budget == 1
    assert tf.max_recursion_depth == 0
    assert tf.require_validation is True
    assert tf.on_validation_failure == "rollback"
    assert tf.name_collision_policy == "reject"
    assert tf.registry_reload == "immediate"
    # team.allow_talent_builder is the pre-existing master switch and stays
    # independent of the talent_factory section.
    assert config.team.allow_talent_builder is True


def test_loads_talent_factory_section_overrides(tmp_path: Path) -> None:
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    (claude_dir / "baton.yaml").write_text(
        "talent_factory:\n"
        "  retry_budget: 3\n"
        "  max_recursion_depth: 1\n"
        "  require_validation: false\n"
        "  on_validation_failure: quarantine\n"
        "  name_collision_policy: manual_review\n"
        "  registry_reload: next_plan\n",
        encoding="utf-8",
    )

    config = ManagerConfig.load(tmp_path)
    tf = config.talent_factory

    assert tf.retry_budget == 3
    assert tf.max_recursion_depth == 1
    assert tf.require_validation is False
    assert tf.on_validation_failure == "quarantine"
    assert tf.name_collision_policy == "manual_review"
    assert tf.registry_reload == "next_plan"


def test_talent_factory_partial_override_preserves_other_defaults(tmp_path: Path) -> None:
    """Deep-merge semantics apply to talent_factory like every other
    section -- overriding one field must not reset its siblings."""
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    (claude_dir / "baton.yaml").write_text(
        "talent_factory:\n  retry_budget: 5\n", encoding="utf-8",
    )

    config = ManagerConfig.load(tmp_path)

    assert config.talent_factory.retry_budget == 5
    assert config.talent_factory.name_collision_policy == "reject"
    assert config.talent_factory.registry_reload == "immediate"


def test_invalid_on_validation_failure_raises(tmp_path: Path) -> None:
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    (claude_dir / "baton.yaml").write_text(
        "talent_factory:\n  on_validation_failure: retry_forever\n", encoding="utf-8",
    )

    with pytest.raises(ManagerConfigError) as exc_info:
        ManagerConfig.load(tmp_path)

    assert "retry_forever" in str(exc_info.value)


def test_invalid_name_collision_policy_raises(tmp_path: Path) -> None:
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    (claude_dir / "baton.yaml").write_text(
        "talent_factory:\n  name_collision_policy: overwrite\n", encoding="utf-8",
    )

    with pytest.raises(ManagerConfigError) as exc_info:
        ManagerConfig.load(tmp_path)

    assert "overwrite" in str(exc_info.value)


def test_invalid_registry_reload_raises(tmp_path: Path) -> None:
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    (claude_dir / "baton.yaml").write_text(
        "talent_factory:\n  registry_reload: eventually\n", encoding="utf-8",
    )

    with pytest.raises(ManagerConfigError) as exc_info:
        ManagerConfig.load(tmp_path)

    assert "eventually" in str(exc_info.value)


def test_talent_factory_round_trips_through_to_dict_from_dict() -> None:
    from agent_baton.core.config.manager import TalentFactoryConfig

    cfg = ManagerConfig(
        talent_factory=TalentFactoryConfig(
            retry_budget=5, name_collision_policy="version_suffix", registry_reload="next_plan",
        ),
    )

    restored = ManagerConfig.from_dict(cfg.to_dict())

    assert restored == cfg
    assert restored.talent_factory.retry_budget == 5
    assert restored.talent_factory.name_collision_policy == "version_suffix"
    assert restored.talent_factory.registry_reload == "next_plan"


def test_spec_yaml_omits_talent_factory_and_still_gets_defaults(tmp_path: Path) -> None:
    """The canonical PRD §9.1 example (``_SPEC_YAML``) predates the
    talent_factory section (P5) -- loading it must populate the section
    from defaults rather than erroring or leaving it unset, exactly like
    any other project baton.yaml written before this feature existed."""
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    (claude_dir / "baton.yaml").write_text(_SPEC_YAML, encoding="utf-8")

    config = ManagerConfig.load(tmp_path)

    assert config.talent_factory.retry_budget == 1
    assert config.talent_factory.name_collision_policy == "reject"
    assert config.team.allow_talent_builder is True


def test_fake_home_fixture_is_effective(fake_home: Path, tmp_path: Path) -> None:
    """Proves the autouse ``fake_home`` fixture (tests/manager/conftest.py)
    actually redirects ``Path.home()`` -- not merely that no error occurs.

    Writes a decoy ``~/.baton/config.yaml`` into the fake home and asserts
    ``ManagerConfig.load()`` picks up its value. If the monkeypatch were
    vacuous (or not applied), this would instead read the real host's
    ``~/.baton/config.yaml`` (or fall back to the built-in default of
    12000), not the decoy value of 4242.
    """
    user_config_dir = fake_home / ".baton"
    user_config_dir.mkdir(parents=True)
    (user_config_dir / "config.yaml").write_text(
        "context:\n  default_step_token_budget: 4242\n", encoding="utf-8"
    )

    # tmp_path has no project-level baton.yaml of its own, so only the
    # (fake) user config layer can be the source of this value.
    config = ManagerConfig.load(tmp_path)

    assert config.context.default_step_token_budget == 4242
