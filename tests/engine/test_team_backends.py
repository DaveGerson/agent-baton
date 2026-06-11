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


class TestSpawnPromptFidelity:
    """A1.a/A1.b/A1.c: spawn.md flattening, safety guards, size + approval."""

    def _nested_plan(self) -> MachinePlan:
        """A team whose lead carries a sub_team (flattened in spawn.md)."""
        return MachinePlan(
            task_id="t-nest", task_summary="nested team",
            risk_level="MEDIUM",
            phases=[PlanPhase(
                phase_id=1, name="Build",
                steps=[PlanStep(
                    step_id="1.1", agent_name="team",
                    task_description="lead + sub-team",
                    team=[
                        TeamMember(
                            member_id="1.1.a", agent_name="architect",
                            role="lead", task_description="coordinate",
                            model="opus",
                            sub_team=[
                                TeamMember(
                                    member_id="1.1.a.a",
                                    agent_name="backend-engineer",
                                    role="implementer",
                                    task_description="impl sub", model="sonnet",
                                ),
                            ],
                        ),
                    ],
                )],
            )],
        )

    def test_sub_team_flattened_with_annotation_and_warning(
        self, tmp_path: Path,
    ) -> None:
        plan = self._nested_plan()
        ClaudeTeamsBackend().on_team_dispatched(
            plan=plan, step=plan.phases[0].steps[0],
            team_context_root=tmp_path,
        )
        text = (tmp_path / "teams" / "team-1.1" / "spawn.md").read_text(
            encoding="utf-8",
        )
        # The nested member appears flat, annotated with its coordinating lead.
        assert "1.1.a.a" in text
        assert "sub-team of 1.1.a" in text
        # Explicit flattening warning is present.
        assert "Agent Teams cannot nest" in text

    def test_safety_guard_warning_for_flagged_agent(
        self, tmp_path: Path,
    ) -> None:
        # Lay down an agents/ dir next to the team-context root with a
        # skills-declaring agent that appears in the team.
        ctx = tmp_path / "team-context"
        ctx.mkdir()
        agents = tmp_path / "agents"
        agents.mkdir()
        (agents / "backend-engineer.md").write_text(
            "---\nname: backend-engineer\nmodel: sonnet\nskills: db,migrate\n---\nbody\n",
            encoding="utf-8",
        )
        plan = _plan_with_team()
        ClaudeTeamsBackend().on_team_dispatched(
            plan=plan, step=plan.phases[0].steps[0],
            team_context_root=ctx,
        )
        text = (ctx / "teams" / "team-1.1" / "spawn.md").read_text(
            encoding="utf-8",
        )
        assert "backend-engineer" in text
        assert "NOT honored as a teammate" in text
        assert "skills" in text

    def test_oversize_team_emits_size_warning(self, tmp_path: Path) -> None:
        members = [
            TeamMember(
                member_id=f"1.1.{chr(ord('a') + i)}",
                agent_name="backend-engineer", role="implementer",
                task_description=f"part {i}", model="sonnet",
            )
            for i in range(6)
        ]
        plan = MachinePlan(
            task_id="t-big", task_summary="big team",
            phases=[PlanPhase(
                phase_id=1, name="Build",
                steps=[PlanStep(step_id="1.1", agent_name="team",
                                task_description="lots", team=members)],
            )],
        )
        ClaudeTeamsBackend().on_team_dispatched(
            plan=plan, step=plan.phases[0].steps[0],
            team_context_root=tmp_path,
        )
        text = (tmp_path / "teams" / "team-1.1" / "spawn.md").read_text(
            encoding="utf-8",
        )
        assert "6 members" in text
        assert "≤5" in text or "<=5" in text or "5 teammates" in text

    def test_plan_approval_instruction_when_reviewer_present(
        self, tmp_path: Path,
    ) -> None:
        # _plan_with_team includes a code-reviewer (role="reviewer").
        plan = _plan_with_team()
        ClaudeTeamsBackend().on_team_dispatched(
            plan=plan, step=plan.phases[0].steps[0],
            team_context_root=tmp_path,
        )
        text = (tmp_path / "teams" / "team-1.1" / "spawn.md").read_text(
            encoding="utf-8",
        )
        assert "plan approval" in text.lower()
        assert "before" in text.lower()

    def test_plan_approval_instruction_when_high_risk(
        self, tmp_path: Path,
    ) -> None:
        plan = MachinePlan(
            task_id="t-hr", task_summary="high risk team",
            risk_level="HIGH",
            phases=[PlanPhase(
                phase_id=1, name="Build",
                steps=[PlanStep(
                    step_id="1.1", agent_name="team",
                    task_description="impl",
                    team=[TeamMember(
                        member_id="1.1.a", agent_name="backend-engineer",
                        role="implementer", task_description="impl",
                        model="sonnet",
                    )],
                )],
            )],
        )
        ClaudeTeamsBackend().on_team_dispatched(
            plan=plan, step=plan.phases[0].steps[0],
            team_context_root=tmp_path,
        )
        text = (tmp_path / "teams" / "team-1.1" / "spawn.md").read_text(
            encoding="utf-8",
        )
        assert "plan approval" in text.lower()

    def test_spawn_installs_taskcompleted_hook(self, tmp_path: Path) -> None:
        plan = _plan_with_team()
        ClaudeTeamsBackend().on_team_dispatched(
            plan=plan, step=plan.phases[0].steps[0],
            team_context_root=tmp_path,
        )
        text = (tmp_path / "teams" / "team-1.1" / "spawn.md").read_text(
            encoding="utf-8",
        )
        assert "TaskCompleted" in text
        assert "baton execute team-record" in text
        assert "TeammateIdle" in text


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
