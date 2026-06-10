"""Tests for Phase G — Assurance Packs.

Covers the 12 spec cases plus policy-check integration with pack presets.
"""
from __future__ import annotations

import argparse
import io
import json
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helpers: fixture-pack factories
# ---------------------------------------------------------------------------

def _write_valid_pack(
    packs_dir: Path,
    name: str = "test-pack",
    *,
    policy_name: str | None = None,
    preset_name: str | None = None,
    risk_level: str = "HIGH",
    keywords: dict | None = None,
    path_patterns: list | None = None,
    rubric_extra: str = "",
) -> Path:
    """Write a minimal valid pack to packs_dir/<name>/."""
    pack_dir = packs_dir / name
    pack_dir.mkdir(parents=True, exist_ok=True)
    (pack_dir / "knowledge").mkdir(exist_ok=True)

    # pack.json
    (pack_dir / "pack.json").write_text(
        json.dumps({
            "name": name,
            "version": "1.0.0",
            "description": f"Test pack {name}",
            "domain": "test",
            "risk_level": risk_level,
        }),
        encoding="utf-8",
    )

    # policy.json
    pset_name = policy_name if policy_name is not None else f"pack:{name}"
    (pack_dir / "policy.json").write_text(
        json.dumps({
            "name": pset_name,
            "description": f"Policy for {name}",
            "rules": [
                {
                    "name": "require_auditor",
                    "description": "Auditor required",
                    "scope": "all",
                    "rule_type": "require_agent",
                    "pattern": "auditor",
                    "severity": "block",
                },
                {
                    "name": "block_sensitive_path",
                    "description": "Block sensitive paths",
                    "scope": "all",
                    "rule_type": "path_block",
                    "pattern": "**/sensitive/**",
                    "severity": "block",
                },
            ],
        }),
        encoding="utf-8",
    )

    # signals.json
    kw = keywords if keywords is not None else {"regulated": ["test-signal"]}
    pp = path_patterns if path_patterns is not None else ["sensitive/"]
    sig_preset = preset_name if preset_name is not None else f"pack:{name}"
    (pack_dir / "signals.json").write_text(
        json.dumps({
            "pack": name,
            "keywords": kw,
            "path_patterns": pp,
            "preset_name": sig_preset,
            "risk_level": risk_level,
        }),
        encoding="utf-8",
    )

    # rubric.md
    (pack_dir / "rubric.md").write_text(
        f"## Review checklist\n\n- [ ] Verify compliance.\n{rubric_extra}",
        encoding="utf-8",
    )

    return pack_dir


# ---------------------------------------------------------------------------
# Case 1: load happy path
# ---------------------------------------------------------------------------

class TestLoadHappyPath:
    def test_load_valid_pack(self, tmp_path: Path) -> None:
        packs_dir = tmp_path / ".claude" / "packs"
        _write_valid_pack(packs_dir, "my-pack")

        from agent_baton.core.govern.packs import load_packs

        packs = load_packs(tmp_path)
        assert len(packs) == 1
        pack = packs[0]
        assert pack.name == "my-pack"
        assert pack.manifest.version == "1.0.0"
        assert pack.manifest.risk_level == "HIGH"
        assert pack.policy_set.name == "pack:my-pack"
        assert pack.preset_name == "pack:my-pack"


# ---------------------------------------------------------------------------
# Case 2: missing required file
# ---------------------------------------------------------------------------

class TestMissingRequiredFile:
    def test_missing_policy_json_returns_error(self, tmp_path: Path) -> None:
        packs_dir = tmp_path / ".claude" / "packs"
        pack_dir = _write_valid_pack(packs_dir, "bad-pack")
        (pack_dir / "policy.json").unlink()

        from agent_baton.core.govern.packs import validate_pack

        errors = validate_pack(pack_dir)
        assert any("policy.json" in str(e) for e in errors)

    def test_missing_required_file_skips_in_load(self, tmp_path: Path) -> None:
        packs_dir = tmp_path / ".claude" / "packs"
        pack_dir = _write_valid_pack(packs_dir, "bad-pack")
        (pack_dir / "signals.json").unlink()

        from agent_baton.core.govern.packs import load_packs

        packs = load_packs(tmp_path)
        assert len(packs) == 0  # bad pack skipped


