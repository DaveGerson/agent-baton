"""Tests for agent_baton.core.learn.interviewer — LearningInterviewer."""
from __future__ import annotations

from pathlib import Path

import pytest

from agent_baton.core.learn.interviewer import LearningInterviewer, _type_label
from agent_baton.core.learn.ledger import LearningLedger
from agent_baton.models.learning import LearningEvidence, LearningIssue


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "baton.db"


@pytest.fixture
def ledger(db_path: Path) -> LearningLedger:
    return LearningLedger(db_path)


@pytest.fixture
def interviewer(ledger: LearningLedger) -> LearningInterviewer:
    return LearningInterviewer(ledger)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_issue(
    ledger: LearningLedger,
    issue_type: str = "routing_mismatch",
    target: str = "python:be",
    severity: str = "medium",
    title: str = "Test issue",
    times: int = 1,
) -> LearningIssue:
    issue = None
    for _ in range(times):
        issue = ledger.record_issue(issue_type, target, severity, title)
    return issue


# ---------------------------------------------------------------------------
# get_next_issue
# ---------------------------------------------------------------------------


class TestGetNextIssue:
    def test_returns_none_when_no_issues(self, interviewer: LearningInterviewer):
        assert interviewer.get_next_issue() is None

    def test_returns_open_issue(self, ledger: LearningLedger, interviewer: LearningInterviewer):
        _seed_issue(ledger, "routing_mismatch", "t1")
        issue = interviewer.get_next_issue()
        assert issue is not None
        assert issue.target == "t1"

    def test_returns_proposed_issue_before_open(
        self, ledger: LearningLedger, interviewer: LearningInterviewer
    ):
        open_issue = _seed_issue(ledger, "agent_degradation", "t1", "low")
        proposed_issue = _seed_issue(ledger, "routing_mismatch", "t2", "medium")
        ledger.update_status(proposed_issue.issue_id, "proposed")

        next_issue = interviewer.get_next_issue()
        assert next_issue.issue_id == proposed_issue.issue_id

    def test_returns_high_severity_before_low(
        self, ledger: LearningLedger, interviewer: LearningInterviewer
    ):
        low = _seed_issue(ledger, "routing_mismatch", "t-low", "low")
        high = _seed_issue(ledger, "agent_degradation", "t-high", "high")
        next_issue = interviewer.get_next_issue()
        assert next_issue.issue_id == high.issue_id

    def test_interview_only_types_prioritized(
        self, ledger: LearningLedger, interviewer: LearningInterviewer
    ):
        routing = _seed_issue(ledger, "routing_mismatch", "t1", "medium")
        drift = _seed_issue(ledger, "pattern_drift", "t2", "low")
        next_issue = interviewer.get_next_issue()
        assert next_issue.issue_id == drift.issue_id

    def test_returns_none_when_only_resolved_issues(
        self, ledger: LearningLedger, interviewer: LearningInterviewer
    ):
        issue = _seed_issue(ledger)
        ledger.update_status(issue.issue_id, "resolved")
        assert interviewer.get_next_issue() is None

    def test_returns_none_when_only_wontfix_issues(
        self, ledger: LearningLedger, interviewer: LearningInterviewer
    ):
        issue = _seed_issue(ledger)
        ledger.update_status(issue.issue_id, "wontfix")
        assert interviewer.get_next_issue() is None

    def test_type_filter_applied(self, ledger: LearningLedger, interviewer: LearningInterviewer):
        _seed_issue(ledger, "routing_mismatch", "t1")
        _seed_issue(ledger, "agent_degradation", "t2")
        issue = interviewer.get_next_issue(type_filter="agent_degradation")
        assert issue.issue_type == "agent_degradation"

    def test_severity_filter_applied(self, ledger: LearningLedger, interviewer: LearningInterviewer):
        _seed_issue(ledger, "routing_mismatch", "t1", "low")
        _seed_issue(ledger, "agent_degradation", "t2", "high")
        issue = interviewer.get_next_issue(severity_filter="low")
        assert issue.severity == "low"

    def test_type_filter_returns_none_when_no_match(
        self, ledger: LearningLedger, interviewer: LearningInterviewer
    ):
        _seed_issue(ledger, "routing_mismatch", "t1")
        issue = interviewer.get_next_issue(type_filter="gate_mismatch")
        assert issue is None


# ---------------------------------------------------------------------------
# format_issue
# ---------------------------------------------------------------------------


