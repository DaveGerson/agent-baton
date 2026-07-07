"""Tests for bd-rm-team-p2 ("Improve ownership and conflict quality").

Covers the four work items:

1. Machine-readable file ownership contracts — ``intended_scope`` derived
   from ``TeamMember.task_description`` and surfaced in
   ``build_team_readiness_diagnostics`` / the ``team-report.json`` artifact.
2. Pre-dispatch overlap warnings — ``detect_scope_overlaps`` flags members
   with identical or path-overlapping file-scope text before any team
   member is dispatched.
3. Conflict severity classification — ``_detect_team_conflict`` now tags
   each conflict ``high``/``medium``/``low`` via deterministic path-based
   heuristics (``agent_baton.core.engine.executor._classify_conflict_severity``).
4. Actionable conflict messages — conflict records carry the affected
   files, member IDs, agents, and a suggested next action.
"""
from __future__ import annotations

import json
from pathlib import Path

from agent_baton.core.engine.executor import (
    ExecutionEngine,
    _suggest_conflict_next_action,
)
from agent_baton.core.engine.team_backends import (
    build_team_readiness_diagnostics,
    detect_scope_overlaps,
    extract_intended_scope,
)
from agent_baton.models.execution import (
    MachinePlan,
    PlanPhase,
    PlanStep,
    SynthesisSpec,
    TeamMember,
    TeamStepResult,
)


def _make_engine(tmp_path: Path) -> ExecutionEngine:
    root = tmp_path / ".claude" / "team-context"
    root.mkdir(parents=True, exist_ok=True)
    return ExecutionEngine(team_context_root=root)


def _team_plan(
    members: list[TeamMember],
    *,
    synthesis: SynthesisSpec | None = None,
    step_id: str = "1.1",
) -> MachinePlan:
    return MachinePlan(
        task_id="test-team-conflicts",
        task_summary="Test team conflict tooling",
        task_type="new-feature",
        risk_level="LOW",
        phases=[
            PlanPhase(
                phase_id=1,
                name="Implementation",
                steps=[
                    PlanStep(
                        step_id=step_id,
                        agent_name="team",
                        task_description="Team work",
                        team=members,
                        synthesis=synthesis,
                    ),
                ],
            ),
        ],
    )


def _two_member_step(roles: tuple[str, str] = ("implementer", "implementer")) -> PlanStep:
    plan = _team_plan([
        TeamMember(
            member_id="1.1.a", agent_name="backend-engineer",
            role=roles[0], task_description="impl a",
        ),
        TeamMember(
            member_id="1.1.b", agent_name="frontend-engineer",
            role=roles[1], task_description="impl b",
        ),
    ])
    return plan.phases[0].steps[0]


# ---------------------------------------------------------------------------
# 1. Machine-readable file ownership contracts
# ---------------------------------------------------------------------------

class TestOwnershipContract:
    def test_intended_scope_extracted_from_task_description(self) -> None:
        scope = extract_intended_scope(
            "Update agent_baton/core/engine/executor.py and add "
            "tests/engine/test_x.py"
        )
        assert scope == ["agent_baton/core/engine/executor.py", "tests/engine/test_x.py"]

    def test_no_path_tokens_returns_empty_scope(self) -> None:
        assert extract_intended_scope("Implement the backend feature") == []

    def test_shared_contracts_include_intended_scope(self, tmp_path: Path) -> None:
        plan = _team_plan([
            TeamMember(
                member_id="1.1.a", agent_name="backend-engineer",
                role="implementer",
                task_description="Modify src/api.py for the new endpoint",
            ),
            TeamMember(
                member_id="1.1.b", agent_name="frontend-engineer",
                role="implementer",
                task_description="Modify src/ui.tsx for the new button",
            ),
        ])
        step = plan.phases[0].steps[0]
        diagnostics = build_team_readiness_diagnostics(
            plan=plan, step=step, backend_name="worktree",
            team_context_root=tmp_path,
        ).to_dict()

        contracts = {c["member_id"]: c for c in diagnostics["shared_contracts"]}
        assert contracts["1.1.a"]["intended_scope"] == ["src/api.py"]
        assert contracts["1.1.b"]["intended_scope"] == ["src/ui.tsx"]
        # Ownership contract also carries role + agent, not just scope.
        assert contracts["1.1.a"]["role"] == "implementer"
        assert contracts["1.1.a"]["agent_name"] == "backend-engineer"

    def test_ownership_contract_appears_in_team_report_artifact(
        self, tmp_path: Path,
    ) -> None:
        """End-to-end: engine.start() on a team plan writes team-report.json
        with per-member intended_scope, through the same pre-dispatch path
        real executions use."""
        engine = _make_engine(tmp_path)
        plan = _team_plan([
            TeamMember(
                member_id="1.1.a", agent_name="backend-engineer",
                role="implementer",
                task_description="Modify src/api.py for the new endpoint",
            ),
            TeamMember(
                member_id="1.1.b", agent_name="frontend-engineer",
                role="implementer",
                task_description="Modify src/ui.tsx for the new button",
            ),
        ])
        engine.start(plan)

        report = (
            tmp_path / ".claude" / "team-context" / "teams" / "team-1.1"
            / "team-report.json"
        )
        assert report.exists()
        data = json.loads(report.read_text(encoding="utf-8"))
        contracts = {c["member_id"]: c for c in data["shared_contracts"]}
        assert contracts["1.1.a"]["intended_scope"] == ["src/api.py"]
        assert contracts["1.1.b"]["intended_scope"] == ["src/ui.tsx"]


