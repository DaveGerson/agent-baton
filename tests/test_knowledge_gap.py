"""Tests for agent_baton.core.engine.knowledge_gap and executor integration.

Coverage:
- parse_knowledge_gap: valid signals, missing signal, partial/malformed signals
- determine_escalation: all branches of the escalation matrix
- executor.record_step_result: auto-resolve flow, queue-for-gate flow,
  best-effort flow
- _dispatch_action: resolved_decisions injected into handoff
"""
from __future__ import annotations

import logging
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent_baton.core.engine.executor import ExecutionEngine, _append_resolved_decisions
from agent_baton.core.engine.knowledge_gap import (
    determine_escalation,
    parse_knowledge_gap,
)
from agent_baton.models.execution import (
    ActionType,
    ExecutionState,
    MachinePlan,
    PlanGate,
    PlanPhase,
    PlanStep,
    StepResult,
)
from agent_baton.models.knowledge import (
    KnowledgeAttachment,
    KnowledgeGapSignal,
    ResolvedDecision,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_plan(
    *,
    risk_level: str = "LOW",
    intervention_level: str = "low",
    phases: list[PlanPhase] | None = None,
) -> MachinePlan:
    if phases is None:
        phases = [
            PlanPhase(
                phase_id=1,
                name="Phase 1",
                steps=[
                    PlanStep(
                        step_id="1.1",
                        agent_name="backend-engineer--python",
                        task_description="Implement the feature",
                    )
                ],
                gate=PlanGate(gate_type="test", command="pytest"),
                approval_required=True,
                approval_description="",
            )
        ]
    return MachinePlan(
        task_id="test-task",
        task_summary="A test task",
        risk_level=risk_level,
        intervention_level=intervention_level,
        phases=phases,
    )


def _make_state(
    *,
    risk_level: str = "LOW",
    intervention_level: str = "low",
    pending_gaps: list[KnowledgeGapSignal] | None = None,
    resolved_decisions: list[ResolvedDecision] | None = None,
) -> ExecutionState:
    plan = _make_plan(risk_level=risk_level, intervention_level=intervention_level)
    return ExecutionState(
        task_id="test-task",
        plan=plan,
        pending_gaps=pending_gaps or [],
        resolved_decisions=resolved_decisions or [],
    )


def _make_engine(tmp_path: Path) -> ExecutionEngine:
    """Return an engine wired to a temporary directory (legacy file mode)."""
    return ExecutionEngine(team_context_root=tmp_path)


# ---------------------------------------------------------------------------
# parse_knowledge_gap — signal parsing
# ---------------------------------------------------------------------------

class TestParseKnowledgeGap:
    def test_full_valid_signal_factual(self):
        outcome = textwrap.dedent("""\
            Some partial work was done.

            KNOWLEDGE_GAP: Need the database schema for the users table
            CONFIDENCE: low
            TYPE: factual
        """)
        signal = parse_knowledge_gap(outcome, step_id="1.1", agent_name="backend-engineer")
        assert signal is not None
        assert signal.description == "Need the database schema for the users table"
        assert signal.confidence == "low"
        assert signal.gap_type == "factual"
        assert signal.step_id == "1.1"
        assert signal.agent_name == "backend-engineer"
        assert outcome in signal.partial_outcome  # full outcome stored

    def test_full_valid_signal_contextual(self):
        outcome = (
            "KNOWLEDGE_GAP: Need business context on retention policy\n"
            "CONFIDENCE: none\n"
            "TYPE: contextual\n"
        )
        signal = parse_knowledge_gap(outcome, step_id="2.1", agent_name="auditor")
        assert signal is not None
        assert signal.gap_type == "contextual"
        assert signal.confidence == "none"

    def test_confidence_partial(self):
        outcome = (
            "KNOWLEDGE_GAP: Partial understanding of the API contract\n"
            "CONFIDENCE: partial\n"
            "TYPE: factual\n"
        )
        signal = parse_knowledge_gap(outcome)
        assert signal is not None
        assert signal.confidence == "partial"

    def test_no_signal_returns_none(self):
        outcome = "Work completed successfully. All tests pass."
        result = parse_knowledge_gap(outcome)
        assert result is None

    def test_empty_string_returns_none(self):
        result = parse_knowledge_gap("")
        assert result is None

    def test_partial_signal_missing_confidence_defaults_to_low(self):
        """CONFIDENCE line missing — should default to 'low'."""
        outcome = "KNOWLEDGE_GAP: Something is missing\nTYPE: factual\n"
        signal = parse_knowledge_gap(outcome)
        assert signal is not None
        assert signal.confidence == "low"

    def test_partial_signal_missing_type_defaults_to_factual(self):
        """TYPE line missing — should default to 'factual'."""
        outcome = "KNOWLEDGE_GAP: Something is missing\nCONFIDENCE: none\n"
        signal = parse_knowledge_gap(outcome)
        assert signal is not None
        assert signal.gap_type == "factual"

    def test_partial_signal_missing_both_defaults(self):
        """Both CONFIDENCE and TYPE missing — defaults applied."""
        outcome = "KNOWLEDGE_GAP: I have no idea what to do here"
        signal = parse_knowledge_gap(outcome)
        assert signal is not None
        assert signal.confidence == "low"
        assert signal.gap_type == "factual"
        assert signal.description == "I have no idea what to do here"

    def test_invalid_confidence_value_defaults_to_low(self):
        """Invalid CONFIDENCE value should fall back to 'low'."""
        outcome = "KNOWLEDGE_GAP: Something\nCONFIDENCE: extreme\nTYPE: factual\n"
        signal = parse_knowledge_gap(outcome)
        assert signal is not None
        assert signal.confidence == "low"

    def test_invalid_type_value_defaults_to_factual(self):
        """Invalid TYPE value should fall back to 'factual'."""
        outcome = "KNOWLEDGE_GAP: Something\nCONFIDENCE: low\nTYPE: philosophical\n"
        signal = parse_knowledge_gap(outcome)
        assert signal is not None
        assert signal.gap_type == "factual"

    def test_case_insensitive_keywords(self):
        """KNOWLEDGE_GAP: header and values should be parsed case-insensitively."""
        outcome = "knowledge_gap: Need XYZ context\nconfidence: NONE\ntype: CONTEXTUAL\n"
        signal = parse_knowledge_gap(outcome)
        assert signal is not None
        assert signal.confidence == "none"
        assert signal.gap_type == "contextual"

    def test_signal_embedded_in_longer_outcome(self):
        """Signal can appear anywhere in the outcome text."""
        outcome = textwrap.dedent("""\
            ## Work Completed
            I implemented the auth module and wrote tests.

            ## Blocker
            KNOWLEDGE_GAP: I don't know the expected JWT expiry policy
            CONFIDENCE: low
            TYPE: contextual

            ## Files Changed
            - auth.py
        """)
        signal = parse_knowledge_gap(outcome, step_id="1.2", agent_name="security-agent")
        assert signal is not None
        assert "JWT expiry policy" in signal.description

    def test_step_id_and_agent_name_populated(self):
        outcome = "KNOWLEDGE_GAP: Need DB schema\nCONFIDENCE: low\nTYPE: factual\n"
        signal = parse_knowledge_gap(outcome, step_id="3.4", agent_name="db-admin")
        assert signal.step_id == "3.4"
        assert signal.agent_name == "db-admin"


# ---------------------------------------------------------------------------
# determine_escalation — escalation matrix
# ---------------------------------------------------------------------------

class TestDetermineEscalation:
    """Test every cell in the escalation matrix."""

    def _factual_signal(self) -> KnowledgeGapSignal:
        return KnowledgeGapSignal(
            description="Need DB schema",
            confidence="low",
            gap_type="factual",
            step_id="1.1",
            agent_name="engineer",
        )

    def _contextual_signal(self) -> KnowledgeGapSignal:
        return KnowledgeGapSignal(
            description="Need org policy context",
            confidence="none",
            gap_type="contextual",
            step_id="1.1",
            agent_name="engineer",
        )

    # Factual + match found → auto-resolve (regardless of risk/intervention)

    def test_factual_match_low_risk_low_intervention_auto_resolve(self):
        result = determine_escalation(
            self._factual_signal(),
            risk_level="LOW",
            intervention_level="low",
            resolution_found=True,
        )
        assert result == "auto-resolve"

    def test_factual_match_high_risk_low_intervention_auto_resolve(self):
        result = determine_escalation(
            self._factual_signal(),
            risk_level="HIGH",
            intervention_level="low",
            resolution_found=True,
        )
        assert result == "auto-resolve"

    def test_factual_match_critical_risk_high_intervention_auto_resolve(self):
        result = determine_escalation(
            self._factual_signal(),
            risk_level="CRITICAL",
            intervention_level="high",
            resolution_found=True,
        )
        assert result == "auto-resolve"

    # Factual + no match + LOW risk + low intervention → best-effort

    def test_factual_no_match_low_risk_low_intervention_best_effort(self):
        result = determine_escalation(
            self._factual_signal(),
            risk_level="LOW",
            intervention_level="low",
            resolution_found=False,
        )
        assert result == "best-effort"

    def test_factual_no_match_low_risk_low_intervention_case_insensitive(self):
        """Risk and intervention strings should be normalised case-insensitively."""
        result = determine_escalation(
            self._factual_signal(),
            risk_level="low",
            intervention_level="LOW",
            resolution_found=False,
        )
        assert result == "best-effort"

    # Factual + no match + LOW risk + medium/high intervention → queue-for-gate

    def test_factual_no_match_low_risk_medium_intervention_queue(self):
        result = determine_escalation(
            self._factual_signal(),
            risk_level="LOW",
            intervention_level="medium",
            resolution_found=False,
        )
        assert result == "queue-for-gate"

    def test_factual_no_match_low_risk_high_intervention_queue(self):
        result = determine_escalation(
            self._factual_signal(),
            risk_level="LOW",
            intervention_level="high",
            resolution_found=False,
        )
        assert result == "queue-for-gate"

    # Factual + no match + MEDIUM+ risk → queue-for-gate

    def test_factual_no_match_medium_risk_any_intervention_queue(self):
        result = determine_escalation(
            self._factual_signal(),
            risk_level="MEDIUM",
            intervention_level="low",
            resolution_found=False,
        )
        assert result == "queue-for-gate"

    def test_factual_no_match_high_risk_low_intervention_queue(self):
        result = determine_escalation(
            self._factual_signal(),
            risk_level="HIGH",
            intervention_level="low",
            resolution_found=False,
        )
        assert result == "queue-for-gate"

    def test_factual_no_match_critical_risk_low_intervention_queue(self):
        result = determine_escalation(
            self._factual_signal(),
            risk_level="CRITICAL",
            intervention_level="low",
            resolution_found=False,
        )
        assert result == "queue-for-gate"

    def test_factual_no_match_medium_risk_high_intervention_queue(self):
        result = determine_escalation(
            self._factual_signal(),
            risk_level="MEDIUM",
            intervention_level="high",
            resolution_found=False,
        )
        assert result == "queue-for-gate"

    # Contextual → always queue-for-gate

    def test_contextual_no_match_low_risk_low_intervention_queue(self):
        result = determine_escalation(
            self._contextual_signal(),
            risk_level="LOW",
            intervention_level="low",
            resolution_found=False,
        )
        assert result == "queue-for-gate"

    def test_contextual_with_match_still_queue(self):
        """Even with a match, contextual gaps go to human gate."""
        result = determine_escalation(
            self._contextual_signal(),
            risk_level="LOW",
            intervention_level="low",
            resolution_found=True,
        )
        assert result == "queue-for-gate"

    def test_contextual_high_risk_queue(self):
        result = determine_escalation(
            self._contextual_signal(),
            risk_level="HIGH",
            intervention_level="low",
            resolution_found=False,
        )
        assert result == "queue-for-gate"


# ---------------------------------------------------------------------------
# _append_resolved_decisions — handoff helper
# ---------------------------------------------------------------------------

class TestAppendResolvedDecisions:
    def test_no_decisions_returns_handoff_unchanged(self):
        handoff = "Some previous work output."
        result = _append_resolved_decisions(handoff, [])
        assert result == handoff

    def test_empty_handoff_with_decisions(self):
        decisions = [
            ResolvedDecision(
                gap_description="JWT expiry policy",
                resolution="Use 24h tokens per security team guidance",
                step_id="1.1",
                timestamp="2026-01-01T00:00:00+00:00",
            )
        ]
        result = _append_resolved_decisions("", decisions)
        assert "## Resolved Decisions (final — do not revisit)" in result
        assert '"JWT expiry policy"' in result
        assert "Use 24h tokens" in result

    def test_existing_handoff_gets_section_appended(self):
        handoff = "Agent did some work.\n\nFiles changed: auth.py"
        decisions = [
            ResolvedDecision(
                gap_description="DB schema question",
                resolution="auto-resolved via my-pack/schema.md",
                step_id="1.1",
                timestamp="2026-01-01T00:00:00+00:00",
            )
        ]
        result = _append_resolved_decisions(handoff, decisions)
        assert handoff.split("\n")[0] in result
        assert "## Resolved Decisions (final — do not revisit)" in result
        assert '"DB schema question"' in result

    def test_multiple_decisions_all_listed(self):
        decisions = [
            ResolvedDecision(
                gap_description="Question A",
                resolution="Answer A",
                step_id="1.1",
                timestamp="2026-01-01T00:00:00+00:00",
            ),
            ResolvedDecision(
                gap_description="Question B",
                resolution="Answer B",
                step_id="1.2",
                timestamp="2026-01-01T00:00:00+00:00",
            ),
        ]
        result = _append_resolved_decisions("", decisions)
        assert '"Question A"' in result
        assert '"Question B"' in result
        assert "Answer A" in result
        assert "Answer B" in result


# ---------------------------------------------------------------------------
# Executor integration — record_step_result with KNOWLEDGE_GAP
# ---------------------------------------------------------------------------

class TestExecutorKnowledgeGapIntegration:
    """Integration tests for the knowledge gap handling in ExecutionEngine.

    Tests use a real engine with a tmp_path persistence root and a
    mocked KnowledgeResolver on the engine's _knowledge_resolver attribute.
    """

    def _start_engine(self, tmp_path: Path, risk_level: str = "LOW", intervention_level: str = "low"):
        engine = _make_engine(tmp_path)
        plan = _make_plan(risk_level=risk_level, intervention_level=intervention_level)
        engine.start(plan)
        return engine

    # Queue-for-gate flow: contextual gap → always queued

    def test_contextual_gap_queued(self, tmp_path):
        engine = self._start_engine(tmp_path)
        outcome = (
            "Partial work done.\n"
            "KNOWLEDGE_GAP: Need business context on SOX compliance rules\n"
            "CONFIDENCE: none\n"
            "TYPE: contextual\n"
        )
        engine.record_step_result(
            step_id="1.1",
            agent_name="backend-engineer--python",
            status="complete",
            outcome=outcome,
        )
        state = engine._load_execution()
        assert state is not None
        assert len(state.pending_gaps) == 1
        gap = state.pending_gaps[0]
        assert "SOX compliance" in gap.description
        assert gap.gap_type == "contextual"
        assert gap.step_id == "1.1"

    # Queue-for-gate flow: factual + no match + HIGH risk

    def test_factual_high_risk_no_match_queued(self, tmp_path):
        engine = self._start_engine(tmp_path, risk_level="HIGH")
        # No resolver attached — resolution_found=False
        outcome = (
            "KNOWLEDGE_GAP: Need exact audit log retention policy\n"
            "CONFIDENCE: low\n"
            "TYPE: factual\n"
        )
        engine.record_step_result(
            step_id="1.1",
            agent_name="backend-engineer--python",
            status="complete",
            outcome=outcome,
        )
        state = engine._load_execution()
        assert state is not None
        assert len(state.pending_gaps) == 1

    # Best-effort flow: factual + no match + LOW risk + low intervention

    def test_factual_low_risk_low_intervention_no_match_best_effort(self, tmp_path):
        engine = self._start_engine(tmp_path, risk_level="LOW", intervention_level="low")
        outcome = (
            "KNOWLEDGE_GAP: Which logging library does the team prefer?\n"
            "CONFIDENCE: low\n"
            "TYPE: factual\n"
        )
        engine.record_step_result(
            step_id="1.1",
            agent_name="backend-engineer--python",
            status="complete",
            outcome=outcome,
        )
        state = engine._load_execution()
        assert state is not None
        # Best-effort: gap not queued, not resolved — just logged
        assert len(state.pending_gaps) == 0
        assert len(state.resolved_decisions) == 0

    # Auto-resolve flow: factual + resolver returns match → ResolvedDecision recorded

    def test_factual_resolver_finds_match_auto_resolves(self, tmp_path):
        engine = self._start_engine(tmp_path, risk_level="LOW")

        # Attach a mock resolver that returns one attachment
        mock_attachment = KnowledgeAttachment(
            source="planner-matched:tag",
            pack_name="my-pack",
            document_name="schema.md",
            path="/some/path/schema.md",
            delivery="reference",
        )
        mock_resolver = MagicMock()
        mock_resolver.resolve.return_value = [mock_attachment]
        engine._knowledge_resolver = mock_resolver

        outcome = (
            "KNOWLEDGE_GAP: Need the DB schema for users table\n"
            "CONFIDENCE: low\n"
            "TYPE: factual\n"
        )
        engine.record_step_result(
            step_id="1.1",
            agent_name="backend-engineer--python",
            status="complete",
            outcome=outcome,
        )
        state = engine._load_execution()
        assert state is not None
        # Auto-resolved: not queued
        assert len(state.pending_gaps) == 0
        # ResolvedDecision recorded
        assert len(state.resolved_decisions) == 1
        decision = state.resolved_decisions[0]
        assert "users table" in decision.gap_description
        assert "my-pack/schema.md" in decision.resolution
        assert decision.step_id == "1.1"

    # Auto-resolve flow: resolver raises → falls back to queue-for-gate

    def test_resolver_exception_falls_back_to_queue(self, tmp_path):
        engine = self._start_engine(tmp_path, risk_level="MEDIUM")

        mock_resolver = MagicMock()
        mock_resolver.resolve.side_effect = RuntimeError("resolver broke")
        engine._knowledge_resolver = mock_resolver

        outcome = (
            "KNOWLEDGE_GAP: Something obscure\n"
            "CONFIDENCE: low\n"
            "TYPE: factual\n"
        )
        engine.record_step_result(
            step_id="1.1",
            agent_name="backend-engineer--python",
            status="complete",
            outcome=outcome,
        )
        state = engine._load_execution()
        assert state is not None
        # MEDIUM risk + no match (resolver failed) → queue-for-gate
        assert len(state.pending_gaps) == 1

    # No gap in outcome — state unchanged

    def test_no_gap_signal_no_state_change(self, tmp_path):
        engine = self._start_engine(tmp_path)
        outcome = "All done. Tests pass. Nothing unusual."
        engine.record_step_result(
            step_id="1.1",
            agent_name="backend-engineer--python",
            status="complete",
            outcome=outcome,
        )
        state = engine._load_execution()
        assert state is not None
        assert len(state.pending_gaps) == 0
        assert len(state.resolved_decisions) == 0

    # Dispatched status — gap not processed (no outcome yet)

    def test_dispatched_status_no_gap_processing(self, tmp_path):
        engine = self._start_engine(tmp_path)
        # "dispatched" should not trigger gap parsing
        engine.record_step_result(
            step_id="1.1",
            agent_name="backend-engineer--python",
            status="dispatched",
            outcome="",
        )
        state = engine._load_execution()
        assert state is not None
        assert len(state.pending_gaps) == 0

    # Interrupted status — gap processed

    def test_interrupted_status_triggers_gap_processing(self, tmp_path):
        engine = self._start_engine(tmp_path, risk_level="HIGH")
        outcome = (
            "KNOWLEDGE_GAP: Need compliance ruleset for EU markets\n"
            "CONFIDENCE: none\n"
            "TYPE: contextual\n"
        )
        engine.record_step_result(
            step_id="1.1",
            agent_name="backend-engineer--python",
            status="interrupted",
            outcome=outcome,
        )
        state = engine._load_execution()
        assert state is not None
        assert len(state.pending_gaps) == 1

    # Multiple gaps in sequence accumulate in pending_gaps

    def test_multiple_gaps_accumulate(self, tmp_path):
        """Two separate steps with contextual gaps → both queued."""
        plan = _make_plan(
            risk_level="LOW",
            phases=[
                PlanPhase(
                    phase_id=1,
                    name="Phase 1",
                    steps=[
                        PlanStep(
                            step_id="1.1",
                            agent_name="backend-engineer--python",
                            task_description="Step 1",
                        ),
                        PlanStep(
                            step_id="1.2",
                            agent_name="backend-engineer--python",
                            task_description="Step 2",
                        ),
                    ],
                )
            ],
        )
        engine = _make_engine(tmp_path)
        engine.start(plan)

        for sid in ("1.1", "1.2"):
            engine.record_step_result(
                step_id=sid,
                agent_name="backend-engineer--python",
                status="complete",
                outcome=(
                    f"KNOWLEDGE_GAP: Gap for {sid}\n"
                    "CONFIDENCE: none\nTYPE: contextual\n"
                ),
            )

        state = engine._load_execution()
        assert state is not None
        assert len(state.pending_gaps) == 2


# ---------------------------------------------------------------------------
# Executor integration — resolved_decisions injected into dispatch prompt
# ---------------------------------------------------------------------------

class TestResolvedDecisionsInDispatchPrompt:
    def test_resolved_decisions_appear_in_prompt(self, tmp_path):
        """When state has resolved decisions, the dispatch prompt carries them."""
        engine = _make_engine(tmp_path)
        plan = _make_plan()
        engine.start(plan)

        # Manually inject a resolved decision into state
        state = engine._load_execution()
        assert state is not None
        state.resolved_decisions.append(
            ResolvedDecision(
                gap_description="Audit log retention period",
                resolution="Use 90-day immutable logs per CFO guidance",
                step_id="1.1",
                timestamp="2026-01-01T00:00:00+00:00",
            )
        )
        engine._save_execution(state)

        # Get the next action — should be DISPATCH for step 1.1
        action = engine.next_action()
        assert action.action_type == ActionType.DISPATCH
        assert "## Resolved Decisions (final — do not revisit)" in action.delegation_prompt
        assert '"Audit log retention period"' in action.delegation_prompt
        assert "90-day immutable logs" in action.delegation_prompt

    def test_no_resolved_decisions_no_section(self, tmp_path):
        """When no resolved decisions exist, the section is absent."""
        engine = _make_engine(tmp_path)
        plan = _make_plan()
        engine.start(plan)

        action = engine.next_action()
        assert action.action_type == ActionType.DISPATCH
        assert "## Resolved Decisions" not in action.delegation_prompt


# ---------------------------------------------------------------------------
# Pending gaps surface at APPROVAL gate
# ---------------------------------------------------------------------------

class TestPendingGapsSurfaceAtApprovalGate:
    def test_pending_gaps_in_approval_context(self, tmp_path):
        """Pending gaps should appear in the approval context string."""
        plan = _make_plan()
        engine = _make_engine(tmp_path)
        engine.start(plan)

        # Complete the step
        engine.record_step_result(
            step_id="1.1",
            agent_name="backend-engineer--python",
            status="complete",
            outcome="Work done.",
        )

        # Manually inject a pending gap
        state = engine._load_execution()
        assert state is not None
        state.pending_gaps.append(
            KnowledgeGapSignal(
                description="Need org policy on data retention",
                confidence="none",
                gap_type="contextual",
                step_id="1.1",
                agent_name="backend-engineer--python",
            )
        )
        engine._save_execution(state)

        # Next action should be APPROVAL (phase has approval_required=True)
        action = engine.next_action()
        assert action.action_type == ActionType.APPROVAL
        assert "Pending Knowledge Gaps" in action.approval_context
        assert "Need org policy on data retention" in action.approval_context
