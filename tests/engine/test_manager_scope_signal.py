"""Tests for :mod:`agent_baton.core.engine.manager_scope_signal` (M9).

See docs/internal/manager-mode-pmo-plan.md Task 13 and
docs/specs/agent-baton-claude-code-middle-manager-prd-tdd.md §13.2.
"""
from __future__ import annotations

from agent_baton.core.engine.manager_scope_signal import (
    ScopeExpansionSignal,
    parse_scope_expansion_signals,
)


class TestParseScopeExpansionSignals:
    def test_single_signal_em_dash(self) -> None:
        outcome = (
            "Implemented the service layer.\n"
            "SCOPE_EXPANSION: app/auth/session.py — session metadata needed\n"
            "All tests pass."
        )
        signals = parse_scope_expansion_signals(outcome, step_id="2.1")
        assert signals == [
            ScopeExpansionSignal(
                path="app/auth/session.py",
                reason="session metadata needed",
                step_id="2.1",
            )
        ]

    def test_hyphen_separator_accepted(self) -> None:
        outcome = "SCOPE_EXPANSION: app/reporting/service.py - needs a new dependency"
        signals = parse_scope_expansion_signals(outcome)
        assert len(signals) == 1
        assert signals[0].path == "app/reporting/service.py"
        assert signals[0].reason == "needs a new dependency"

    def test_multiple_signals(self) -> None:
        outcome = (
            "SCOPE_EXPANSION: app/a.py — reason a\n"
            "SCOPE_EXPANSION: app/b.py — reason b\n"
        )
        signals = parse_scope_expansion_signals(outcome)
        assert [s.path for s in signals] == ["app/a.py", "app/b.py"]
        assert [s.reason for s in signals] == ["reason a", "reason b"]

    def test_case_insensitive_prefix(self) -> None:
        outcome = "scope_expansion: app/a.py — lowercase prefix"
        signals = parse_scope_expansion_signals(outcome)
        assert len(signals) == 1
        assert signals[0].path == "app/a.py"

    def test_no_signal(self) -> None:
        assert parse_scope_expansion_signals("Nothing to see here.") == []

    def test_empty_text(self) -> None:
        assert parse_scope_expansion_signals("") == []

    def test_free_text_format_does_not_match(self) -> None:
        """The unrelated adaptive-replanning free-text format
        (``SCOPE_EXPANSION: <description>``, no path/reason split) must
        NOT match this stricter parser -- the two signal formats are
        independent (see module docstring)."""
        outcome = "SCOPE_EXPANSION: Add RBAC middleware to auth module"
        assert parse_scope_expansion_signals(outcome) == []

    def test_empty_path_or_reason_ignored(self) -> None:
        outcome = "SCOPE_EXPANSION:  — reason with no path\n"
        assert parse_scope_expansion_signals(outcome) == []

    def test_deduplicates_identical_pairs(self) -> None:
        outcome = (
            "SCOPE_EXPANSION: app/a.py — dup reason\n"
            "SCOPE_EXPANSION: app/a.py — dup reason\n"
        )
        signals = parse_scope_expansion_signals(outcome)
        assert len(signals) == 1

    def test_caps_at_max_signals_per_step(self) -> None:
        outcome = "\n".join(
            f"SCOPE_EXPANSION: app/{i}.py — reason {i}" for i in range(20)
        )
        signals = parse_scope_expansion_signals(outcome)
        assert len(signals) == 8

    def test_step_id_defaults_empty(self) -> None:
        signals = parse_scope_expansion_signals("SCOPE_EXPANSION: a.py — r")
        assert signals[0].step_id == ""

    def test_reason_captures_rest_of_line_only(self) -> None:
        outcome = (
            "SCOPE_EXPANSION: app/a.py — first reason\n"
            "This next line is not part of the signal.\n"
        )
        signals = parse_scope_expansion_signals(outcome)
        assert signals[0].reason == "first reason"