# ---------------------------------------------------------------------------
# 2. Pre-dispatch overlap warnings
# ---------------------------------------------------------------------------

class TestOverlapWarnings:
    def test_overlapping_paths_produce_warning(self) -> None:
        members = [
            TeamMember(
                member_id="1.1.a", agent_name="backend-engineer",
                role="implementer",
                task_description="Modify src/shared.py for the API",
            ),
            TeamMember(
                member_id="1.1.b", agent_name="frontend-engineer",
                role="implementer",
                task_description="Modify src/shared.py for the UI hook",
            ),
        ]
        warnings = detect_scope_overlaps(members)
        assert len(warnings) == 1
        assert "1.1.a" in warnings[0] and "1.1.b" in warnings[0]
        assert "src/shared.py" in warnings[0]

    def test_identical_scope_text_produces_warning(self) -> None:
        members = [
            TeamMember(
                member_id="1.1.a", agent_name="backend-engineer",
                role="implementer", task_description="Fix the payment bug",
            ),
            TeamMember(
                member_id="1.1.b", agent_name="frontend-engineer",
                role="implementer", task_description="Fix the payment bug",
            ),
        ]
        warnings = detect_scope_overlaps(members)
        assert len(warnings) == 1
        assert "identical" in warnings[0]

    def test_disjoint_scopes_produce_no_warning(self) -> None:
        members = [
            TeamMember(
                member_id="1.1.a", agent_name="backend-engineer",
                role="implementer", task_description="impl",
            ),
            TeamMember(
                member_id="1.1.b", agent_name="code-reviewer",
                role="reviewer", task_description="review",
            ),
        ]
        assert detect_scope_overlaps(members) == []

    def test_overlap_warning_surfaces_in_readiness_diagnostics_before_dispatch(
        self, tmp_path: Path,
    ) -> None:
        plan = _team_plan([
            TeamMember(
                member_id="1.1.a", agent_name="backend-engineer",
                role="implementer",
                task_description="Modify src/shared.py for the API",
            ),
            TeamMember(
                member_id="1.1.b", agent_name="frontend-engineer",
                role="implementer",
                task_description="Modify src/shared.py for the UI hook",
            ),
        ])
        step = plan.phases[0].steps[0]
        diagnostics = build_team_readiness_diagnostics(
            plan=plan, step=step, backend_name="worktree",
            team_context_root=tmp_path,
        )
        # Computed purely from the roster — no member results (i.e. no
        # dispatch) exist yet, proving the warning fires BEFORE any team
        # member is actually dispatched.
        assert diagnostics.warning_count >= 1
        assert any("src/shared.py" in w for w in diagnostics.warnings)

    def test_overlap_warning_lands_in_plan_diagnostics_on_start(
        self, tmp_path: Path,
    ) -> None:
        engine = _make_engine(tmp_path)
        plan = _team_plan([
            TeamMember(
                member_id="1.1.a", agent_name="backend-engineer",
                role="implementer",
                task_description="Modify src/shared.py for the API",
            ),
            TeamMember(
                member_id="1.1.b", agent_name="frontend-engineer",
                role="implementer",
                task_description="Modify src/shared.py for the UI hook",
            ),
        ])
        engine.start(plan)
        state = engine._load_state()
        readiness = state.plan.plan_diagnostics["team_readiness"]["1.1"]
        assert readiness["warning_count"] >= 1
        assert any("src/shared.py" in w for w in readiness["warnings"])