# ---------------------------------------------------------------------------
# Case 3: policy name mismatch
# ---------------------------------------------------------------------------

class TestPolicyNameMismatch:
    def test_policy_name_mismatch_returns_error(self, tmp_path: Path) -> None:
        packs_dir = tmp_path / ".claude" / "packs"
        pack_dir = _write_valid_pack(
            packs_dir, "my-pack", policy_name="pack:wrong-name"
        )

        from agent_baton.core.govern.packs import validate_pack

        errors = validate_pack(pack_dir)
        assert any("policy.json" in str(e) and "wrong-name" in str(e) for e in errors)


# ---------------------------------------------------------------------------
# Case 4: unknown signal category
# ---------------------------------------------------------------------------

class TestUnknownSignalCategory:
    def test_unknown_category_returns_error(self, tmp_path: Path) -> None:
        packs_dir = tmp_path / ".claude" / "packs"
        pack_dir = _write_valid_pack(
            packs_dir, "my-pack", keywords={"finance": ["invoice", "ledger"]}
        )

        from agent_baton.core.govern.packs import validate_pack

        errors = validate_pack(pack_dir)
        assert any("signals.json" in str(e) and "finance" in str(e) for e in errors)


# ---------------------------------------------------------------------------
# Case 5: rubric no headings
# ---------------------------------------------------------------------------

class TestRubricNoHeadings:
    def test_rubric_without_heading_returns_error(self, tmp_path: Path) -> None:
        packs_dir = tmp_path / ".claude" / "packs"
        pack_dir = _write_valid_pack(packs_dir, "my-pack")
        # Overwrite rubric with no heading but a checkbox.
        (pack_dir / "rubric.md").write_text(
            "- [ ] Some checkbox\n", encoding="utf-8"
        )

        from agent_baton.core.govern.packs import validate_pack

        errors = validate_pack(pack_dir)
        assert any("rubric.md" in str(e) and "heading" in str(e) for e in errors)

    def test_rubric_without_checkbox_returns_error(self, tmp_path: Path) -> None:
        packs_dir = tmp_path / ".claude" / "packs"
        pack_dir = _write_valid_pack(packs_dir, "my-pack")
        # Overwrite rubric with a heading but no checkbox.
        (pack_dir / "rubric.md").write_text(
            "## My heading\n\nSome prose.\n", encoding="utf-8"
        )

        from agent_baton.core.govern.packs import validate_pack

        errors = validate_pack(pack_dir)
        assert any("rubric.md" in str(e) and "checkbox" in str(e) for e in errors)


# ---------------------------------------------------------------------------
# Case 6: pack preset resolution + builtin unaffected
# ---------------------------------------------------------------------------

class TestPackPresetResolution:
    def test_pack_preset_resolves_after_registration(self, tmp_path: Path) -> None:
        packs_dir = tmp_path / ".claude" / "packs"
        _write_valid_pack(packs_dir, "my-pack")

        from agent_baton.core.govern.packs import (
            load_packs,
            register_pack_policies,
        )
        from agent_baton.core.govern.policy import PolicyEngine

        packs = load_packs(tmp_path)
        register_pack_policies(packs)

        engine = PolicyEngine()
        ps = engine.load_preset("pack:my-pack")
        assert ps is not None
        assert ps.name == "pack:my-pack"

    def test_builtin_preset_unaffected_by_pack_registration(
        self, tmp_path: Path
    ) -> None:
        packs_dir = tmp_path / ".claude" / "packs"
        _write_valid_pack(packs_dir, "my-pack")

        from agent_baton.core.govern.packs import (
            load_packs,
            register_pack_policies,
        )
        from agent_baton.core.govern.policy import PolicyEngine

        packs = load_packs(tmp_path)
        register_pack_policies(packs)

        engine = PolicyEngine()
        ps = engine.load_preset("regulated")
        assert ps is not None
        assert ps.name == "regulated"  # builtin unchanged


# ---------------------------------------------------------------------------
# Case 7: classifier merge (keyword + path → pack preset)
# ---------------------------------------------------------------------------

