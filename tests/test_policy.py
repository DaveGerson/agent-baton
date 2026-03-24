"""Tests for agent_baton.core.policy.PolicyEngine, PolicySet, PolicyRule, PolicyViolation."""
from __future__ import annotations

from pathlib import Path

import pytest

from agent_baton.core.govern.policy import (
    PolicyEngine,
    PolicyRule,
    PolicySet,
    PolicyViolation,
)


# ---------------------------------------------------------------------------
# PolicyRule — serialisation
# DECISION: Removed test_required_name_stored and test_optional_defaults —
# both are trivial dataclass field assertions. Kept roundtrip (proves
# to_dict + from_dict) and from_dict_defaults (distinct edge-case: missing keys).
# ---------------------------------------------------------------------------

class TestPolicyRuleFields:
    def test_to_dict_roundtrip(self) -> None:
        rule = PolicyRule(
            name="test_rule",
            description="desc",
            scope="*reviewer*",
            rule_type="tool_restrict",
            pattern="Bash,Write",
            severity="warn",
        )
        restored = PolicyRule.from_dict(rule.to_dict())
        assert restored.name == rule.name
        assert restored.scope == rule.scope
        assert restored.rule_type == rule.rule_type
        assert restored.pattern == rule.pattern
        assert restored.severity == rule.severity

    def test_from_dict_defaults_for_missing_keys(self) -> None:
        rule = PolicyRule.from_dict({"name": "minimal"})
        assert rule.description == ""
        assert rule.scope == "all"
        assert rule.severity == "block"


# ---------------------------------------------------------------------------
# PolicySet — serialisation
# DECISION: Removed test_name_and_description (trivial field storage) and
# test_to_dict_contains_rules (subset of roundtrip). Kept roundtrip and
# from_dict_empty_rules (distinct empty-list edge case).
# ---------------------------------------------------------------------------

class TestPolicySetFields:
    def test_from_dict_restores_rules(self) -> None:
        original = PolicySet(
            name="orig",
            description="test",
            rules=[
                PolicyRule(name="r1", scope="all", rule_type="path_block", pattern="**/.env"),
                PolicyRule(name="r2", scope="*reviewer*", rule_type="tool_restrict", pattern="Bash"),
            ],
        )
        restored = PolicySet.from_dict(original.to_dict())
        assert restored.name == "orig"
        assert len(restored.rules) == 2
        assert restored.rules[0].name == "r1"
        assert restored.rules[1].pattern == "Bash"

    def test_from_dict_empty_rules(self) -> None:
        ps = PolicySet.from_dict({"name": "empty"})
        assert ps.rules == []


# ---------------------------------------------------------------------------
# PolicyEngine — save / load / list roundtrip
# ---------------------------------------------------------------------------

class TestPolicyEnginePersistence:
    def test_save_creates_json_file(self, tmp_path: Path) -> None:
        engine = PolicyEngine(tmp_path)
        ps = PolicySet(name="my_policy", description="test")
        path = engine.save_preset(ps)
        assert path.exists()
        assert path.suffix == ".json"

    def test_save_load_roundtrip(self, tmp_path: Path) -> None:
        engine = PolicyEngine(tmp_path)
        ps = PolicySet(
            name="roundtrip",
            description="desc",
            rules=[PolicyRule(name="r", pattern="**/.env")],
        )
        engine.save_preset(ps)
        loaded = engine.load_preset("roundtrip")
        assert loaded is not None
        assert loaded.name == "roundtrip"
        assert len(loaded.rules) == 1
        assert loaded.rules[0].name == "r"

    def test_load_missing_preset_returns_none_for_custom_dir(self, tmp_path: Path) -> None:
        # Use a fresh empty dir that has no built-in presets shadowed
        empty_dir = tmp_path / "policies"
        empty_dir.mkdir()
        engine = PolicyEngine(empty_dir)
        # Should return None for a name that doesn't exist on disk or in built-ins
        result = engine.load_preset("nonexistent_xyz_preset")
        assert result is None

    def test_list_presets_includes_saved(self, tmp_path: Path) -> None:
        engine = PolicyEngine(tmp_path)
        engine.save_preset(PolicySet(name="custom_pol"))
        names = engine.list_presets()
        assert "custom_pol" in names

    def test_list_presets_includes_all_five_builtins(self, tmp_path: Path) -> None:
        engine = PolicyEngine(tmp_path)
        names = engine.list_presets()
        for expected in ("standard_dev", "data_analysis", "infrastructure", "regulated", "security"):
            assert expected in names

    def test_list_presets_empty_dir(self, tmp_path: Path) -> None:
        # Even with empty on-disk dir, built-in presets should be listed
        engine = PolicyEngine(tmp_path)
        assert len(engine.list_presets()) >= 5

    def test_load_builtin_preset_by_name(self, tmp_path: Path) -> None:
        engine = PolicyEngine(tmp_path)
        ps = engine.load_preset("standard_dev")
        assert ps is not None
        assert ps.name == "standard_dev"
        assert len(ps.rules) > 0


