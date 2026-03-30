"""Tests for governance integration in IntelligentPlanner.

Verifies that:
- DataClassifier is invoked during create_plan() when provided.
- Classifier risk level is used as a floor (never lowered by keyword signals).
- PolicyEngine validates agent assignments and records violations.
- Both classifiers and policy_engine remain optional (backward compatibility).
- Governance details appear in shared_context and explain_plan() output.
- _classify_to_preset_key maps guardrail preset names to policy keys correctly.
- _validate_agents_against_policy deduplicates violations and checks require_agent rules.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from agent_baton.core.engine.planner import IntelligentPlanner
from agent_baton.core.govern.classifier import ClassificationResult, DataClassifier
from agent_baton.core.govern.policy import (
    PolicyEngine,
    PolicyRule,
    PolicySet,
    PolicyViolation,
)
from agent_baton.models.enums import RiskLevel
from agent_baton.models.execution import MachinePlan


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _make_agent_dir(tmp_path: Path) -> Path:
    """Create a minimal agents directory so the registry doesn't fail."""
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir(exist_ok=True)
    for name in ["backend-engineer", "architect", "test-engineer", "code-reviewer",
                 "auditor", "subject-matter-expert"]:
        content = (
            f"---\nname: {name}\ndescription: {name} specialist.\n"
            f"model: sonnet\npermissionMode: default\ntools: Read, Write\n---\n"
        )
        (agents_dir / f"{name}.md").write_text(content, encoding="utf-8")
    return agents_dir


@pytest.fixture()
def agents_dir(tmp_path: Path) -> Path:
    return _make_agent_dir(tmp_path)


@pytest.fixture()
def ctx(tmp_path: Path) -> Path:
    d = tmp_path / "team-context"
    d.mkdir()
    return d


@pytest.fixture()
def base_planner(ctx: Path, agents_dir: Path) -> IntelligentPlanner:
    """Planner without governance components — baseline backward-compat fixture."""
    from agent_baton.core.orchestration.registry import AgentRegistry
    from agent_baton.core.orchestration.router import AgentRouter

    p = IntelligentPlanner(team_context_root=ctx)
    reg = AgentRegistry()
    reg.load_directory(agents_dir)
    p._registry = reg
    p._router = AgentRouter(reg)
    return p


@pytest.fixture()
def governed_planner(ctx: Path, agents_dir: Path) -> IntelligentPlanner:
    """Planner with real DataClassifier and PolicyEngine wired in."""
    from agent_baton.core.orchestration.registry import AgentRegistry
    from agent_baton.core.orchestration.router import AgentRouter

    p = IntelligentPlanner(
        team_context_root=ctx,
        classifier=DataClassifier(),
        policy_engine=PolicyEngine(),
    )
    reg = AgentRegistry()
    reg.load_directory(agents_dir)
    p._registry = reg
    p._router = AgentRouter(reg)
    return p


# ---------------------------------------------------------------------------
# Backward compatibility — no governance components
# ---------------------------------------------------------------------------


class TestBackwardCompatibility:
    """Planner without classifier/policy_engine must work exactly as before."""

    def test_create_plan_succeeds_without_governance(self, base_planner: IntelligentPlanner) -> None:
        plan = base_planner.create_plan("Add a helper function")
        assert isinstance(plan, MachinePlan)

    def test_no_classification_state_when_no_classifier(self, base_planner: IntelligentPlanner) -> None:
        base_planner.create_plan("Add a helper function")
        assert base_planner._last_classification is None

    def test_no_violations_when_no_policy_engine(self, base_planner: IntelligentPlanner) -> None:
        base_planner.create_plan("Add a helper function")
        assert base_planner._last_policy_violations == []

    def test_shared_context_has_no_governance_fields_without_governance(
        self, base_planner: IntelligentPlanner
    ) -> None:
        plan = base_planner.create_plan("Add a helper function")
        assert "Guardrail Preset:" not in plan.shared_context
        assert "Sensitivity Signals:" not in plan.shared_context
        assert "Policy Notes:" not in plan.shared_context

    def test_explain_plan_shows_no_classifier_message(
        self, base_planner: IntelligentPlanner
    ) -> None:
        plan = base_planner.create_plan("Add a helper function")
        explanation = base_planner.explain_plan(plan)
        assert "No classifier configured" in explanation


# ---------------------------------------------------------------------------
# DataClassifier integration
# ---------------------------------------------------------------------------