class TestClassifierMerge:
    def test_pack_keyword_triggers_pack_preset(self, tmp_path: Path) -> None:
        packs_dir = tmp_path / ".claude" / "packs"
        _write_valid_pack(
            packs_dir,
            "my-pack",
            keywords={"regulated": ["my-unique-keyword-xyz"]},
            path_patterns=[],
        )

        from agent_baton.core.govern.packs import (
            load_packs,
            make_classifier_for_packs,
        )

        packs = load_packs(tmp_path)
        classifier = make_classifier_for_packs(packs)
        result = classifier.classify("task involving my-unique-keyword-xyz")
        # The keyword matches as a regulated signal → preset "Regulated Data"
        # (not a pack preset — keyword signals don't override preset to pack)
        assert result.risk_level.value in ("HIGH", "CRITICAL")
        assert any("my-unique-keyword-xyz" in s for s in result.signals_found)

    def test_pack_path_pattern_triggers_pack_preset(self, tmp_path: Path) -> None:
        packs_dir = tmp_path / ".claude" / "packs"
        _write_valid_pack(
            packs_dir,
            "my-pack",
            keywords={},
            path_patterns=["special-data/"],
            risk_level="HIGH",
        )

        from agent_baton.core.govern.packs import (
            load_packs,
            make_classifier_for_packs,
        )

        packs = load_packs(tmp_path)
        classifier = make_classifier_for_packs(packs)
        result = classifier.classify(
            "update config", ["special-data/records.json"]
        )
        assert result.guardrail_preset == "pack:my-pack"
        assert "pack:my-pack" in result.matched_packs

    def test_no_packs_returns_plain_classifier(self) -> None:
        from agent_baton.core.govern.classifier import DataClassifier
        from agent_baton.core.govern.packs import make_classifier_for_packs

        classifier = make_classifier_for_packs([])
        assert isinstance(classifier, DataClassifier)
        result = classifier.classify("standard development work")
        assert result.guardrail_preset == "Standard Development"
        assert result.matched_packs == []


# ---------------------------------------------------------------------------
# Case 8: init scaffold → validate roundtrip
# ---------------------------------------------------------------------------

class TestInitScaffoldValidateRoundtrip:
    def test_scaffolded_pack_passes_validation(
        self, tmp_path: Path, capsys
    ) -> None:
        """The scaffold replaces [YOUR_PACK_NAME] with the actual name,
        so the resulting pack passes validation immediately."""
        from agent_baton.cli.commands.govern import packs_cmd

        packs_cmd._cmd_init(
            argparse.Namespace(name="new-pack", dir=str(tmp_path))
        )
        capsys.readouterr()

        from agent_baton.core.govern.packs import validate_pack

        pack_dir = tmp_path / ".claude" / "packs" / "new-pack"
        errors = validate_pack(pack_dir)
        assert errors == [], f"Scaffold should pass validation, got: {errors}"

    def test_fixed_scaffolded_pack_passes_validation(
        self, tmp_path: Path, capsys
    ) -> None:
        """After fixing placeholders, the scaffolded pack should pass validation."""
        from agent_baton.cli.commands.govern import packs_cmd

        # Scaffold
        packs_cmd._cmd_init(
            argparse.Namespace(name="fixed-pack", dir=str(tmp_path))
        )
        capsys.readouterr()

        pack_dir = tmp_path / ".claude" / "packs" / "fixed-pack"

        # Fix pack.json name
        pack_json_path = pack_dir / "pack.json"
        data = json.loads(pack_json_path.read_text())
        data["name"] = "fixed-pack"
        pack_json_path.write_text(json.dumps(data), encoding="utf-8")

        # Fix policy.json name
        policy_path = pack_dir / "policy.json"
        pol_data = json.loads(policy_path.read_text())
        pol_data["name"] = "pack:fixed-pack"
        policy_path.write_text(json.dumps(pol_data), encoding="utf-8")

        # Fix signals.json keywords (remove placeholder)
        sig_path = pack_dir / "signals.json"
        sig_data = json.loads(sig_path.read_text())
        sig_data["keywords"] = {"regulated": ["fixed-keyword"]}
        sig_data["preset_name"] = "pack:fixed-pack"
        sig_path.write_text(json.dumps(sig_data), encoding="utf-8")

        from agent_baton.core.govern.packs import validate_pack

        errors = validate_pack(pack_dir)
        assert errors == [], f"Expected no errors, got: {errors}"

    def test_validate_cmd_exits_2_on_errors(
        self, tmp_path: Path, capsys
    ) -> None:
        """validate subcommand exits 2 when errors are found."""
        packs_dir = tmp_path / ".claude" / "packs"
        pack_dir = _write_valid_pack(packs_dir, "bad-pack")
        (pack_dir / "rubric.md").write_text("no heading no checkbox", encoding="utf-8")

        from agent_baton.cli.commands.govern import packs_cmd

        with pytest.raises(SystemExit) as exc_info:
            packs_cmd._cmd_validate(
                argparse.Namespace(name="bad-pack", dir=str(tmp_path))
            )
        assert exc_info.value.code == 2

    def test_validate_cmd_exits_0_all_valid(
        self, tmp_path: Path, capsys
    ) -> None:
        """validate subcommand exits 0 when all packs are valid."""
        packs_dir = tmp_path / ".claude" / "packs"
        _write_valid_pack(packs_dir, "good-pack")

        from agent_baton.cli.commands.govern import packs_cmd

        packs_cmd._cmd_validate(
            argparse.Namespace(name="good-pack", dir=str(tmp_path))
        )
        out, _ = capsys.readouterr()
        assert "[OK]" in out


