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
