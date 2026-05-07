"""Roundtrip and golden-fixture tests for agent_baton/models/execution.py.

These tests pin the on-disk serialization shape of every persisted type
so that a future dataclass-to-Pydantic migration cannot silently regress
the ``to_dict``/``from_dict`` contract.

Three test categories per type:
    - ``test_<Type>_to_dict_matches_golden``   — from_dict(golden) → to_dict() == golden
    - ``test_<Type>_roundtrip_idempotent``     — from_dict(to_dict(from_dict(golden))).to_dict() == golden
    - ``test_<Type>_legacy_extra_fields_ignored`` — from_dict({**golden, "unknown_future_field": "x"})
                                                    does not raise (forward-compat)

NO changes to the actual model types are made here.  This is scaffolding
only — all tests run against the existing dataclass implementation.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from agent_baton.models.execution import (
    ApprovalResult,
    ConsolidationResult,
    FeedbackQuestion,
    FeedbackResult,
    FileAttribution,
    GateResult,
    InteractionTurn,
    MachinePlan,
    PendingApprovalRequest,
    PlanAmendment,
    PlanGate,
    PlanPhase,
    PlanStep,
    StepResult,
    SynthesisSpec,
    TeamMember,
    TeamStepResult,
)
from agent_baton.models.knowledge import KnowledgeGapSignal, ResolvedDecision

GOLDEN_DIR = Path(__file__).parent / "golden_states"


def _golden(name: str) -> dict[str, Any]:
    """Load a golden JSON fixture by type name."""
    return json.loads((GOLDEN_DIR / f"{name}.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# InteractionTurn
# ---------------------------------------------------------------------------

class TestInteractionTurn:
    def test_to_dict_matches_golden(self) -> None:
        golden = _golden("InteractionTurn")
        obj = InteractionTurn.from_dict(golden)
        assert obj.to_dict() == golden

    def test_roundtrip_idempotent(self) -> None:
        golden = _golden("InteractionTurn")
        assert InteractionTurn.from_dict(
            InteractionTurn.from_dict(golden).to_dict()
        ).to_dict() == golden

    def test_legacy_extra_fields_ignored(self) -> None:
        golden = _golden("InteractionTurn")
        InteractionTurn.from_dict({**golden, "_future_field": "ignored"})


# ---------------------------------------------------------------------------
# SynthesisSpec
# ---------------------------------------------------------------------------

class TestSynthesisSpec:
    def test_to_dict_matches_golden(self) -> None:
        golden = _golden("SynthesisSpec")
        obj = SynthesisSpec.from_dict(golden)
        assert obj.to_dict() == golden

    def test_roundtrip_idempotent(self) -> None:
        golden = _golden("SynthesisSpec")
        assert SynthesisSpec.from_dict(
            SynthesisSpec.from_dict(golden).to_dict()
        ).to_dict() == golden

    def test_legacy_extra_fields_ignored(self) -> None:
        golden = _golden("SynthesisSpec")
        SynthesisSpec.from_dict({**golden, "_future_field": "ignored"})


# ---------------------------------------------------------------------------
# TeamMember
# ---------------------------------------------------------------------------

class TestTeamMember:
    def test_to_dict_matches_golden(self) -> None:
        golden = _golden("TeamMember")
        obj = TeamMember.from_dict(golden)
        assert obj.to_dict() == golden

    def test_roundtrip_idempotent(self) -> None:
        golden = _golden("TeamMember")
        assert TeamMember.from_dict(
            TeamMember.from_dict(golden).to_dict()
        ).to_dict() == golden

    def test_legacy_extra_fields_ignored(self) -> None:
        golden = _golden("TeamMember")
        # Extra field at the top level of TeamMember; from_dict uses .get() calls
        # so unknown keys are silently dropped.
        TeamMember.from_dict({**golden, "_future_field": "ignored"})


# ---------------------------------------------------------------------------
# PlanGate
# ---------------------------------------------------------------------------

class TestPlanGate:
    def test_to_dict_matches_golden(self) -> None:
        golden = _golden("PlanGate")
        obj = PlanGate.from_dict(golden)
        assert obj.to_dict() == golden

    def test_roundtrip_idempotent(self) -> None:
        golden = _golden("PlanGate")
        assert PlanGate.from_dict(
            PlanGate.from_dict(golden).to_dict()
        ).to_dict() == golden

    def test_legacy_extra_fields_ignored(self) -> None:
        golden = _golden("PlanGate")
        PlanGate.from_dict({**golden, "_future_field": "ignored"})

    def test_type_alias_accepted(self) -> None:
        """PlanGate.from_dict accepts 'type' as an alias for 'gate_type'."""
        golden = _golden("PlanGate")
        aliased = {k: v for k, v in golden.items() if k != "gate_type"}
        aliased["type"] = golden["gate_type"]
        obj = PlanGate.from_dict(aliased)
        assert obj.gate_type == golden["gate_type"]


# ---------------------------------------------------------------------------
# FeedbackQuestion
# ---------------------------------------------------------------------------

class TestFeedbackQuestion:
    def test_to_dict_matches_golden(self) -> None:
        golden = _golden("FeedbackQuestion")
        obj = FeedbackQuestion.from_dict(golden)
        assert obj.to_dict() == golden

    def test_roundtrip_idempotent(self) -> None:
        golden = _golden("FeedbackQuestion")
        assert FeedbackQuestion.from_dict(
            FeedbackQuestion.from_dict(golden).to_dict()
        ).to_dict() == golden

    def test_legacy_extra_fields_ignored(self) -> None:
        golden = _golden("FeedbackQuestion")
        FeedbackQuestion.from_dict({**golden, "_future_field": "ignored"})


# ---------------------------------------------------------------------------
# PlanStep
# ---------------------------------------------------------------------------

class TestPlanStep:
    def test_to_dict_matches_golden(self) -> None:
        golden = _golden("PlanStep")
        obj = PlanStep.from_dict(golden)
        assert obj.to_dict() == golden

    def test_roundtrip_idempotent(self) -> None:
        golden = _golden("PlanStep")
        assert PlanStep.from_dict(
            PlanStep.from_dict(golden).to_dict()
        ).to_dict() == golden

    def test_legacy_extra_fields_ignored(self) -> None:
        golden = _golden("PlanStep")
        PlanStep.from_dict({**golden, "_future_field": "ignored"})

    def test_optional_fields_omitted_when_falsy(self) -> None:
        """to_dict omits optional fields when they are falsy (e.g., team=[])."""
        golden = _golden("PlanStep")
        # Remove optional keys that to_dict only emits when truthy
        minimal = {k: v for k, v in golden.items()
                   if k not in ("team", "knowledge", "synthesis", "mcp_servers",
                                "interactive", "max_turns", "command",
                                "expected_outcome", "timeout_seconds",
                                "parallel_safe", "max_estimated_minutes")}
        obj = PlanStep.from_dict(minimal)
        serialized = obj.to_dict()
        # Optional falsy fields should not appear in to_dict() output
        assert "team" not in serialized
        assert "knowledge" not in serialized


# ---------------------------------------------------------------------------
# PlanPhase
# ---------------------------------------------------------------------------

class TestPlanPhase:
    def test_to_dict_matches_golden(self) -> None:
        golden = _golden("PlanPhase")
        obj = PlanPhase.from_dict(golden)
        assert obj.to_dict() == golden

    def test_roundtrip_idempotent(self) -> None:
        golden = _golden("PlanPhase")
        assert PlanPhase.from_dict(
            PlanPhase.from_dict(golden).to_dict()
        ).to_dict() == golden

    def test_legacy_extra_fields_ignored(self) -> None:
        golden = _golden("PlanPhase")
        PlanPhase.from_dict({**golden, "_future_field": "ignored"})


# ---------------------------------------------------------------------------
# PlanAmendment
# ---------------------------------------------------------------------------

class TestPlanAmendment:
    def test_to_dict_matches_golden(self) -> None:
        golden = _golden("PlanAmendment")
        obj = PlanAmendment.from_dict(golden)
        assert obj.to_dict() == golden

    def test_roundtrip_idempotent(self) -> None:
        golden = _golden("PlanAmendment")
        assert PlanAmendment.from_dict(
            PlanAmendment.from_dict(golden).to_dict()
        ).to_dict() == golden

    def test_legacy_extra_fields_ignored(self) -> None:
        golden = _golden("PlanAmendment")
        PlanAmendment.from_dict({**golden, "_future_field": "ignored"})


# ---------------------------------------------------------------------------
# TeamStepResult
# ---------------------------------------------------------------------------

class TestTeamStepResult:
    def test_to_dict_matches_golden(self) -> None:
        golden = _golden("TeamStepResult")
        obj = TeamStepResult.from_dict(golden)
        assert obj.to_dict() == golden

    def test_roundtrip_idempotent(self) -> None:
        golden = _golden("TeamStepResult")
        assert TeamStepResult.from_dict(
            TeamStepResult.from_dict(golden).to_dict()
        ).to_dict() == golden

    def test_legacy_extra_fields_ignored(self) -> None:
        golden = _golden("TeamStepResult")
        TeamStepResult.from_dict({**golden, "_future_field": "ignored"})


# ---------------------------------------------------------------------------
# StepResult
# ---------------------------------------------------------------------------

class TestStepResult:
    def test_to_dict_matches_golden(self) -> None:
        golden = _golden("StepResult")
        obj = StepResult.from_dict(golden)
        assert obj.to_dict() == golden

    def test_roundtrip_idempotent(self) -> None:
        golden = _golden("StepResult")
        assert StepResult.from_dict(
            StepResult.from_dict(golden).to_dict()
        ).to_dict() == golden

    def test_legacy_extra_fields_ignored(self) -> None:
        golden = _golden("StepResult")
        StepResult.from_dict({**golden, "_future_field": "ignored"})

    def test_member_results_absent_when_empty(self) -> None:
        """to_dict omits member_results when the list is empty."""
        golden = _golden("StepResult")
        no_members = {**golden, "member_results": []}
        obj = StepResult.from_dict(no_members)
        assert "member_results" not in obj.to_dict()

    def test_interaction_history_absent_when_empty(self) -> None:
        """to_dict omits interaction_history when the list is empty."""
        golden = _golden("StepResult")
        no_history = {**golden, "interaction_history": []}
        obj = StepResult.from_dict(no_history)
        assert "interaction_history" not in obj.to_dict()


# ---------------------------------------------------------------------------
# ApprovalResult
# ---------------------------------------------------------------------------

class TestApprovalResult:
    def test_to_dict_matches_golden(self) -> None:
        golden = _golden("ApprovalResult")
        obj = ApprovalResult.from_dict(golden)
        assert obj.to_dict() == golden

    def test_roundtrip_idempotent(self) -> None:
        golden = _golden("ApprovalResult")
        assert ApprovalResult.from_dict(
            ApprovalResult.from_dict(golden).to_dict()
        ).to_dict() == golden

    def test_legacy_extra_fields_ignored(self) -> None:
        golden = _golden("ApprovalResult")
        ApprovalResult.from_dict({**golden, "_future_field": "ignored"})


# ---------------------------------------------------------------------------
# PendingApprovalRequest
# ---------------------------------------------------------------------------

class TestPendingApprovalRequest:
    def test_to_dict_matches_golden(self) -> None:
        golden = _golden("PendingApprovalRequest")
        obj = PendingApprovalRequest.from_dict(golden)
        assert obj.to_dict() == golden

    def test_roundtrip_idempotent(self) -> None:
        golden = _golden("PendingApprovalRequest")
        assert PendingApprovalRequest.from_dict(
            PendingApprovalRequest.from_dict(golden).to_dict()
        ).to_dict() == golden

    def test_legacy_extra_fields_ignored(self) -> None:
        golden = _golden("PendingApprovalRequest")
        PendingApprovalRequest.from_dict({**golden, "_future_field": "ignored"})


# ---------------------------------------------------------------------------
# GateResult
# ---------------------------------------------------------------------------

class TestGateResult:
    def test_to_dict_matches_golden(self) -> None:
        golden = _golden("GateResult")
        obj = GateResult.from_dict(golden)
        assert obj.to_dict() == golden

    def test_roundtrip_idempotent(self) -> None:
        golden = _golden("GateResult")
        assert GateResult.from_dict(
            GateResult.from_dict(golden).to_dict()
        ).to_dict() == golden

    def test_legacy_extra_fields_ignored(self) -> None:
        golden = _golden("GateResult")
        GateResult.from_dict({**golden, "_future_field": "ignored"})


# ---------------------------------------------------------------------------
# FeedbackResult
# ---------------------------------------------------------------------------

class TestFeedbackResult:
    def test_to_dict_matches_golden(self) -> None:
        golden = _golden("FeedbackResult")
        obj = FeedbackResult.from_dict(golden)
        assert obj.to_dict() == golden

    def test_roundtrip_idempotent(self) -> None:
        golden = _golden("FeedbackResult")
        assert FeedbackResult.from_dict(
            FeedbackResult.from_dict(golden).to_dict()
        ).to_dict() == golden

    def test_legacy_extra_fields_ignored(self) -> None:
        golden = _golden("FeedbackResult")
        FeedbackResult.from_dict({**golden, "_future_field": "ignored"})


# ---------------------------------------------------------------------------
# FileAttribution
# ---------------------------------------------------------------------------

class TestFileAttribution:
    def test_to_dict_matches_golden(self) -> None:
        golden = _golden("FileAttribution")
        obj = FileAttribution.from_dict(golden)
        assert obj.to_dict() == golden

    def test_roundtrip_idempotent(self) -> None:
        golden = _golden("FileAttribution")
        assert FileAttribution.from_dict(
            FileAttribution.from_dict(golden).to_dict()
        ).to_dict() == golden

    def test_legacy_extra_fields_ignored(self) -> None:
        golden = _golden("FileAttribution")
        FileAttribution.from_dict({**golden, "_future_field": "ignored"})


# ---------------------------------------------------------------------------
# ConsolidationResult
# ---------------------------------------------------------------------------

class TestConsolidationResult:
    def test_to_dict_matches_golden(self) -> None:
        golden = _golden("ConsolidationResult")
        obj = ConsolidationResult.from_dict(golden)
        assert obj.to_dict() == golden

    def test_roundtrip_idempotent(self) -> None:
        golden = _golden("ConsolidationResult")
        assert ConsolidationResult.from_dict(
            ConsolidationResult.from_dict(golden).to_dict()
        ).to_dict() == golden

    def test_legacy_extra_fields_ignored(self) -> None:
        golden = _golden("ConsolidationResult")
        ConsolidationResult.from_dict({**golden, "_future_field": "ignored"})


# ---------------------------------------------------------------------------
# MachinePlan (outer type — also exercises PlanPhase, PlanStep, etc.)
# ---------------------------------------------------------------------------

class TestMachinePlan:
    def test_to_dict_matches_golden(self) -> None:
        golden = _golden("MachinePlan")
        obj = MachinePlan.from_dict(golden)
        assert obj.to_dict() == golden

    def test_roundtrip_idempotent(self) -> None:
        golden = _golden("MachinePlan")
        assert MachinePlan.from_dict(
            MachinePlan.from_dict(golden).to_dict()
        ).to_dict() == golden

    def test_legacy_extra_fields_ignored(self) -> None:
        golden = _golden("MachinePlan")
        MachinePlan.from_dict({**golden, "_future_field": "ignored"})

    def test_nested_phases_preserved(self) -> None:
        """Round-tripped MachinePlan has the correct phase count."""
        golden = _golden("MachinePlan")
        obj = MachinePlan.from_dict(golden)
        assert len(obj.phases) == len(golden["phases"])

    def test_nested_steps_preserved(self) -> None:
        """Each phase's step list survives the roundtrip intact."""
        golden = _golden("MachinePlan")
        obj = MachinePlan.from_dict(golden)
        for i, phase in enumerate(obj.phases):
            assert len(phase.steps) == len(golden["phases"][i]["steps"])

    def test_resource_limits_roundtrip(self) -> None:
        """resource_limits nested object survives a full roundtrip."""
        golden = _golden("MachinePlan")
        obj = MachinePlan.from_dict(golden)
        assert obj.resource_limits is not None
        serialized = obj.to_dict()
        assert serialized["resource_limits"] == golden["resource_limits"]

    def test_foresight_insights_roundtrip(self) -> None:
        """foresight_insights list survives a full roundtrip."""
        golden = _golden("MachinePlan")
        obj = MachinePlan.from_dict(golden)
        assert len(obj.foresight_insights) == len(golden["foresight_insights"])
        assert obj.to_dict()["foresight_insights"] == golden["foresight_insights"]