# ---------------------------------------------------------------------------
# 3. Conflict severity classification
# ---------------------------------------------------------------------------

class TestConflictSeverity:
    def test_same_source_file_two_implementers_is_high(self, tmp_path: Path) -> None:
        engine = _make_engine(tmp_path)
        step = _two_member_step(("implementer", "implementer"))
        member_results = [
            TeamStepResult(member_id="1.1.a", agent_name="backend-engineer",
                            status="complete", outcome="a", files_changed=["src/app.py"]),
            TeamStepResult(member_id="1.1.b", agent_name="frontend-engineer",
                            status="complete", outcome="b", files_changed=["src/app.py"]),
        ]
        conflict = engine._detect_team_conflict(step, member_results)
        assert conflict is not None
        assert conflict.severity == "high"

    def test_shared_config_file_is_medium(self, tmp_path: Path) -> None:
        engine = _make_engine(tmp_path)
        step = _two_member_step(("implementer", "implementer"))
        member_results = [
            TeamStepResult(member_id="1.1.a", agent_name="backend-engineer",
                            status="complete", outcome="a", files_changed=["conftest.py"]),
            TeamStepResult(member_id="1.1.b", agent_name="frontend-engineer",
                            status="complete", outcome="b", files_changed=["conftest.py"]),
        ]
        conflict = engine._detect_team_conflict(step, member_results)
        assert conflict is not None
        assert conflict.severity == "medium"

    def test_shared_test_file_is_medium(self, tmp_path: Path) -> None:
        engine = _make_engine(tmp_path)
        step = _two_member_step(("implementer", "implementer"))
        member_results = [
            TeamStepResult(member_id="1.1.a", agent_name="backend-engineer",
                            status="complete", outcome="a",
                            files_changed=["tests/test_app.py"]),
            TeamStepResult(member_id="1.1.b", agent_name="frontend-engineer",
                            status="complete", outcome="b",
                            files_changed=["tests/test_app.py"]),
        ]
        conflict = engine._detect_team_conflict(step, member_results)
        assert conflict is not None
        assert conflict.severity == "medium"

    def test_shared_docs_file_is_low(self, tmp_path: Path) -> None:
        engine = _make_engine(tmp_path)
        step = _two_member_step(("implementer", "implementer"))
        member_results = [
            TeamStepResult(member_id="1.1.a", agent_name="backend-engineer",
                            status="complete", outcome="a",
                            files_changed=["docs/guide.md"]),
            TeamStepResult(member_id="1.1.b", agent_name="frontend-engineer",
                            status="complete", outcome="b",
                            files_changed=["docs/guide.md"]),
        ]
        conflict = engine._detect_team_conflict(step, member_results)
        assert conflict is not None
        assert conflict.severity == "low"

    def test_generated_artifact_is_low(self, tmp_path: Path) -> None:
        engine = _make_engine(tmp_path)
        step = _two_member_step(("implementer", "implementer"))
        member_results = [
            TeamStepResult(member_id="1.1.a", agent_name="backend-engineer",
                            status="complete", outcome="a",
                            files_changed=["dist/bundle.min.js"]),
            TeamStepResult(member_id="1.1.b", agent_name="frontend-engineer",
                            status="complete", outcome="b",
                            files_changed=["dist/bundle.min.js"]),
        ]
        conflict = engine._detect_team_conflict(step, member_results)
        assert conflict is not None
        assert conflict.severity == "low"

    def test_source_file_implementer_and_reviewer_is_medium(
        self, tmp_path: Path,
    ) -> None:
        """Not two implementers -> not 'high', even for a source file."""
        engine = _make_engine(tmp_path)
        step = _two_member_step(("implementer", "reviewer"))
        member_results = [
            TeamStepResult(member_id="1.1.a", agent_name="backend-engineer",
                            status="complete", outcome="a", files_changed=["src/app.py"]),
            TeamStepResult(member_id="1.1.b", agent_name="frontend-engineer",
                            status="complete", outcome="b", files_changed=["src/app.py"]),
        ]
        conflict = engine._detect_team_conflict(step, member_results)
        assert conflict is not None
        assert conflict.severity == "medium"

    def test_worst_file_wins_across_multiple_conflicting_files(
        self, tmp_path: Path,
    ) -> None:
        """A single high-severity file must not be diluted by a co-occurring
        docs conflict in the same conflict record."""
        engine = _make_engine(tmp_path)
        step = _two_member_step(("implementer", "implementer"))
        member_results = [
            TeamStepResult(member_id="1.1.a", agent_name="backend-engineer",
                            status="complete", outcome="a",
                            files_changed=["docs/guide.md", "src/app.py"]),
            TeamStepResult(member_id="1.1.b", agent_name="frontend-engineer",
                            status="complete", outcome="b",
                            files_changed=["docs/guide.md", "src/app.py"]),
        ]
        conflict = engine._detect_team_conflict(step, member_results)
        assert conflict is not None
        assert conflict.severity == "high"