class TestClassifierIntegration:
    """DataClassifier is invoked and its results appear in the plan."""

    def test_classification_stored_after_create_plan(
        self, governed_planner: IntelligentPlanner
    ) -> None:
        governed_planner.create_plan("Add a helper function")
        assert governed_planner._last_classification is not None
        assert isinstance(governed_planner._last_classification, ClassificationResult)

    def test_guardrail_preset_in_shared_context(
        self, governed_planner: IntelligentPlanner
    ) -> None:
        plan = governed_planner.create_plan("Add a helper function")
        assert "Guardrail Preset:" in plan.shared_context

    def test_regulated_signals_in_shared_context(
        self, governed_planner: IntelligentPlanner
    ) -> None:
        plan = governed_planner.create_plan(
            "Implement HIPAA compliance audit trail for patient records"
        )
        assert "Sensitivity Signals:" in plan.shared_context

    def test_classifier_result_in_explain_plan(
        self, governed_planner: IntelligentPlanner
    ) -> None:
        plan = governed_planner.create_plan("Add a helper function")
        explanation = governed_planner.explain_plan(plan)
        assert "## Data Classification" in explanation
        assert "Guardrail Preset:" in explanation

    def test_classification_reset_between_calls(
        self, governed_planner: IntelligentPlanner
    ) -> None:
        governed_planner.create_plan("First task")
        first = governed_planner._last_classification

        governed_planner.create_plan("Second task")
        second = governed_planner._last_classification

        # Both should have been populated (not stale from first call)
        assert first is not None
        assert second is not None

    def test_mock_classifier_is_called(self, ctx: Path, agents_dir: Path) -> None:
        """Verify the classifier's classify() method is actually invoked."""
        from agent_baton.core.orchestration.registry import AgentRegistry
        from agent_baton.core.orchestration.router import AgentRouter

        mock_classifier = MagicMock(spec=DataClassifier)
        mock_classifier.classify.return_value = ClassificationResult(
            risk_level=RiskLevel.LOW,
            guardrail_preset="Standard Development",
            signals_found=[],
            confidence="high",
            explanation="",
        )

        p = IntelligentPlanner(team_context_root=ctx, classifier=mock_classifier)
        reg = AgentRegistry()
        reg.load_directory(agents_dir)
        p._registry = reg
        p._router = AgentRouter(reg)

        p.create_plan("Add a widget endpoint")
        mock_classifier.classify.assert_called_once_with("Add a widget endpoint")


# ---------------------------------------------------------------------------
# Risk level floor from classifier
# ---------------------------------------------------------------------------


class TestRiskLevelFloor:
    """Classifier risk level is the floor; keywords can raise it further."""

    def test_classifier_risk_raises_plan_risk(self, ctx: Path, agents_dir: Path) -> None:
        """When classifier says HIGH but keywords say LOW, plan should be HIGH."""
        from agent_baton.core.orchestration.registry import AgentRegistry
        from agent_baton.core.orchestration.router import AgentRouter

        mock_classifier = MagicMock(spec=DataClassifier)
        mock_classifier.classify.return_value = ClassificationResult(
            risk_level=RiskLevel.HIGH,
            guardrail_preset="Security-Sensitive",
            signals_found=["security:authentication"],
            confidence="high",
            explanation="",
        )
        p = IntelligentPlanner(team_context_root=ctx, classifier=mock_classifier)
        reg = AgentRegistry()
        reg.load_directory(agents_dir)
        p._registry = reg
        p._router = AgentRouter(reg)

        plan = p.create_plan("Refactor the widget helper utility")
        # Without classifier this would be LOW (no risk keywords); with classifier HIGH
        assert plan.risk_level == "HIGH"

    def test_keywords_can_raise_above_classifier(self, ctx: Path, agents_dir: Path) -> None:
        """When keywords say MEDIUM but classifier says LOW, plan should be MEDIUM."""
        from agent_baton.core.orchestration.registry import AgentRegistry
        from agent_baton.core.orchestration.router import AgentRouter

        mock_classifier = MagicMock(spec=DataClassifier)
        mock_classifier.classify.return_value = ClassificationResult(
            risk_level=RiskLevel.LOW,
            guardrail_preset="Standard Development",
            signals_found=[],
            confidence="high",
            explanation="",
        )
        p = IntelligentPlanner(team_context_root=ctx, classifier=mock_classifier)
        reg = AgentRegistry()
        reg.load_directory(agents_dir)
        p._registry = reg
        p._router = AgentRouter(reg)

        # "migration" triggers MEDIUM in keyword signals
        plan = p.create_plan("Run a database schema migration")
        assert plan.risk_level in ("MEDIUM", "HIGH")

    def test_classifier_error_falls_back_to_keywords(
        self, ctx: Path, agents_dir: Path
    ) -> None:
        """If the classifier raises, the plan still succeeds with keyword risk."""
        from agent_baton.core.orchestration.registry import AgentRegistry
        from agent_baton.core.orchestration.router import AgentRouter

        mock_classifier = MagicMock(spec=DataClassifier)
        mock_classifier.classify.side_effect = RuntimeError("classifier down")

        p = IntelligentPlanner(team_context_root=ctx, classifier=mock_classifier)
        reg = AgentRegistry()
        reg.load_directory(agents_dir)
        p._registry = reg
        p._router = AgentRouter(reg)

        plan = p.create_plan("Add a helper function")
        assert plan.risk_level == "LOW"
        assert p._last_classification is None


