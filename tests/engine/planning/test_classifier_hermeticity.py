"""Regression coverage: planner classification must be hermetic.

``IntelligentPlanner()`` built with no explicit ``task_classifier`` routes
through ``FallbackClassifier`` (agent_baton/core/engine/classifier.py),
which tries a live Sonnet call via ``HeadlessClaude`` whenever the
``claude`` binary happens to be reachable on ``PATH`` --
``_talent_agent_available()`` only checks binary presence, never an
explicit opt-in flag or ``ANTHROPIC_API_KEY``. In a sandbox where
``claude`` is on PATH (this repo's own dev containers included -- we run
*inside* Claude Code), that silently turned "unit" tests that build
``IntelligentPlanner()`` with no ``agents``/``task_type`` override into
live, network-dependent LLM calls: the model is free to recommend any
registered agent for any phase, including a reviewer-class agent for an
Implement-type phase, which ``ValidationStage``'s ``agent_phase_mismatch``
check then (correctly) rejects. The result was an intermittent
``PlanQualityError`` on byte-identical input, uncorrelated with any code
change under test -- see the phase-5 gate-repair incident this file
documents a regression test for.

``tests/engine/planning/conftest.py`` now forces the TalentAgent probe
unavailable for every test in this directory (autouse), restoring the
"mock mode by default ... without API keys" contract
``test_planner_smoke.py``'s module docstring already promises. This file
directly asserts that guarantee holds.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agent_baton.core.engine.planner import IntelligentPlanner


def _write_agent(agents_dir: Path, name: str, description: str, model: str = "sonnet") -> None:
    content = (
        f"---\nname: {name}\ndescription: {description}\nmodel: {model}\n"
        f"permissionMode: default\ntools: Read, Write\n---\n\n# {name}\n"
    )
    (agents_dir / f"{name}.md").write_text(content, encoding="utf-8")


def _make_planner(tmp_path: Path) -> IntelligentPlanner:
    tmp_path.mkdir(parents=True, exist_ok=True)
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    for name, desc in [
        ("architect", "System design specialist."),
        ("backend-engineer", "Generic backend engineer."),
        ("test-engineer", "Testing specialist."),
        ("code-reviewer", "Code review specialist."),
        ("security-reviewer", "Security-focused code review specialist."),
        ("auditor", "Audit and compliance specialist."),
    ]:
        _write_agent(agents_dir, name, desc)

    ctx = tmp_path / "team-context"
    ctx.mkdir()
    planner = IntelligentPlanner(team_context_root=ctx)
    from agent_baton.core.orchestration.registry import AgentRegistry
    from agent_baton.core.orchestration.router import AgentRouter

    registry = AgentRegistry()
    registry.load_directory(agents_dir)
    planner._registry = registry
    planner._router = AgentRouter(registry)
    return planner


class TestTalentAgentClassifierForcedUnavailable:
    """The autouse conftest fixture must make ``_talent_agent_available``
    report unavailable, so ``FallbackClassifier`` never attempts a live
    ``claude`` subprocess call from within this test directory."""

    def test_talent_agent_available_reports_false(self) -> None:
        from agent_baton.core.engine.classifier import _talent_agent_available

        available, hc = _talent_agent_available()
        assert available is False
        assert hc is None

    def test_talent_agent_classifier_returns_none(self) -> None:
        """With the probe forced unavailable, ``TalentAgentClassifier``
        must decline immediately rather than reaching for a subprocess."""
        from agent_baton.core.engine.classifier import TalentAgentClassifier
        from agent_baton.core.orchestration.registry import AgentRegistry

        classifier = TalentAgentClassifier()
        result = classifier.classify("Add user authentication", AgentRegistry())
        assert result is None


class TestPlannerClassificationIsDeterministic:
    """A fresh ``IntelligentPlanner()`` with no explicit ``task_classifier``
    must produce byte-identical plans across repeated calls for the same
    input -- the specific regression for the flaky
    ``security-reviewer``-in-``Implement`` failure (agent_phase_mismatch)."""

    @pytest.mark.parametrize(
        "task_summary",
        [
            "Add user authentication",
            "Add user authentication with login and signup endpoints",
        ],
    )
    def test_repeated_plans_have_identical_roster_and_phases(
        self, tmp_path: Path, task_summary: str
    ) -> None:
        runs = []
        for i in range(8):
            planner = _make_planner(tmp_path / f"run-{i}")
            plan = planner.create_plan(task_summary)
            runs.append(
                [
                    (phase.name, [step.agent_name for step in phase.steps])
                    for phase in plan.phases
                ]
            )

        first = runs[0]
        for i, run in enumerate(runs[1:], start=1):
            assert run == first, (
                f"create_plan({task_summary!r}) produced a different plan on "
                f"run {i} than run 0 -- classification is not hermetic "
                f"(run0={first!r} run{i}={run!r})"
            )

    def test_no_reviewer_class_agent_lands_in_an_implement_phase(
        self, tmp_path: Path
    ) -> None:
        """Direct assertion of the failure mode this regression guards:
        a reviewer-class agent must never be assigned to an
        Implement-type phase step."""
        from agent_baton.core.orchestration.router import is_reviewer_agent

        for i in range(8):
            planner = _make_planner(tmp_path / f"run-{i}")
            plan = planner.create_plan(
                "Add user authentication with login and signup endpoints"
            )
            for phase in plan.phases:
                if phase.name.lower().split(":")[0].strip() not in (
                    "implement", "fix", "draft", "migrate",
                ):
                    continue
                for step in phase.steps:
                    assert not is_reviewer_agent(step.agent_name), (
                        f"reviewer-class agent {step.agent_name!r} assigned "
                        f"to {phase.name!r} phase step {step.step_id} on run {i}"
                    )