# ---------------------------------------------------------------------------
# 4. Actionable conflict messages
# ---------------------------------------------------------------------------

class TestActionableConflictMessage:
    def test_conflict_message_contains_files_members_agents_and_next_action(
        self, tmp_path: Path,
    ) -> None:
        engine = _make_engine(tmp_path)
        step = _two_member_step(("implementer", "implementer"))
        member_results = [
            TeamStepResult(member_id="1.1.a", agent_name="backend-engineer",
                            status="complete", outcome="a", files_changed=["src/app.py"]),
            TeamStepResult(member_id="1.1.b", agent_name="frontend-engineer",
                            status="complete", outcome="b", files_changed=["src/app.py"]),
        ]
        conflict = engine._detect_team_conflict(step, member_results)
        assert conflict is not None

        # Structured fields (bd-rm-team-p2 additive ConflictRecord fields).
        assert conflict.files == ["src/app.py"]
        assert set(conflict.member_ids) == {"1.1.a", "1.1.b"}
        assert set(conflict.agents) == {"backend-engineer", "frontend-engineer"}
        assert conflict.next_action

        # The rendered message names all four categories of information.
        detail = conflict.resolution_detail
        assert "src/app.py" in detail
        assert "1.1.a" in detail and "1.1.b" in detail
        assert "backend-engineer" in detail and "frontend-engineer" in detail
        assert conflict.next_action in detail

    def test_high_and_low_severity_get_different_next_actions(self) -> None:
        high = _suggest_conflict_next_action("high")
        low = _suggest_conflict_next_action("low")
        assert high != low
        assert "implementers" in high.lower()

    def test_conflict_record_round_trips_new_fields(self, tmp_path: Path) -> None:
        """New fields survive to_dict/from_dict — retrospective persistence
        must not silently drop the actionable data."""
        from agent_baton.models.retrospective import ConflictRecord

        engine = _make_engine(tmp_path)
        step = _two_member_step(("implementer", "implementer"))
        member_results = [
            TeamStepResult(member_id="1.1.a", agent_name="backend-engineer",
                            status="complete", outcome="a", files_changed=["src/app.py"]),
            TeamStepResult(member_id="1.1.b", agent_name="frontend-engineer",
                            status="complete", outcome="b", files_changed=["src/app.py"]),
        ]
        conflict = engine._detect_team_conflict(step, member_results)
        assert conflict is not None

        restored = ConflictRecord.from_dict(conflict.to_dict())
        assert restored == conflict

    def test_old_conflict_record_json_without_new_fields_still_loads(self) -> None:
        """Backward compatibility: retrospectives written before this change
        have no member_ids/files/next_action keys."""
        from agent_baton.models.retrospective import ConflictRecord

        legacy = {
            "conflict_id": "conflict-legacy",
            "step_id": "1.1",
            "agents": ["backend-engineer", "frontend-engineer"],
            "severity": "medium",
        }
        rec = ConflictRecord.from_dict(legacy)
        assert rec.member_ids == []
        assert rec.files == []
        assert rec.next_action == ""
