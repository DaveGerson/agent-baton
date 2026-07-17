"""Tests for scope expansion processing and guardrails."""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from agent_baton.core.engine.scope_expansion import (
    MAX_EXPANSIONS_PER_EXECUTION,
    MAX_STEP_MULTIPLIER,
    check_expansion_guardrails,
    generate_expansion_phase,
)
from agent_baton.core.engine.bead_signal import parse_scope_expansions


class TestParseExpansionSignals:
    def test_single_signal(self):
        outcome = "Done.\nSCOPE_EXPANSION: Add RBAC middleware to auth module\nAll good."
        result = parse_scope_expansions(outcome)
        assert result == ["Add RBAC middleware to auth module"]

    def test_multiple_signals(self):
        outcome = (
            "SCOPE_EXPANSION: Add rate limiting\n"
            "SCOPE_EXPANSION: Add request logging\n"
        )
        result = parse_scope_expansions(outcome)
        assert len(result) == 2
        assert "rate limiting" in result[0]
        assert "request logging" in result[1]

    def test_no_signal(self):
        assert parse_scope_expansions("Just a normal outcome.") == []

    def test_empty_outcome(self):
        assert parse_scope_expansions("") == []

    def test_case_insensitive(self):
        result = parse_scope_expansions("scope_expansion: some work")
        assert result == ["some work"]


class TestExpansionGuardrails:
    def _make_state(self, expansions_applied: int = 0, total_steps: int = 5):
        state = MagicMock()
        state.scope_expansions_applied = expansions_applied
        phases = []
        for i in range(total_steps):
            step = MagicMock()
            step.step_id = f"1.{i}"
            phase = MagicMock()
            phase.steps = [step]
            phases.append(phase)
        state.plan.phases = phases
        return state

    def test_allows_first_expansion(self):
        state = self._make_state(expansions_applied=0)
        assert check_expansion_guardrails(state, original_step_count=5) is None

    def test_blocks_at_max(self):
        state = self._make_state(expansions_applied=3)
        result = check_expansion_guardrails(state, original_step_count=5)
        assert result is not None
        assert "Maximum" in result

    def test_blocks_at_step_ceiling(self):
        state = self._make_state(expansions_applied=0, total_steps=10)
        result = check_expansion_guardrails(state, original_step_count=5)
        assert result is not None
        assert "ceiling" in result

    def test_allows_within_ceiling(self):
        state = self._make_state(expansions_applied=0, total_steps=8)
        assert check_expansion_guardrails(state, original_step_count=5) is None