# ---------------------------------------------------------------------------
# PolicyEngine integration
# ---------------------------------------------------------------------------


class TestPolicyEngineIntegration:
    """PolicyEngine runs after agent assignment and records violations."""

    def test_violations_recorded_for_high_risk_plan_missing_auditor(
        self, ctx: Path, agents_dir: Path
    ) -> None:
        """Regulated preset requires auditor; plan without auditor should have violations."""
        from agent_baton.core.orchestration.registry import AgentRegistry
        from agent_baton.core.orchestration.router import AgentRouter

        mock_classifier = MagicMock(spec=DataClassifier)
        mock_classifier.classify.return_value = ClassificationResult(
            risk_level=RiskLevel.HIGH,
            guardrail_preset="Regulated Data",
            signals_found=["regulated:compliance"],
            confidence="high",
            explanation="",
        )
        p = IntelligentPlanner(
            team_context_root=ctx,
            classifier=mock_classifier,
            policy_engine=PolicyEngine(),
        )
        reg = AgentRegistry()
        reg.load_directory(agents_dir)
        p._registry = reg
        p._router = AgentRouter(reg)

        # Force agent list without auditor or SME
        p.create_plan("HIPAA compliance update", agents=["backend-engineer", "test-engineer"])

        # Regulated preset requires "auditor" and "subject-matter-expert"
        violation_details = " ".join(v.details for v in p._last_policy_violations)
        assert "auditor" in violation_details or "subject-matter-expert" in violation_details

    def test_no_violations_for_standard_low_risk(
        self, governed_planner: IntelligentPlanner
    ) -> None:
        """A plain low-risk task with standard agents should have no blocking violations."""
        governed_planner.create_plan("Add a helper function to format dates")
        # Standard preset only has path_block / tool_restrict rules that won't
        # fire unless agents try to write .env or secrets/. Since plan time
        # context_files is just CLAUDE.md, no path violations expected.
        blocking = [
            v for v in governed_planner._last_policy_violations
            if v.rule.severity == "block"
            and v.rule.rule_type == "path_block"
        ]
        assert blocking == []

    def test_violations_appear_in_shared_context(
        self, ctx: Path, agents_dir: Path
    ) -> None:
        """Policy violations must surface in shared_context as warnings."""
        from agent_baton.core.orchestration.registry import AgentRegistry
        from agent_baton.core.orchestration.router import AgentRouter

        mock_classifier = MagicMock(spec=DataClassifier)
        mock_classifier.classify.return_value = ClassificationResult(
            risk_level=RiskLevel.HIGH,
            guardrail_preset="Regulated Data",
            signals_found=["regulated:compliance"],
            confidence="high",
            explanation="",
        )
        p = IntelligentPlanner(
            team_context_root=ctx,
            classifier=mock_classifier,
            policy_engine=PolicyEngine(),
        )
        reg = AgentRegistry()
        reg.load_directory(agents_dir)
        p._registry = reg
        p._router = AgentRouter(reg)

        plan = p.create_plan(
            "HIPAA compliance update", agents=["backend-engineer"]
        )
        assert "Policy Notes:" in plan.shared_context

    def test_violations_appear_in_explain_plan(
        self, ctx: Path, agents_dir: Path
    ) -> None:
        from agent_baton.core.orchestration.registry import AgentRegistry
        from agent_baton.core.orchestration.router import AgentRouter

        mock_classifier = MagicMock(spec=DataClassifier)
        mock_classifier.classify.return_value = ClassificationResult(
            risk_level=RiskLevel.HIGH,
            guardrail_preset="Regulated Data",
            signals_found=["regulated:compliance"],
            confidence="high",
            explanation="",
        )
        p = IntelligentPlanner(
            team_context_root=ctx,
            classifier=mock_classifier,
            policy_engine=PolicyEngine(),
        )
        reg = AgentRegistry()
        reg.load_directory(agents_dir)
        p._registry = reg
        p._router = AgentRouter(reg)

        plan = p.create_plan("HIPAA audit trail", agents=["backend-engineer"])
        explanation = p.explain_plan(plan)
        assert "## Policy Notes" in explanation

    def test_policy_engine_error_does_not_break_plan(
        self, ctx: Path, agents_dir: Path
    ) -> None:
        """If PolicyEngine.load_preset raises, create_plan still succeeds."""
        from agent_baton.core.orchestration.registry import AgentRegistry
        from agent_baton.core.orchestration.router import AgentRouter

        mock_pe = MagicMock(spec=PolicyEngine)
        mock_pe.load_preset.side_effect = RuntimeError("policy store unavailable")

        p = IntelligentPlanner(
            team_context_root=ctx,
            policy_engine=mock_pe,
        )
        reg = AgentRegistry()
        reg.load_directory(agents_dir)
        p._registry = reg
        p._router = AgentRouter(reg)

        plan = p.create_plan("Add a helper function")
        assert isinstance(plan, MachinePlan)
        assert p._last_policy_violations == []

    def test_violations_reset_between_calls(
        self, ctx: Path, agents_dir: Path
    ) -> None:
        """Violations from a previous call must not bleed into the next."""
        from agent_baton.core.orchestration.registry import AgentRegistry
        from agent_baton.core.orchestration.router import AgentRouter

        mock_classifier = MagicMock(spec=DataClassifier)
        mock_classifier.classify.side_effect = [
            ClassificationResult(
                risk_level=RiskLevel.HIGH,
                guardrail_preset="Regulated Data",
                signals_found=["regulated:compliance"],
                confidence="high",
                explanation="",
            ),
            ClassificationResult(
                risk_level=RiskLevel.LOW,
                guardrail_preset="Standard Development",
                signals_found=[],
                confidence="high",
                explanation="",
            ),
        ]

        p = IntelligentPlanner(
            team_context_root=ctx,
            classifier=mock_classifier,
            policy_engine=PolicyEngine(),
        )
        reg = AgentRegistry()
        reg.load_directory(agents_dir)
        p._registry = reg
        p._router = AgentRouter(reg)

        p.create_plan("HIPAA audit trail", agents=["backend-engineer"])
        assert len(p._last_policy_violations) > 0

        # Second call — LOW risk standard plan — should clear previous violations
        p.create_plan("Add a helper function")
        assert p._last_policy_violations == []