# ---------------------------------------------------------------------------
# ExecutionState (outer type — exercises all nested types)
# ---------------------------------------------------------------------------

class TestExecutionState:
    def test_to_dict_matches_golden(self) -> None:
        golden = _golden("ExecutionState")
        from agent_baton.models.execution import ExecutionState
        obj = ExecutionState.from_dict(golden)
        assert obj.to_dict() == golden

    def test_roundtrip_idempotent(self) -> None:
        golden = _golden("ExecutionState")
        from agent_baton.models.execution import ExecutionState
        assert ExecutionState.from_dict(
            ExecutionState.from_dict(golden).to_dict()
        ).to_dict() == golden

    def test_legacy_extra_fields_ignored(self) -> None:
        golden = _golden("ExecutionState")
        from agent_baton.models.execution import ExecutionState
        ExecutionState.from_dict({**golden, "_future_field": "ignored"})

    def test_step_results_preserved(self) -> None:
        """step_results list survives a roundtrip."""
        golden = _golden("ExecutionState")
        from agent_baton.models.execution import ExecutionState
        obj = ExecutionState.from_dict(golden)
        assert len(obj.step_results) == len(golden["step_results"])

    def test_approval_results_preserved(self) -> None:
        """approval_results list survives a roundtrip."""
        golden = _golden("ExecutionState")
        from agent_baton.models.execution import ExecutionState
        obj = ExecutionState.from_dict(golden)
        assert len(obj.approval_results) == len(golden["approval_results"])

    def test_amendments_preserved(self) -> None:
        """amendments list survives a roundtrip."""
        golden = _golden("ExecutionState")
        from agent_baton.models.execution import ExecutionState
        obj = ExecutionState.from_dict(golden)
        assert len(obj.amendments) == len(golden["amendments"])

    def test_consolidation_result_preserved(self) -> None:
        """consolidation_result nested object survives a roundtrip."""
        golden = _golden("ExecutionState")
        from agent_baton.models.execution import ExecutionState
        obj = ExecutionState.from_dict(golden)
        assert obj.consolidation_result is not None
        assert obj.to_dict()["consolidation_result"] == golden["consolidation_result"]

    def test_interaction_history_preserved(self) -> None:
        """interaction_history inside step_results survives a roundtrip."""
        golden = _golden("ExecutionState")
        from agent_baton.models.execution import ExecutionState
        obj = ExecutionState.from_dict(golden)
        sr = obj.step_results[0]
        expected_turns = len(golden["step_results"][0].get("interaction_history", []))
        assert len(sr.interaction_history) == expected_turns

    def test_pending_gaps_preserved(self) -> None:
        """pending_gaps list survives a roundtrip."""
        golden = _golden("ExecutionState")
        from agent_baton.models.execution import ExecutionState
        obj = ExecutionState.from_dict(golden)
        assert len(obj.pending_gaps) == len(golden["pending_gaps"])

    def test_resolved_decisions_preserved(self) -> None:
        """resolved_decisions list survives a roundtrip."""
        golden = _golden("ExecutionState")
        from agent_baton.models.execution import ExecutionState
        obj = ExecutionState.from_dict(golden)
        assert len(obj.resolved_decisions) == len(golden["resolved_decisions"])

    def test_none_consolidation_result_roundtrips(self) -> None:
        """ExecutionState with consolidation_result=None serializes correctly."""
        golden = _golden("ExecutionState")
        from agent_baton.models.execution import ExecutionState
        data = {**golden, "consolidation_result": None}
        obj = ExecutionState.from_dict(data)
        assert obj.consolidation_result is None
        assert obj.to_dict()["consolidation_result"] is None

    def test_delivered_knowledge_roundtrips(self) -> None:
        """delivered_knowledge dict survives a full roundtrip."""
        golden = _golden("ExecutionState")
        from agent_baton.models.execution import ExecutionState
        obj = ExecutionState.from_dict(golden)
        assert obj.to_dict()["delivered_knowledge"] == golden["delivered_knowledge"]