class TestFormatIssue:
    def test_includes_issue_id_prefix(self, ledger: LearningLedger, interviewer: LearningInterviewer):
        issue = _seed_issue(ledger, "routing_mismatch", "python:backend-engineer")
        output = interviewer.format_issue(issue)
        assert issue.issue_id[:8] in output

    def test_includes_type_label(self, ledger: LearningLedger, interviewer: LearningInterviewer):
        issue = _seed_issue(ledger, "routing_mismatch", "python:backend-engineer")
        output = interviewer.format_issue(issue)
        assert "Routing Mismatch" in output

    def test_includes_severity(self, ledger: LearningLedger, interviewer: LearningInterviewer):
        issue = _seed_issue(ledger, "agent_degradation", "agent-x", "high")
        output = interviewer.format_issue(issue)
        assert "high" in output

    def test_includes_occurrence_count(self, ledger: LearningLedger, interviewer: LearningInterviewer):
        issue = _seed_issue(ledger, "routing_mismatch", "t1", "medium", "Test", times=3)
        output = interviewer.format_issue(issue)
        assert "3" in output

    def test_includes_title(self, ledger: LearningLedger, interviewer: LearningInterviewer):
        issue = _seed_issue(ledger, "gate_mismatch", "ts:test", "medium", "Gate broken")
        output = interviewer.format_issue(issue)
        assert "Gate broken" in output

    def test_includes_evidence_details(self, ledger: LearningLedger, interviewer: LearningInterviewer):
        ev = LearningEvidence("2026-04-13T00:00:00Z", "t1", "Specific detail observed", {})
        issue = ledger.record_issue("routing_mismatch", "t1", "medium", "Issue", ev)
        output = interviewer.format_issue(issue)
        assert "Specific detail observed" in output

    def test_truncates_evidence_at_five(self, ledger: LearningLedger, interviewer: LearningInterviewer):
        for i in range(8):
            ev = LearningEvidence("2026-04-13T00:00:00Z", f"t{i}", f"Detail {i}", {})
            ledger.record_issue("routing_mismatch", "overflow-target", "low", "Title", ev)
        issue = ledger.get_open_issues(issue_type="routing_mismatch")[0]
        output = interviewer.format_issue(issue)
        assert "more" in output

    def test_includes_proposed_fix_when_present(
        self, ledger: LearningLedger, interviewer: LearningInterviewer
    ):
        issue = _seed_issue(ledger)
        ledger.update_status(issue.issue_id, "proposed", proposed_fix="Add flavor override")
        updated = ledger.get_issue(issue.issue_id)
        output = interviewer.format_issue(updated)
        assert "Add flavor override" in output

    def test_ends_with_what_would_you_like(
        self, ledger: LearningLedger, interviewer: LearningInterviewer
    ):
        issue = _seed_issue(ledger)
        output = interviewer.format_issue(issue)
        assert "What would you like to do?" in output


# ---------------------------------------------------------------------------
# get_options
# ---------------------------------------------------------------------------


class TestGetOptions:
    @pytest.mark.parametrize("issue_type,expected_keys", [
        ("routing_mismatch", {"a", "b", "c", "f"}),
        ("agent_degradation", {"a", "b", "c", "d", "e", "f", "g"}),
        ("knowledge_gap", {"a", "b", "c", "d", "f"}),
        ("pattern_drift", {"a", "b", "c", "f"}),
        ("prompt_evolution", {"a", "b", "c", "f"}),
        ("roster_bloat", {"a", "b", "c", "f"}),
        ("gate_mismatch", {"a", "b", "c", "d", "f"}),
    ])
    def test_correct_options_per_type(
        self,
        ledger: LearningLedger,
        interviewer: LearningInterviewer,
        issue_type: str,
        expected_keys: set[str],
    ):
        issue = _seed_issue(ledger, issue_type)
        options = interviewer.get_options(issue)
        keys = {key for key, _ in options}
        assert keys == expected_keys

    def test_options_are_tuples(self, ledger: LearningLedger, interviewer: LearningInterviewer):
        issue = _seed_issue(ledger, "routing_mismatch")
        options = interviewer.get_options(issue)
        for item in options:
            assert isinstance(item, tuple)
            assert len(item) == 2

    def test_unknown_type_falls_back_to_default_options(
        self, ledger: LearningLedger, interviewer: LearningInterviewer
    ):
        issue = LearningIssue(
            issue_id="custom",
            issue_type="unknown_custom_type",
            severity="low",
            status="open",
            title="Custom",
            target="custom-target",
        )
        options = interviewer.get_options(issue)
        assert len(options) > 0

    def test_returns_copy_not_reference(
        self, ledger: LearningLedger, interviewer: LearningInterviewer
    ):
        issue = _seed_issue(ledger, "routing_mismatch")
        opts1 = interviewer.get_options(issue)
        opts1.append(("z", "injected"))
        opts2 = interviewer.get_options(issue)
        assert ("z", "injected") not in opts2


