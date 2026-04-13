"""Tests for agent_baton.core.engine.bead_signal.parse_bead_signals().

Coverage:
- BEAD_DISCOVERY: single signal, content extracted, Bead fields correct
- BEAD_DECISION: single signal, CHOSE/BECAUSE subfields parsed and merged into content
- BEAD_DECISION: missing CHOSE or BECAUSE falls back gracefully (no crash)
- BEAD_WARNING: single signal, Bead fields correct
- Multiple signals in one outcome: all three types together
- Multiple signals of same type in one outcome
- Empty outcome returns empty list (never raises)
- Whitespace-only outcome returns empty list
- Malformed signals (colon present but no content) are silently dropped
- Case-insensitive matching
- Signal embedded mid-paragraph is still extracted
- step_id / agent_name / task_id / bead_count flow through to generated Bead
- bead_count threshold affects generated ID length
- PromptDispatcher delegation prompt contains _BEAD_SIGNALS_LINE content
"""
from __future__ import annotations

import textwrap

import pytest

from agent_baton.core.engine.bead_signal import parse_bead_signals
from agent_baton.models.bead import Bead


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _single_discovery(text: str = "discovered something") -> str:
    return f"BEAD_DISCOVERY: {text}"


def _single_warning(text: str = "warning here") -> str:
    return f"BEAD_WARNING: {text}"