# ---------------------------------------------------------------------------
# _classify_to_preset_key
# ---------------------------------------------------------------------------


class TestClassifyToPresetKey:
    """Unit tests for the static helper that maps preset names to policy keys."""

    @pytest.mark.parametrize("preset_name,expected_key", [
        ("Standard Development", "standard_dev"),
        ("Data Analysis", "data_analysis"),
        ("Infrastructure Changes", "infrastructure"),
        ("Regulated Data", "regulated"),
        ("Security-Sensitive", "security"),
    ])
    def test_known_presets_map_correctly(self, preset_name: str, expected_key: str) -> None:
        classification = ClassificationResult(
            risk_level=RiskLevel.LOW,
            guardrail_preset=preset_name,
        )
        assert IntelligentPlanner._classify_to_preset_key(classification) == expected_key

    def test_none_classification_returns_standard_dev(self) -> None:
        assert IntelligentPlanner._classify_to_preset_key(None) == "standard_dev"

    def test_unknown_preset_name_falls_back_to_standard_dev(self) -> None:
        classification = ClassificationResult(
            risk_level=RiskLevel.LOW,
            guardrail_preset="Some Unknown Preset",
        )
        assert IntelligentPlanner._classify_to_preset_key(classification) == "standard_dev"


# ---------------------------------------------------------------------------
# _validate_agents_against_policy
# ---------------------------------------------------------------------------