# ---------------------------------------------------------------------------
# record_decision
# ---------------------------------------------------------------------------


class TestRecordDecision:
    def test_choice_a_sets_applied(self, ledger: LearningLedger, interviewer: LearningInterviewer):
        issue = _seed_issue(ledger)
        result = interviewer.record_decision(issue.issue_id, "a", "Applied fix")
        assert result is True
        updated = ledger.get_issue(issue.issue_id)
        assert updated.status == "applied"

    def test_choice_b_sets_investigating(
        self, ledger: LearningLedger, interviewer: LearningInterviewer
    ):
        issue = _seed_issue(ledger)
        interviewer.record_decision(issue.issue_id, "b")
        updated = ledger.get_issue(issue.issue_id)
        assert updated.status == "investigating"

    def test_choice_c_sets_wontfix(self, ledger: LearningLedger, interviewer: LearningInterviewer):
        issue = _seed_issue(ledger)
        interviewer.record_decision(issue.issue_id, "c", "Not worth fixing")
        updated = ledger.get_issue(issue.issue_id)
        assert updated.status == "wontfix"

    def test_choice_f_keeps_open(self, ledger: LearningLedger, interviewer: LearningInterviewer):
        """Skip (f) should leave the issue open for next time."""
        issue = _seed_issue(ledger)
        interviewer.record_decision(issue.issue_id, "f")
        updated = ledger.get_issue(issue.issue_id)
        assert updated.status == "open"

    def test_choice_d_sets_applied(self, ledger: LearningLedger, interviewer: LearningInterviewer):
        """For agent_degradation, 'd' = drop agent = applied."""
        issue = _seed_issue(ledger, "agent_degradation")
        interviewer.record_decision(issue.issue_id, "d", "Drop this agent")
        updated = ledger.get_issue(issue.issue_id)
        assert updated.status == "applied"

    def test_resolution_set_for_terminal_status(
        self, ledger: LearningLedger, interviewer: LearningInterviewer
    ):
        issue = _seed_issue(ledger)
        interviewer.record_decision(issue.issue_id, "a", "My reasoning")
        updated = ledger.get_issue(issue.issue_id)
        assert updated.resolution is not None
        assert "My reasoning" in updated.resolution

    def test_resolution_type_is_interview_for_terminal(
        self, ledger: LearningLedger, interviewer: LearningInterviewer
    ):
        issue = _seed_issue(ledger)
        interviewer.record_decision(issue.issue_id, "a")
        updated = ledger.get_issue(issue.issue_id)
        assert updated.resolution_type == "interview"

    def test_resolution_type_none_for_skip(
        self, ledger: LearningLedger, interviewer: LearningInterviewer
    ):
        issue = _seed_issue(ledger)
        interviewer.record_decision(issue.issue_id, "f")
        updated = ledger.get_issue(issue.issue_id)
        assert updated.resolution_type is None

    def test_choice_stripped_and_lowercased(
        self, ledger: LearningLedger, interviewer: LearningInterviewer
    ):
        issue = _seed_issue(ledger)
        interviewer.record_decision(issue.issue_id, "  A  ")
        updated = ledger.get_issue(issue.issue_id)
        assert updated.status == "applied"

    def test_unknown_choice_falls_back_to_open(
        self, ledger: LearningLedger, interviewer: LearningInterviewer
    ):
        issue = _seed_issue(ledger)
        interviewer.record_decision(issue.issue_id, "z")
        updated = ledger.get_issue(issue.issue_id)
        assert updated.status == "open"

    def test_returns_false_for_nonexistent_issue(self, interviewer: LearningInterviewer):
        result = interviewer.record_decision("no-such-issue-id", "a")
        assert result is False


# ---------------------------------------------------------------------------
# _type_label helper
# ---------------------------------------------------------------------------


class TestTypeLabel:
    @pytest.mark.parametrize("issue_type,expected_label", [
        ("routing_mismatch", "Routing Mismatch"),
        ("agent_degradation", "Agent Degradation"),
        ("knowledge_gap", "Knowledge Gap"),
        ("roster_bloat", "Roster Bloat"),
        ("gate_mismatch", "Gate Mismatch"),
        ("pattern_drift", "Pattern Drift"),
        ("prompt_evolution", "Prompt Evolution"),
    ])
    def test_known_types(self, issue_type: str, expected_label: str):
        assert _type_label(issue_type) == expected_label

    def test_unknown_type_titlecased(self):
        result = _type_label("my_custom_issue")
        assert result == "My Custom Issue"