# ---------------------------------------------------------------------------
# PolicyEngine.create_standard_presets
# DECISION: Merged test_returns_five_presets, test_all_presets_have_rules,
# test_all_presets_have_descriptions into a single parametrized test.
# test_preset_names_correct is kept separate as it asserts the exact set.
# ---------------------------------------------------------------------------

EXPECTED_PRESETS = {"standard_dev", "data_analysis", "infrastructure", "regulated", "security"}


class TestCreateStandardPresets:
    def test_preset_names_correct(self, tmp_path: Path) -> None:
        engine = PolicyEngine(tmp_path)
        names = {p.name for p in engine.create_standard_presets()}
        assert names == EXPECTED_PRESETS

    @pytest.mark.parametrize("preset_name", sorted(EXPECTED_PRESETS))
    def test_preset_has_rules_and_description(self, tmp_path: Path, preset_name: str) -> None:
        engine = PolicyEngine(tmp_path)
        presets = {p.name: p for p in engine.create_standard_presets()}
        preset = presets[preset_name]
        assert len(preset.rules) > 0, f"Preset '{preset_name}' has no rules"
        assert preset.description != "", f"Preset '{preset_name}' has no description"


# ---------------------------------------------------------------------------
# PolicyEngine.evaluate — path_block rules
# ---------------------------------------------------------------------------

class TestEvaluatePathBlock:
    def test_no_violations_for_compliant_paths(self, tmp_path: Path) -> None:
        engine = PolicyEngine(tmp_path)
        ps = PolicySet(
            name="p",
            rules=[PolicyRule(name="block_env", rule_type="path_block", pattern="**/.env")],
        )
        violations = engine.evaluate(ps, "backend-engineer", ["src/app.py"], [])
        assert violations == []

    def test_violation_for_blocked_path(self, tmp_path: Path) -> None:
        engine = PolicyEngine(tmp_path)
        ps = PolicySet(
            name="p",
            rules=[PolicyRule(name="block_env", rule_type="path_block", pattern="**/.env")],
        )
        violations = engine.evaluate(ps, "backend-engineer", ["config/.env"], [])
        assert len(violations) == 1
        assert violations[0].rule.name == "block_env"
        assert violations[0].agent_name == "backend-engineer"

    def test_multiple_path_violations(self, tmp_path: Path) -> None:
        engine = PolicyEngine(tmp_path)
        ps = PolicySet(
            name="p",
            rules=[PolicyRule(name="block_secrets", rule_type="path_block", pattern="secrets/*")],
        )
        violations = engine.evaluate(
            ps, "agent",
            ["secrets/api.key", "secrets/db.key", "src/app.py"],
            [],
        )
        assert len(violations) == 2


# ---------------------------------------------------------------------------
# PolicyEngine.evaluate — tool_restrict rules
# ---------------------------------------------------------------------------