# ---------------------------------------------------------------------------
# ExecutionAction — to_dict() structured field tests for GATE extensions
# ---------------------------------------------------------------------------


class TestExecutionActionGateExtensions:
    """Tests that derived_commands and agent_additions are serialised correctly
    by ``ExecutionAction.to_dict()`` under the lean-payload convention (present
    only when non-empty)."""

    def _gate_action(self, **kwargs):
        from agent_baton.models.execution import ExecutionAction, ActionType
        return ExecutionAction(action_type=ActionType.GATE, **kwargs)

    def test_empty_extension_fields_omitted_from_to_dict(self) -> None:
        """When both lists are empty, neither key appears in to_dict()."""
        action = self._gate_action(gate_type="test", gate_command="pytest -q", phase_id=1)
        d = action.to_dict()
        assert "derived_commands" not in d
        assert "agent_additions" not in d

    def test_derived_commands_present_when_non_empty(self) -> None:
        """to_dict() includes derived_commands when the list is non-empty."""
        dc = [{"command": "npm audit", "source_file": "package.json", "rationale": "audit script"}]
        action = self._gate_action(
            gate_type="test",
            gate_command="pytest -q && npm audit",
            phase_id=2,
            derived_commands=dc,
        )
        d = action.to_dict()
        assert "derived_commands" in d
        assert d["derived_commands"] == dc
        # agent_additions still absent when empty.
        assert "agent_additions" not in d

    def test_agent_additions_present_when_non_empty(self) -> None:
        """to_dict() includes agent_additions when the list is non-empty."""
        aa = ["pre-commit run --all-files"]
        action = self._gate_action(
            gate_type="lint",
            gate_command="ruff check . && pre-commit run --all-files",
            phase_id=0,
            agent_additions=aa,
        )
        d = action.to_dict()
        assert "agent_additions" in d
        assert d["agent_additions"] == aa
        # derived_commands still absent when empty.
        assert "derived_commands" not in d

    def test_both_extension_fields_present_simultaneously(self) -> None:
        """Both keys appear when both lists are non-empty."""
        dc = [{"command": "make test", "source_file": "Makefile", "rationale": "test target"}]
        aa = ["npm audit --audit-level=high"]
        action = self._gate_action(
            gate_type="test",
            gate_command="pytest && make test && npm audit --audit-level=high",
            phase_id=3,
            derived_commands=dc,
            agent_additions=aa,
        )
        d = action.to_dict()
        assert d["derived_commands"] == dc
        assert d["agent_additions"] == aa

    def test_to_dict_preserves_action_type_as_string(self) -> None:
        """action_type is serialised as a plain string for protocol consumers."""
        from agent_baton.models.execution import ActionType
        action = self._gate_action(gate_type="build", gate_command="make build", phase_id=1)
        assert action.to_dict()["action_type"] == ActionType.GATE.value

    def test_to_dict_extensions_are_copies_not_references(self) -> None:
        """Mutating the returned list must not affect the action's fields."""
        dc = [{"command": "pytest", "source_file": "ci.yml", "rationale": "test"}]
        action = self._gate_action(
            gate_type="test",
            gate_command="pytest",
            phase_id=1,
            derived_commands=dc,
        )
        returned = action.to_dict()["derived_commands"]
        returned.append({"command": "INJECTED", "source_file": "", "rationale": ""})
        assert len(action.derived_commands) == 1  # original unaffected