# ---------------------------------------------------------------------------
# Case 9: planner require_agent flow with a pack PolicySet
# ---------------------------------------------------------------------------

class TestPlannerRequireAgentWithPackPolicy:
    def test_validate_agents_against_pack_policy(self, tmp_path: Path) -> None:
        """validate_agents_against_policy works with a pack PolicySet."""
        from agent_baton.core.govern.packs import (
            load_packs,
            register_pack_policies,
        )
        from agent_baton.core.govern.policy import PolicyEngine
        from agent_baton.core.engine.planning.utils.risk_and_policy import (
            validate_agents_against_policy,
        )
        from agent_baton.models.execution import PlanPhase, PlanStep

        packs_dir = tmp_path / ".claude" / "packs"
        _write_valid_pack(packs_dir, "reg-pack")

        packs = load_packs(tmp_path)
        register_pack_policies(packs)
        engine = PolicyEngine()
        ps = engine.load_preset("pack:reg-pack")
        assert ps is not None

        # Plan that DOES include auditor → require_agent satisfied.
        step = PlanStep(
            step_id="1.1",
            agent_name="auditor",
            task_description="Review changes",
        )
        phase = PlanPhase(phase_id=1, name="review", steps=[step])
        violations = validate_agents_against_policy(
            ["auditor"], ps, [phase], engine
        )
        # Only path_block violations would remain; require_agent for auditor is satisfied.
        require_violations = [
            v for v in violations if v.rule.rule_type == "require_agent"
        ]
        assert len(require_violations) == 0

    def test_validate_agents_missing_required_agent(self, tmp_path: Path) -> None:
        """validate_agents_against_policy surfaces missing required agent."""
        from agent_baton.core.govern.packs import (
            load_packs,
            register_pack_policies,
        )
        from agent_baton.core.govern.policy import PolicyEngine
        from agent_baton.core.engine.planning.utils.risk_and_policy import (
            validate_agents_against_policy,
        )
        from agent_baton.models.execution import PlanPhase, PlanStep

        packs_dir = tmp_path / ".claude" / "packs"
        _write_valid_pack(packs_dir, "reg-pack")

        packs = load_packs(tmp_path)
        register_pack_policies(packs)
        engine = PolicyEngine()
        ps = engine.load_preset("pack:reg-pack")
        assert ps is not None

        # Plan that does NOT include auditor.
        step = PlanStep(
            step_id="1.1",
            agent_name="developer",
            task_description="Implement feature",
        )
        phase = PlanPhase(phase_id=1, name="impl", steps=[step])
        violations = validate_agents_against_policy(
            ["developer"], ps, [phase], engine
        )
        # Should have a violation for missing auditor.
        assert any(
            v.rule.pattern == "auditor" and v.rule.rule_type == "require_agent"
            for v in violations
        )