def _single_decision(
    decision: str = "Use SQLAlchemy",
    chose: str = "",
    because: str = "",
) -> str:
    lines = [f"BEAD_DECISION: {decision}"]
    if chose:
        lines.append(f"CHOSE: {chose}")
    if because:
        lines.append(f"BECAUSE: {because}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# BEAD_DISCOVERY
# ---------------------------------------------------------------------------


class TestBeadDiscovery:
    def test_single_discovery_produces_one_bead(self) -> None:
        beads = parse_bead_signals(_single_discovery())
        assert len(beads) == 1

    def test_discovery_bead_type_is_discovery(self) -> None:
        beads = parse_bead_signals(_single_discovery())
        assert beads[0].bead_type == "discovery"

    def test_discovery_content_is_extracted(self) -> None:
        beads = parse_bead_signals("BEAD_DISCOVERY: JWT uses RS256 not HS256")
        assert beads[0].content == "JWT uses RS256 not HS256"

    def test_discovery_status_is_open(self) -> None:
        beads = parse_bead_signals(_single_discovery())
        assert beads[0].status == "open"

    def test_discovery_source_is_agent_signal(self) -> None:
        beads = parse_bead_signals(_single_discovery())
        assert beads[0].source == "agent-signal"

    def test_discovery_confidence_is_medium(self) -> None:
        beads = parse_bead_signals(_single_discovery())
        assert beads[0].confidence == "medium"

    def test_discovery_scope_is_step(self) -> None:
        beads = parse_bead_signals(_single_discovery())
        assert beads[0].scope == "step"

    def test_discovery_step_id_flows_through(self) -> None:
        beads = parse_bead_signals(_single_discovery(), step_id="2.3")
        assert beads[0].step_id == "2.3"

    def test_discovery_agent_name_flows_through(self) -> None:
        beads = parse_bead_signals(_single_discovery(), agent_name="test-engineer")
        assert beads[0].agent_name == "test-engineer"

    def test_discovery_task_id_flows_through(self) -> None:
        beads = parse_bead_signals(_single_discovery(), task_id="task-abc")
        assert beads[0].task_id == "task-abc"

    def test_discovery_bead_id_has_bd_prefix(self) -> None:
        beads = parse_bead_signals(_single_discovery())
        assert beads[0].bead_id.startswith("bd-")

    def test_discovery_created_at_is_set(self) -> None:
        beads = parse_bead_signals(_single_discovery())
        assert beads[0].created_at != ""

    def test_multiple_discoveries_in_one_outcome(self) -> None:
        outcome = textwrap.dedent("""\
            Work done.
            BEAD_DISCOVERY: auth uses JWT RS256
            BEAD_DISCOVERY: DB pool default is 5 connections
        """)
        beads = parse_bead_signals(outcome)
        discoveries = [b for b in beads if b.bead_type == "discovery"]
        assert len(discoveries) == 2

    def test_discovery_trailing_whitespace_stripped(self) -> None:
        beads = parse_bead_signals("BEAD_DISCOVERY:   content with spaces   ")
        assert beads[0].content == "content with spaces"


# ---------------------------------------------------------------------------
# BEAD_DECISION
# ---------------------------------------------------------------------------


class TestBeadDecision:
    def test_single_decision_produces_one_bead(self) -> None:
        beads = parse_bead_signals(_single_decision())
        assert len(beads) == 1

    def test_decision_bead_type_is_decision(self) -> None:
        beads = parse_bead_signals(_single_decision())
        assert beads[0].bead_type == "decision"

    def test_decision_confidence_is_high(self) -> None:
        beads = parse_bead_signals(_single_decision())
        assert beads[0].confidence == "high"

    def test_decision_chose_and_because_merged_into_content(self) -> None:
        outcome = textwrap.dedent("""\
            BEAD_DECISION: Use SQLAlchemy 2.0 mapped_column style
            CHOSE: mapped_column over Column
            BECAUSE: Matches project convention and enables better type inference
        """)
        beads = parse_bead_signals(outcome)
        assert len(beads) == 1
        assert "Use SQLAlchemy 2.0 mapped_column style" in beads[0].content
        assert "mapped_column over Column" in beads[0].content
        assert "Matches project convention" in beads[0].content

    def test_decision_content_uses_pipe_separator(self) -> None:
        outcome = "BEAD_DECISION: Go with Redis\nCHOSE: Redis\nBECAUSE: speed\n"
        beads = parse_bead_signals(outcome)
        # content is "decision | CHOSE: chose | BECAUSE: because"
        assert "|" in beads[0].content

    def test_decision_without_chose_falls_back_to_description_only(self) -> None:
        outcome = "BEAD_DECISION: Use async everywhere\nBECAUSE: Better throughput\n"
        beads = parse_bead_signals(outcome)
        assert len(beads) == 1
        assert "Use async everywhere" in beads[0].content

    def test_decision_without_because_falls_back_to_description_only(self) -> None:
        outcome = "BEAD_DECISION: Use SQLAlchemy\nCHOSE: SQLAlchemy\n"
        beads = parse_bead_signals(outcome)
        assert len(beads) == 1
        assert "Use SQLAlchemy" in beads[0].content

    def test_decision_without_chose_or_because_is_just_description(self) -> None:
        outcome = "BEAD_DECISION: Stick with monolith\n"
        beads = parse_bead_signals(outcome)
        assert len(beads) == 1
        assert beads[0].content == "Stick with monolith"

    def test_decision_step_id_flows_through(self) -> None:
        beads = parse_bead_signals(_single_decision(), step_id="3.2")
        assert beads[0].step_id == "3.2"

    def test_decision_task_id_flows_through(self) -> None:
        beads = parse_bead_signals(_single_decision(), task_id="task-xyz")
        assert beads[0].task_id == "task-xyz"

    def test_chose_and_because_search_window_stays_local(self) -> None:
        """CHOSE/BECAUSE after another BEAD_DECISION must not bleed into the first."""
        outcome = textwrap.dedent("""\
            BEAD_DECISION: First decision
            CHOSE: choice-A
            BECAUSE: reason-A

            Some other content

            BEAD_DECISION: Second decision
            CHOSE: choice-B
            BECAUSE: reason-B
        """)
        beads = parse_bead_signals(outcome)
        decisions = [b for b in beads if b.bead_type == "decision"]
        assert len(decisions) == 2
        # Each decision should carry its own CHOSE/BECAUSE, not the other's
        first = next(d for d in decisions if "First decision" in d.content)
        assert "choice-A" in first.content
        assert "choice-B" not in first.content


# ---------------------------------------------------------------------------
# BEAD_WARNING
# ---------------------------------------------------------------------------


class TestBeadWarning:
    def test_single_warning_produces_one_bead(self) -> None:
        beads = parse_bead_signals(_single_warning())
        assert len(beads) == 1

    def test_warning_bead_type_is_warning(self) -> None:
        beads = parse_bead_signals(_single_warning())
        assert beads[0].bead_type == "warning"

    def test_warning_content_extracted(self) -> None:
        beads = parse_bead_signals("BEAD_WARNING: Port 5433 may conflict in CI")
        assert beads[0].content == "Port 5433 may conflict in CI"

    def test_warning_status_is_open(self) -> None:
        beads = parse_bead_signals(_single_warning())
        assert beads[0].status == "open"

    def test_warning_source_is_agent_signal(self) -> None:
        beads = parse_bead_signals(_single_warning())
        assert beads[0].source == "agent-signal"

    def test_warning_confidence_is_medium(self) -> None:
        beads = parse_bead_signals(_single_warning())
        assert beads[0].confidence == "medium"


# ---------------------------------------------------------------------------
# Multiple signals in a single outcome
# ---------------------------------------------------------------------------


class TestMultipleSignals:
    def test_all_three_signal_types_extracted(self) -> None:
        outcome = textwrap.dedent("""\
            ## Work Completed

            BEAD_DISCOVERY: Auth module uses JWT RS256.
            BEAD_DECISION: Use SQLAlchemy 2.0 style.
            CHOSE: mapped_column
            BECAUSE: Better type inference.
            BEAD_WARNING: Test DB fixture uses hardcoded port 5433.
        """)
        beads = parse_bead_signals(outcome)
        types = {b.bead_type for b in beads}
        assert "discovery" in types
        assert "decision" in types
        assert "warning" in types

    def test_three_signals_produces_three_beads(self) -> None:
        outcome = textwrap.dedent("""\
            BEAD_DISCOVERY: finding one
            BEAD_WARNING: risk identified
            BEAD_DECISION: go with option A
        """)
        beads = parse_bead_signals(outcome)
        assert len(beads) == 3

    def test_two_discoveries_produce_two_beads(self) -> None:
        outcome = "BEAD_DISCOVERY: thing one\nBEAD_DISCOVERY: thing two\n"
        beads = parse_bead_signals(outcome)
        discoveries = [b for b in beads if b.bead_type == "discovery"]
        assert len(discoveries) == 2

    def test_multiple_beads_have_different_ids(self) -> None:
        outcome = textwrap.dedent("""\
            BEAD_DISCOVERY: discovery A
            BEAD_WARNING: warning B
        """)
        beads = parse_bead_signals(outcome)
        ids = [b.bead_id for b in beads]
        assert len(ids) == len(set(ids)), "Bead IDs must be unique within one outcome"

    def test_signal_embedded_in_longer_outcome_is_extracted(self) -> None:
        outcome = textwrap.dedent("""\
            ## Summary
            I refactored the auth module and improved test coverage.

            ### Files changed
            - auth.py
            - tests/test_auth.py

            BEAD_DISCOVERY: The auth module was using HS256 not RS256 — changed.

            ### Next steps
            Deploy and monitor.
        """)
        beads = parse_bead_signals(outcome)
        assert len(beads) == 1
        assert "HS256" in beads[0].content


# ---------------------------------------------------------------------------
# Edge cases — empty, whitespace, malformed
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_empty_string_returns_empty_list(self) -> None:
        result = parse_bead_signals("")
        assert result == []

    def test_none_equivalent_empty_string_returns_empty_list(self) -> None:
        result = parse_bead_signals("")
        assert isinstance(result, list)

    def test_whitespace_only_outcome_returns_empty_list(self) -> None:
        result = parse_bead_signals("   \n\n\t  ")
        assert result == []

    def test_no_signals_in_regular_outcome_returns_empty_list(self) -> None:
        outcome = "Work completed. All tests pass. No issues found."
        result = parse_bead_signals(outcome)
        assert result == []

    def test_malformed_discovery_empty_content_after_colon_skipped(self) -> None:
        """BEAD_DISCOVERY: with no content should produce no bead."""
        outcome = "BEAD_DISCOVERY:   \n"
        result = parse_bead_signals(outcome)
        assert result == []

    def test_malformed_warning_empty_content_after_colon_skipped(self) -> None:
        outcome = "BEAD_WARNING:   \n"
        result = parse_bead_signals(outcome)
        assert result == []

    def test_malformed_decision_empty_content_skipped(self) -> None:
        outcome = "BEAD_DECISION:   \n"
        result = parse_bead_signals(outcome)
        assert result == []

    def test_function_never_raises_on_arbitrary_input(self) -> None:
        """parse_bead_signals must never raise regardless of input."""
        nasty_inputs = [
            "BEAD_DISCOVERY: \x00\x01\x02 null bytes",
            "BEAD_DECISION: " + "x" * 10_000,
            "BEAD_WARNING: unicode \u2603\U0001F600",
            "bead_discovery: lowercase signal",
            "BEAD_DISCOVERY: line one\nBEAD_DISCOVERY: line two",
        ]
        for text in nasty_inputs:
            result = parse_bead_signals(text)
            assert isinstance(result, list)

    def test_case_insensitive_discovery(self) -> None:
        outcome = "bead_discovery: lower case signal"
        beads = parse_bead_signals(outcome)
        assert len(beads) == 1
        assert beads[0].bead_type == "discovery"

    def test_case_insensitive_warning(self) -> None:
        outcome = "BEAD_warning: Mixed case"
        beads = parse_bead_signals(outcome)
        assert len(beads) == 1
        assert beads[0].bead_type == "warning"

    def test_case_insensitive_decision(self) -> None:
        outcome = "bead_DECISION: Mixed case decision\nchose: X\nbecause: Y\n"
        beads = parse_bead_signals(outcome)
        assert len(beads) == 1
        assert beads[0].bead_type == "decision"

    def test_keyword_without_colon_does_not_match(self) -> None:
        outcome = "BEAD_DISCOVERY something without colon"
        result = parse_bead_signals(outcome)
        assert result == []


# ---------------------------------------------------------------------------
# bead_count progressive ID scaling
# ---------------------------------------------------------------------------


class TestBeadCountScaling:
    def test_bead_count_0_produces_4_char_hash(self) -> None:
        beads = parse_bead_signals(_single_discovery(), bead_count=0)
        hash_part = beads[0].bead_id[len("bd-"):]
        assert len(hash_part) == 4

    def test_bead_count_500_produces_5_char_hash(self) -> None:
        beads = parse_bead_signals(_single_discovery(), bead_count=500)
        hash_part = beads[0].bead_id[len("bd-"):]
        assert len(hash_part) == 5

    def test_bead_count_1500_produces_6_char_hash(self) -> None:
        beads = parse_bead_signals(_single_discovery(), bead_count=1500)
        hash_part = beads[0].bead_id[len("bd-"):]
        assert len(hash_part) == 6


# ---------------------------------------------------------------------------
# PromptDispatcher — delegation prompt contains bead signal instructions
# ---------------------------------------------------------------------------


class TestDispatcherBeadSignalLine:
    """Verify that PromptDispatcher includes the bead signal protocol instructions."""

    def _make_step(self):
        from agent_baton.models.execution import PlanStep
        return PlanStep(
            step_id="1.1",
            agent_name="backend-engineer--python",
            task_description="Implement the feature",
        )

    def test_delegation_prompt_contains_bead_discovery_instruction(self) -> None:
        from agent_baton.core.engine.dispatcher import PromptDispatcher
        dispatcher = PromptDispatcher()
        step = self._make_step()
        prompt = dispatcher.build_delegation_prompt(step)
        assert "BEAD_DISCOVERY" in prompt

    def test_delegation_prompt_contains_bead_decision_instruction(self) -> None:
        from agent_baton.core.engine.dispatcher import PromptDispatcher
        dispatcher = PromptDispatcher()
        step = self._make_step()
        prompt = dispatcher.build_delegation_prompt(step)
        assert "BEAD_DECISION" in prompt

    def test_delegation_prompt_contains_bead_warning_instruction(self) -> None:
        from agent_baton.core.engine.dispatcher import PromptDispatcher
        dispatcher = PromptDispatcher()
        step = self._make_step()
        prompt = dispatcher.build_delegation_prompt(step)
        assert "BEAD_WARNING" in prompt

    def test_delegation_prompt_contains_chose_instruction(self) -> None:
        from agent_baton.core.engine.dispatcher import PromptDispatcher
        dispatcher = PromptDispatcher()
        step = self._make_step()
        prompt = dispatcher.build_delegation_prompt(step)
        assert "CHOSE" in prompt

    def test_delegation_prompt_contains_because_instruction(self) -> None:
        from agent_baton.core.engine.dispatcher import PromptDispatcher
        dispatcher = PromptDispatcher()
        step = self._make_step()
        prompt = dispatcher.build_delegation_prompt(step)
        assert "BECAUSE" in prompt

    def test_bead_signals_line_constant_contains_all_three_signal_types(self) -> None:
        """The _BEAD_SIGNALS_LINE constant must reference all three signal types."""
        from agent_baton.core.engine.dispatcher import _BEAD_SIGNALS_LINE
        assert "BEAD_DISCOVERY" in _BEAD_SIGNALS_LINE
        assert "BEAD_DECISION" in _BEAD_SIGNALS_LINE
        assert "BEAD_WARNING" in _BEAD_SIGNALS_LINE

    def test_bead_signals_line_appears_after_knowledge_gaps_line(self) -> None:
        """Signal instructions should appear after the knowledge gap instructions."""
        from agent_baton.core.engine.dispatcher import PromptDispatcher
        dispatcher = PromptDispatcher()
        step = self._make_step()
        prompt = dispatcher.build_delegation_prompt(step)
        kg_pos = prompt.find("KNOWLEDGE_GAP")
        bead_pos = prompt.find("BEAD_DISCOVERY")
        assert kg_pos != -1, "KNOWLEDGE_GAP not found in prompt"
        assert bead_pos != -1, "BEAD_DISCOVERY not found in prompt"
        assert bead_pos > kg_pos, "BEAD signals should appear after KNOWLEDGE_GAP"