class TestGenerateExpansionPhase:
    def _make_plan(self, max_phase_id: int = 3):
        plan = MagicMock()
        phases = []
        for i in range(1, max_phase_id + 1):
            p = MagicMock()
            p.phase_id = i
            phases.append(p)
        plan.phases = phases
        return plan

    def test_basic_generation(self):
        plan = self._make_plan()
        phase = generate_expansion_phase("Add test coverage for auth", plan, trigger_phase_id=2)
        assert phase.phase_id == 4
        assert "Expansion:" in phase.name
        assert len(phase.steps) == 1
        assert phase.gate is not None

    def test_selects_test_engineer(self):
        plan = self._make_plan()
        phase = generate_expansion_phase("Write unit tests for the parser", plan, trigger_phase_id=1)
        assert phase.steps[0].agent_name == "test-engineer"

    def test_selects_backend_for_api(self):
        plan = self._make_plan()
        phase = generate_expansion_phase("Add API endpoint for user profiles", plan, trigger_phase_id=1)
        assert phase.steps[0].agent_name == "backend-engineer"

    def test_selects_frontend(self):
        plan = self._make_plan()
        phase = generate_expansion_phase("Build React component for dashboard", plan, trigger_phase_id=1)
        assert phase.steps[0].agent_name == "frontend-engineer"

    def test_selects_security(self):
        plan = self._make_plan()
        phase = generate_expansion_phase("Fix RBAC vulnerability in auth module", plan, trigger_phase_id=1)
        assert phase.steps[0].agent_name == "security-reviewer"

    def test_fallback_to_backend(self):
        plan = self._make_plan()
        phase = generate_expansion_phase("Do something generic", plan, trigger_phase_id=1)
        assert phase.steps[0].agent_name == "backend-engineer"

    def test_step_id_format(self):
        plan = self._make_plan(max_phase_id=5)
        phase = generate_expansion_phase("Fix something", plan, trigger_phase_id=1)
        assert phase.steps[0].step_id == "6.1"

    def test_long_description_truncated_in_name(self):
        plan = self._make_plan()
        desc = "A" * 200
        phase = generate_expansion_phase(desc, plan, trigger_phase_id=1)
        assert len(phase.name) < 100

    def test_generated_step_has_no_allowed_paths_and_is_write_capable(self):
        """Threat model (Phase 3 'Make scope contracts authoritative',
        3.3): an adaptively-generated expansion phase must NOT end up
        with implicit, unbounded write access. This module never sets
        ``allowed_paths`` (it has no repo-topology evidence to derive
        one from -- it only has a free-text description), and the
        generated step's default ``step_type`` ('developing') is
        write-capable. That combination is exactly what
        ``ClaudeCodeLauncher.configure_step_scope``'s fail-closed
        PATH_SCOPE_EMPTY check exists to catch downstream -- pinned here
        so this module and the launcher's contract cannot silently drift
        apart (e.g. a future edit here that changes the default
        step_type without also adding scope-derivation would otherwise
        go unnoticed)."""
        from agent_baton.core.engine.planning.scope_contract import is_write_capable

        plan = self._make_plan()
        phase = generate_expansion_phase("Add API endpoint for reports", plan, trigger_phase_id=1)
        step = phase.steps[0]
        assert step.allowed_paths == []
        assert is_write_capable(step.step_type)

    def test_generated_step_is_refused_at_dispatch_not_silently_unbounded(self):
        """End-to-end with the real launcher: a write-capable step with
        no allowed_paths must never reach the ``claude`` subprocess with
        unbounded (whole-repo) write access -- it must be refused
        fail-closed instead."""
        import asyncio

        from agent_baton.core.runtime.claude_launcher import (
            ClaudeCodeConfig,
            ClaudeCodeLauncher,
        )

        plan = self._make_plan()
        phase = generate_expansion_phase("Add API endpoint for reports", plan, trigger_phase_id=1)
        step = phase.steps[0]

        launcher = ClaudeCodeLauncher(ClaudeCodeConfig(claude_path="echo"))
        launcher.configure_step_scope(
            step.step_id, step.allowed_paths, step.blocked_paths,
            write_capable=is_write_capable_default_true(step.step_type),
        )
        result = asyncio.run(
            launcher.launch(
                agent_name=step.agent_name, model="sonnet",
                prompt="do work", step_id=step.step_id,
            )
        )
        assert result.status == "failed"
        assert "PATH_SCOPE_EMPTY" in (result.error or "")


def is_write_capable_default_true(step_type: str) -> bool:
    """Mirror the production call site's semantics: TaskWorker always
    passes ``write_capable=True`` unless the step_type is intentionally
    read-only (see scope_contract.READ_ONLY_STEP_TYPES); the launcher's
    own default is also True, so this simply names that contract for the
    test above rather than hardcoding a bare ``True``."""
    from agent_baton.core.engine.planning.scope_contract import (
        is_intentionally_read_only,
    )

    return not is_intentionally_read_only(step_type)


class TestExecutionStateExpansionFields:
    def test_serialization_roundtrip(self):
        from agent_baton.models.execution import ExecutionState, MachinePlan
        plan = MachinePlan(task_id="t-1", task_summary="test", phases=[])
        state = ExecutionState(task_id="t-1", plan=plan)
        state.pending_scope_expansions = [{"description": "test", "step_id": "1.1", "phase_id": 0}]
        state.scope_expansions_applied = 2

        d = state.to_dict()
        assert d["pending_scope_expansions"] == [{"description": "test", "step_id": "1.1", "phase_id": 0}]
        assert d["scope_expansions_applied"] == 2

        restored = ExecutionState.from_dict(d)
        assert restored.pending_scope_expansions == [{"description": "test", "step_id": "1.1", "phase_id": 0}]
        assert restored.scope_expansions_applied == 2

    def test_backward_compat(self):
        from agent_baton.models.execution import ExecutionState, MachinePlan
        plan_data = MachinePlan(task_id="t-1", task_summary="test", phases=[]).to_dict()
        old_data = {"task_id": "t-1", "plan": plan_data}
        state = ExecutionState.from_dict(old_data)
        assert state.pending_scope_expansions == []
        assert state.scope_expansions_applied == 0