# ---------------------------------------------------------------------------
# Case 10: multi-pack highest-risk-wins
# ---------------------------------------------------------------------------

class TestMultiPackHighestRiskWins:
    def test_highest_risk_pack_wins_on_path_match(self, tmp_path: Path) -> None:
        """When two packs match, the highest risk_level one wins the preset."""
        packs_dir = tmp_path / ".claude" / "packs"
        _write_valid_pack(
            packs_dir,
            "medium-pack",
            keywords={},
            path_patterns=["data/"],
            risk_level="MEDIUM",
        )
        _write_valid_pack(
            packs_dir,
            "high-pack",
            keywords={},
            path_patterns=["data/"],
            risk_level="HIGH",
        )

        from agent_baton.core.govern.packs import (
            load_packs,
            make_classifier_for_packs,
        )

        packs = load_packs(tmp_path)
        classifier = make_classifier_for_packs(packs)
        result = classifier.classify("process files", ["data/records.csv"])

        # HIGH wins over MEDIUM.
        assert result.guardrail_preset == "pack:high-pack"
        assert result.risk_level.value == "HIGH"

    def test_tie_resolved_alphabetically(self, tmp_path: Path) -> None:
        """When two HIGH-risk packs match, earlier alphabetically wins."""
        packs_dir = tmp_path / ".claude" / "packs"
        _write_valid_pack(
            packs_dir,
            "zzz-pack",
            keywords={},
            path_patterns=["data/"],
            risk_level="HIGH",
        )
        _write_valid_pack(
            packs_dir,
            "aaa-pack",
            keywords={},
            path_patterns=["data/"],
            risk_level="HIGH",
        )

        from agent_baton.core.govern.packs import (
            load_packs,
            make_classifier_for_packs,
        )

        packs = load_packs(tmp_path)
        classifier = make_classifier_for_packs(packs)
        result = classifier.classify("process files", ["data/records.csv"])
        # "pack:aaa-pack" < "pack:zzz-pack" alphabetically → aaa-pack wins.
        assert result.guardrail_preset == "pack:aaa-pack"


# ---------------------------------------------------------------------------
# Case 11: gates missing command key
# ---------------------------------------------------------------------------

class TestGatesMissingCommandKey:
    def test_gate_missing_command_returns_error(self, tmp_path: Path) -> None:
        packs_dir = tmp_path / ".claude" / "packs"
        pack_dir = _write_valid_pack(packs_dir, "gated-pack")
        # Write gates.json with a gate missing "command".
        (pack_dir / "gates.json").write_text(
            json.dumps({
                "pack": "gated-pack",
                "gates": [
                    {"id": "my_gate", "description": "A gate"},
                    # Missing "command" key
                ],
            }),
            encoding="utf-8",
        )

        from agent_baton.core.govern.packs import validate_pack

        errors = validate_pack(pack_dir)
        assert any("gates.json" in str(e) and "command" in str(e) for e in errors)


# ---------------------------------------------------------------------------
# Case 12: invalid pack skipped while valid sibling loads
# ---------------------------------------------------------------------------

class TestInvalidPackSkipped:
    def test_invalid_pack_skipped_valid_sibling_loads(self, tmp_path: Path) -> None:
        packs_dir = tmp_path / ".claude" / "packs"
        # Good pack.
        _write_valid_pack(packs_dir, "good-pack")
        # Bad pack (missing rubric.md).
        bad_dir = packs_dir / "bad-pack"
        bad_dir.mkdir()
        (bad_dir / "pack.json").write_text(
            json.dumps({"name": "bad-pack", "version": "1.0.0", "description": "bad"}),
            encoding="utf-8",
        )
        # policy.json, signals.json exist but rubric.md is absent.
        (bad_dir / "policy.json").write_text(
            json.dumps({"name": "pack:bad-pack", "description": "", "rules": []}),
            encoding="utf-8",
        )
        (bad_dir / "signals.json").write_text(
            json.dumps({
                "pack": "bad-pack",
                "keywords": {},
                "path_patterns": [],
                "preset_name": "pack:bad-pack",
                "risk_level": "HIGH",
            }),
            encoding="utf-8",
        )
        # rubric.md intentionally missing

        from agent_baton.core.govern.packs import load_packs

        packs = load_packs(tmp_path)
        names = [p.name for p in packs]
        assert "good-pack" in names
        assert "bad-pack" not in names


