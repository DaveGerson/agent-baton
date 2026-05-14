"""Tests for the pluggable team backends (A1).

Covers the selector, both backend implementations' observable side
effects, and the agent frontmatter audit helper.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agent_baton.core.engine.team_backends import (
    ClaudeTeamsBackend,
    TeamBackend,
    WorktreeTeamBackend,
    audit_agents_for_teammate_safety,
    check_resumability_constraints,
    select_team_backend,
)
from agent_baton.models.execution import (
    MachinePlan,
    PlanPhase,
    PlanStep,
    TeamMember,
)


def _plan_with_team() -> MachinePlan:
    return MachinePlan(
        task_id="t1",
        task_summary="exercise team backend",
        phases=[PlanPhase(
            phase_id=1, name="Build",
            steps=[PlanStep(
                step_id="1.1", agent_name="team",
                task_description="implement and review",
                model="sonnet",
                team=[
                    TeamMember(
                        member_id="1.1.a", agent_name="backend-engineer",
                        role="implementer", task_description="impl",
                        model="sonnet",
                    ),
                    TeamMember(
                        member_id="1.1.b", agent_name="code-reviewer",
                        role="reviewer", task_description="review",
                        model="sonnet",
                    ),
                ],
            )],
        )],
    )


class TestSelector:
    def test_default_is_worktree(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("BATON_TEAMS_BACKEND", raising=False)
        be = select_team_backend()
        assert isinstance(be, WorktreeTeamBackend)
        assert be.name == "worktree"

    def test_env_var_claude_teams(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BATON_TEAMS_BACKEND", "claude-teams")
        be = select_team_backend()
        assert isinstance(be, ClaudeTeamsBackend)

    def test_unknown_value_falls_back(
        self, monkeypatch: pytest.MonkeyPatch, caplog,
    ) -> None:
        monkeypatch.setenv("BATON_TEAMS_BACKEND", "totally-made-up")
        be = select_team_backend()
        assert isinstance(be, WorktreeTeamBackend)

    def test_explicit_arg_overrides_env(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("BATON_TEAMS_BACKEND", "claude-teams")
        be = select_team_backend("worktree")
        assert isinstance(be, WorktreeTeamBackend)


class TestWorktreeBackend:
    def test_dispatched_hook_is_noop(self, tmp_path: Path) -> None:
        plan = _plan_with_team()
        be = WorktreeTeamBackend()
        # Just confirm it doesn't raise; no file artifact is written.
        be.on_team_dispatched(
            plan=plan, step=plan.phases[0].steps[0],
            team_context_root=tmp_path,
        )
        assert not (tmp_path / "teams").exists()

    def test_no_external_hook(self) -> None:
        be = WorktreeTeamBackend()
        assert be.hook_record_command(
            task_id="t1", step_id="1.1", member_id="1.1.a",
        ) == ""


class TestClaudeTeamsBackend:
    def test_writes_spawn_prompt(self, tmp_path: Path) -> None:
        plan = _plan_with_team()
        be = ClaudeTeamsBackend()
        be.on_team_dispatched(
            plan=plan, step=plan.phases[0].steps[0],
            team_context_root=tmp_path,
        )
        spawn = tmp_path / "teams" / "team-1.1" / "spawn.md"
        assert spawn.exists()
        text = spawn.read_text(encoding="utf-8")
        assert "backend-engineer" in text
        assert "code-reviewer" in text
        # Known-limitation guidance is included so the lead reads it.
        assert "skills" in text and "mcpServers" in text
        assert "No in-process resumption" in text

    def test_hook_command_includes_member_id(self) -> None:
        be = ClaudeTeamsBackend()
        cmd = be.hook_record_command(
            task_id="t1", step_id="1.1", member_id="1.1.a",
        )
        assert "baton execute team-record" in cmd
        assert "--member-id 1.1.a" in cmd
        assert "--hook-source claude-teams" in cmd


class TestAgentAudit:
    def test_audit_flags_skills_field(self, tmp_path: Path) -> None:
        agents = tmp_path / "agents"
        agents.mkdir()
        (agents / "safe-agent.md").write_text(
            "---\nname: safe-agent\nmodel: sonnet\n---\nbody\n",
            encoding="utf-8",
        )
        (agents / "skills-agent.md").write_text(
            "---\nname: skills-agent\nmodel: sonnet\nskills: foo,bar\n---\nbody\n",
            encoding="utf-8",
        )
        flagged = audit_agents_for_teammate_safety(agents)
        assert flagged == {"skills-agent": ["skills"]}

    def test_audit_flags_mcp_servers(self, tmp_path: Path) -> None:
        agents = tmp_path / "agents"
        agents.mkdir()
        (agents / "mcp-agent.md").write_text(
            "---\nname: mcp-agent\nmodel: sonnet\nmcpServers: [github, slack]\n---\n",
            encoding="utf-8",
        )
        flagged = audit_agents_for_teammate_safety(agents)
        assert "mcp-agent" in flagged
        assert "mcpServers" in flagged["mcp-agent"]

    def test_empty_lists_are_not_flagged(self, tmp_path: Path) -> None:
        agents = tmp_path / "agents"
        agents.mkdir()
        (agents / "empty.md").write_text(
            "---\nname: empty\nmodel: sonnet\nskills: []\nmcpServers: []\n---\n",
            encoding="utf-8",
        )
        flagged = audit_agents_for_teammate_safety(agents)
        assert flagged == {}

    def test_audit_skips_non_frontmatter_files(self, tmp_path: Path) -> None:
        agents = tmp_path / "agents"
        agents.mkdir()
        (agents / "no-fm.md").write_text("just markdown, no frontmatter\n")
        flagged = audit_agents_for_teammate_safety(agents)
        assert flagged == {}

    def test_audit_missing_dir_returns_empty(self, tmp_path: Path) -> None:
        flagged = audit_agents_for_teammate_safety(tmp_path / "nope")
        assert flagged == {}


class TestProtocolConformance:
    """Both backends implement the runtime-checkable protocol."""

    @pytest.mark.parametrize("cls", [WorktreeTeamBackend, ClaudeTeamsBackend])
    def test_isinstance_team_backend(self, cls: type) -> None:
        assert isinstance(cls(), TeamBackend)


class TestResumabilityConstraint:
    """A1.d: planner-time warning for claude-teams + long-running."""

    def test_no_warning_for_worktree_backend(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("BATON_TEAMS_BACKEND", raising=False)
        plan = _plan_with_team()
        plan.budget_tier = "long-running"
        assert check_resumability_constraints(plan) == []

    def test_no_warning_for_short_budget(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("BATON_TEAMS_BACKEND", "claude-teams")
        plan = _plan_with_team()
        plan.budget_tier = "standard"
        assert check_resumability_constraints(plan) == []

    def test_warning_when_claude_teams_long_running_with_team(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("BATON_TEAMS_BACKEND", "claude-teams")
        plan = _plan_with_team()
        plan.budget_tier = "long-running"
        warns = check_resumability_constraints(plan)
        assert len(warns) == 1
        assert "claude-teams" in warns[0]
        assert "cannot resume" in warns[0]

    def test_no_warning_when_no_team_phases(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from agent_baton.models.execution import PlanStep, PlanPhase, MachinePlan
        monkeypatch.setenv("BATON_TEAMS_BACKEND", "claude-teams")
        plan = MachinePlan(
            task_id="t-noteam", task_summary="plain",
            budget_tier="long-running",
            phases=[PlanPhase(
                phase_id=1, name="P1",
                steps=[PlanStep(step_id="1.1", agent_name="solo",
                                task_description="x")],
            )],
        )
        assert check_resumability_constraints(plan) == []