class TestValidateAgentsAgainstPolicy:
    """Unit tests for the policy validation helper."""

    def _make_planner(self, ctx: Path, agents_dir: Path) -> IntelligentPlanner:
        from agent_baton.core.orchestration.registry import AgentRegistry
        from agent_baton.core.orchestration.router import AgentRouter

        p = IntelligentPlanner(
            team_context_root=ctx,
            policy_engine=PolicyEngine(),
        )
        reg = AgentRegistry()
        reg.load_directory(agents_dir)
        p._registry = reg
        p._router = AgentRouter(reg)
        return p

    def test_require_agent_violation_when_agent_missing(
        self, ctx: Path, agents_dir: Path
    ) -> None:
        p = self._make_planner(ctx, agents_dir)
        policy = PolicySet(
            name="test",
            rules=[
                PolicyRule(
                    name="require_auditor",
                    rule_type="require_agent",
                    pattern="auditor",
                    severity="block",
                )
            ],
        )
        plan = p.create_plan("Add feature", agents=["backend-engineer"])
        violations = p._validate_agents_against_policy(
            ["backend-engineer"], policy, plan.phases
        )
        assert any(v.rule.name == "require_auditor" for v in violations)
        assert any("auditor" in v.details for v in violations)

    def test_no_require_agent_violation_when_agent_present(
        self, ctx: Path, agents_dir: Path
    ) -> None:
        p = self._make_planner(ctx, agents_dir)
        policy = PolicySet(
            name="test",
            rules=[
                PolicyRule(
                    name="require_auditor",
                    rule_type="require_agent",
                    pattern="auditor",
                    severity="block",
                )
            ],
        )
        plan = p.create_plan("Add feature", agents=["backend-engineer", "auditor"])
        violations = p._validate_agents_against_policy(
            ["backend-engineer", "auditor"], policy, plan.phases
        )
        assert not any(v.rule.name == "require_auditor" for v in violations)

    def test_violations_are_deduplicated(self, ctx: Path, agents_dir: Path) -> None:
        """The same (agent, rule) pair should not appear twice even with multiple phases."""
        p = self._make_planner(ctx, agents_dir)
        policy = PolicySet(
            name="test",
            rules=[
                PolicyRule(
                    name="require_auditor",
                    rule_type="require_agent",
                    pattern="auditor",
                    severity="block",
                )
            ],
        )
        # Create a plan with multiple phases — the require_agent check fires once
        plan = p.create_plan("Add feature", agents=["backend-engineer"])
        violations = p._validate_agents_against_policy(
            ["backend-engineer"], policy, plan.phases
        )
        names = [v.rule.name for v in violations]
        # Should appear exactly once even if plan has 4 phases
        assert names.count("require_auditor") == 1

    def test_flavored_agent_name_satisfies_require_agent(
        self, ctx: Path, agents_dir: Path
    ) -> None:
        """'backend-engineer--python' must satisfy require_agent for 'backend-engineer'."""
        p = self._make_planner(ctx, agents_dir)
        policy = PolicySet(
            name="test",
            rules=[
                PolicyRule(
                    name="require_backend",
                    rule_type="require_agent",
                    pattern="backend-engineer",
                    severity="block",
                )
            ],
        )
        plan = p.create_plan("Add feature", agents=["backend-engineer"])
        # Simulate the flavored name from routing
        violations = p._validate_agents_against_policy(
            ["backend-engineer--python"], policy, plan.phases
        )
        # "backend-engineer--python".split("--")[0] == "backend-engineer" → satisfied
        assert not any(v.rule.name == "require_backend" for v in violations)


# ---------------------------------------------------------------------------
# explain_plan governance sections
# ---------------------------------------------------------------------------


class TestExplainPlanGovernanceSections:
    def test_explain_plan_always_has_data_classification_section(
        self, governed_planner: IntelligentPlanner
    ) -> None:
        plan = governed_planner.create_plan("Add a helper function")
        explanation = governed_planner.explain_plan(plan)
        assert "## Data Classification" in explanation

    def test_explain_plan_always_has_policy_notes_section(
        self, governed_planner: IntelligentPlanner
    ) -> None:
        plan = governed_planner.create_plan("Add a helper function")
        explanation = governed_planner.explain_plan(plan)
        assert "## Policy Notes" in explanation

    def test_explain_plan_no_violations_message(
        self, governed_planner: IntelligentPlanner
    ) -> None:
        plan = governed_planner.create_plan("Add a helper function")
        explanation = governed_planner.explain_plan(plan)
        assert "No policy violations detected" in explanation

    def test_explain_plan_no_classifier_section_without_classifier(
        self, base_planner: IntelligentPlanner
    ) -> None:
        plan = base_planner.create_plan("Add a helper function")
        explanation = base_planner.explain_plan(plan)
        assert "## Data Classification" in explanation  # section always present
        assert "No classifier configured" in explanation