class TestEvaluateToolRestrict:
    def test_no_violation_when_tool_not_restricted(self, tmp_path: Path) -> None:
        engine = PolicyEngine(tmp_path)
        ps = PolicySet(
            name="p",
            rules=[PolicyRule(name="no_bash", rule_type="tool_restrict", pattern="Bash")],
        )
        violations = engine.evaluate(ps, "reviewer", [], ["Read", "Glob", "Grep"])
        assert violations == []

    def test_violation_when_restricted_tool_present(self, tmp_path: Path) -> None:
        engine = PolicyEngine(tmp_path)
        ps = PolicySet(
            name="p",
            rules=[PolicyRule(name="no_bash", rule_type="tool_restrict", pattern="Bash")],
        )
        violations = engine.evaluate(ps, "reviewer", [], ["Read", "Bash"])
        assert len(violations) == 1
        assert violations[0].rule.name == "no_bash"

    def test_multiple_tools_in_pattern(self, tmp_path: Path) -> None:
        engine = PolicyEngine(tmp_path)
        ps = PolicySet(
            name="p",
            rules=[PolicyRule(name="read_only", rule_type="tool_restrict", pattern="Write,Bash")],
        )
        violations = engine.evaluate(ps, "reviewer", [], ["Read", "Write", "Bash"])
        assert len(violations) == 2


# ---------------------------------------------------------------------------
# PolicyEngine.evaluate — scope matching
# DECISION: Merged 6 scope tests into 1 parametrized test covering
# (scope_pattern, agent_name, path_pattern, paths, should_match).
# ---------------------------------------------------------------------------

class TestEvaluateScope:
    @pytest.mark.parametrize("scope,agent,paths,expect_violations", [
        # "all" scope applies to any agent
        ("all", "backend-engineer", ["x/.env"], 1),
        ("all", "frontend-engineer", ["x/.env"], 1),
        # wildcard scope only matches matching agents
        ("*reviewer*", "security-reviewer", [".env"], 1),
        ("*reviewer*", "backend-engineer", ["x/.env"], 0),
        # exact scope only matches exact name
        ("devops-engineer", "devops-engineer", ["src/main.py"], 1),
        ("devops-engineer", "backend-engineer", ["src/main.py"], 0),
    ])
    def test_scope_matching(
        self,
        tmp_path: Path,
        scope: str,
        agent: str,
        paths: list[str],
        expect_violations: int,
    ) -> None:
        engine = PolicyEngine(tmp_path)
        ps = PolicySet(
            name="p",
            rules=[PolicyRule(name="r", scope=scope, rule_type="path_block", pattern="**/.env" if scope == "all" else "*.env" if scope == "*reviewer*" else "src/*")],
        )
        violations = engine.evaluate(ps, agent, paths, [])
        assert len(violations) == expect_violations


# ---------------------------------------------------------------------------
# PolicyEngine.evaluate — require_agent / require_gate (structural checks)
# ---------------------------------------------------------------------------

class TestEvaluateRequire:
    def test_require_agent_produces_violation(self, tmp_path: Path) -> None:
        engine = PolicyEngine(tmp_path)
        ps = PolicySet(
            name="p",
            rules=[PolicyRule(name="need_auditor", rule_type="require_agent", pattern="auditor")],
        )
        violations = engine.evaluate(ps, "backend-engineer", [], [])
        assert len(violations) == 1
        assert "auditor" in violations[0].details

    def test_require_gate_produces_violation(self, tmp_path: Path) -> None:
        engine = PolicyEngine(tmp_path)
        ps = PolicySet(
            name="p",
            rules=[PolicyRule(name="audit_trail", rule_type="require_gate", pattern="audit_trail")],
        )
        violations = engine.evaluate(ps, "agent", [], [])
        assert len(violations) == 1

    def test_empty_policy_no_violations(self, tmp_path: Path) -> None:
        engine = PolicyEngine(tmp_path)
        ps = PolicySet(name="empty")
        violations = engine.evaluate(ps, "agent", ["src/app.py"], ["Read", "Write"])
        assert violations == []