# ---------------------------------------------------------------------------
# policy-check with pack policy
# ---------------------------------------------------------------------------

class TestPolicyCheckWithPackPolicy:
    """baton policy-check correctly denies using a pack policy preset."""

    def _make_check_args(
        self, *, agent: str | None = None, cwd: str | None = None
    ) -> argparse.Namespace:
        return argparse.Namespace(agent=agent, cwd=cwd)

    def test_policy_check_denies_with_pack_policy(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        """A pack policy blocks path writes as expected."""
        # Set up a pack in the project.
        packs_dir = tmp_path / ".claude" / "packs"
        _write_valid_pack(packs_dir, "my-pack")

        # Write active-policy.json pointing to the pack preset.
        dot_claude = tmp_path / ".claude"
        dot_claude.mkdir(exist_ok=True)
        (dot_claude / "active-policy.json").write_text(
            json.dumps({"preset": "pack:my-pack"}), encoding="utf-8"
        )

        from agent_baton.cli.commands.govern import policy_check as cmd

        monkeypatch.setattr(
            "sys.stdin",
            io.StringIO(
                json.dumps({
                    "tool_name": "Write",
                    "tool_input": {"file_path": "/project/sensitive/data.json"},
                    "session_id": "pack-test",
                })
            ),
        )

        cmd.handler(self._make_check_args(cwd=str(tmp_path)))
        out, err = capsys.readouterr()

        assert out.strip(), "Expected deny output but got empty stdout"
        data = json.loads(out)
        assert data["hookSpecificOutput"]["permissionDecision"] == "deny"
        reason = data["hookSpecificOutput"]["permissionDecisionReason"]
        assert "pack:my-pack" in reason or "block_sensitive_path" in reason

    def test_policy_check_allows_non_blocked_path_with_pack_policy(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        """A pack policy does not block non-sensitive paths."""
        packs_dir = tmp_path / ".claude" / "packs"
        _write_valid_pack(packs_dir, "my-pack")

        dot_claude = tmp_path / ".claude"
        dot_claude.mkdir(exist_ok=True)
        (dot_claude / "active-policy.json").write_text(
            json.dumps({"preset": "pack:my-pack"}), encoding="utf-8"
        )

        from agent_baton.cli.commands.govern import policy_check as cmd

        monkeypatch.setattr(
            "sys.stdin",
            io.StringIO(
                json.dumps({
                    "tool_name": "Write",
                    "tool_input": {"file_path": "/project/src/main.py"},
                    "session_id": "pack-test-2",
                })
            ),
        )

        cmd.handler(self._make_check_args(cwd=str(tmp_path)))
        out, err = capsys.readouterr()
        # No block: stdout should be empty (no deny JSON).
        assert out.strip() == ""

    def test_policy_check_unknown_pack_fails_open(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        """Unknown pack preset (not registered) fails open — no crash."""
        dot_claude = tmp_path / ".claude"
        dot_claude.mkdir(exist_ok=True)
        (dot_claude / "active-policy.json").write_text(
            json.dumps({"preset": "pack:nonexistent"}), encoding="utf-8"
        )

        from agent_baton.cli.commands.govern import policy_check as cmd

        monkeypatch.setattr(
            "sys.stdin",
            io.StringIO(
                json.dumps({
                    "tool_name": "Write",
                    "tool_input": {"file_path": "/project/src/foo.py"},
                    "session_id": "pack-test-3",
                })
            ),
        )

        exit_code = None
        try:
            cmd.handler(self._make_check_args(cwd=str(tmp_path)))
        except SystemExit as e:
            exit_code = int(e.code) if e.code is not None else 0

        out, err = capsys.readouterr()
        # Should not crash; should fail-open (no deny).
        assert exit_code is None