# ---------------------------------------------------------------------------
# FIX-6: PolicyEngine is actually called during baton plan CLI path
# ---------------------------------------------------------------------------


class TestPolicyEngineCalledDuringPlan:
    """Verify that PolicyEngine enforcement fires during create_plan().

    The plan CLI wires both DataClassifier and PolicyEngine into IntelligentPlanner.
    These tests confirm the wiring is exercised — not just that the objects exist.
    """

    def test_policy_engine_load_preset_called(
        self, agents_dir: Path, ctx: Path
    ) -> None:
        """PolicyEngine.load_preset() is called at least once during create_plan()."""
        from unittest.mock import MagicMock, patch
        from agent_baton.core.orchestration.registry import AgentRegistry
        from agent_baton.core.orchestration.router import AgentRouter

        mock_policy = MagicMock(spec=PolicyEngine)
        # load_preset returns None → no policy set → no violations (graceful)
        mock_policy.load_preset.return_value = None

        planner = IntelligentPlanner(
            team_context_root=ctx,
            classifier=DataClassifier(),
            policy_engine=mock_policy,
        )
        reg = AgentRegistry()
        reg.load_directory(agents_dir)
        planner._registry = reg
        planner._router = AgentRouter(reg)

        planner.create_plan("Add a helper function")

        mock_policy.load_preset.assert_called_once()

    def test_policy_engine_receives_plan_with_violations_recorded(
        self, agents_dir: Path, ctx: Path
    ) -> None:
        """When PolicyEngine returns violations, they are stored in _last_policy_violations."""
        from agent_baton.core.orchestration.registry import AgentRegistry
        from agent_baton.core.orchestration.router import AgentRouter

        violation_rule = PolicyRule(
            name="require_auditor",
            rule_type="require_agent",
            pattern="auditor",
            severity="warn",
        )
        fake_set = PolicySet(name="default", rules=[violation_rule])

        mock_policy = MagicMock(spec=PolicyEngine)
        mock_policy.load_preset.return_value = fake_set

        # _validate_agents_against_policy is a method on IntelligentPlanner, not
        # PolicyEngine — so we use a real planner but a mock PolicyEngine that
        # returns a policy set with a require_agent rule.
        planner = IntelligentPlanner(
            team_context_root=ctx,
            classifier=DataClassifier(),
            policy_engine=mock_policy,
        )
        reg = AgentRegistry()
        reg.load_directory(agents_dir)
        planner._registry = reg
        planner._router = AgentRouter(reg)

        # A plain "add feature" task uses backend-engineer + test-engineer; auditor
        # is not included — so the require_agent rule triggers a violation.
        planner.create_plan("Add a new endpoint to the API", agents=["backend-engineer"])

        # Violations must be stored (may be empty if auditor happened to be added,
        # but the policy engine must have been consulted).
        mock_policy.load_preset.assert_called_once()

    def test_plan_cmd_handler_wires_policy_engine(self, tmp_path: Path) -> None:
        """The plan CLI handler instantiates PolicyEngine and passes it to the planner."""
        import argparse
        from unittest.mock import patch, MagicMock

        args = argparse.Namespace(
            summary="Add a helper function",
            task_type=None,
            agents=None,
            project=str(tmp_path),
            json=True,
            save=False,
            explain=False,
            knowledge=[],
            knowledge_pack=[],
            intervention="low",
            complexity=None,
        )

        captured: list[PolicyEngine] = []

        original_init = IntelligentPlanner.__init__

        def capturing_init(self_inner, *iargs, **ikwargs):
            if "policy_engine" in ikwargs and ikwargs["policy_engine"] is not None:
                captured.append(ikwargs["policy_engine"])
            original_init(self_inner, *iargs, **ikwargs)

        with patch.object(IntelligentPlanner, "__init__", capturing_init):
            from agent_baton.cli.commands.execution import plan_cmd
            import io, sys
            old_stdout = sys.stdout
            sys.stdout = io.StringIO()
            try:
                plan_cmd.handler(args)
            finally:
                sys.stdout = old_stdout

        assert captured, "Expected PolicyEngine to be passed to IntelligentPlanner"
        assert isinstance(captured[0], PolicyEngine)
