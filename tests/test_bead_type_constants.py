"""Tests for the bead_type vocabulary constants added in schema v15.

The constants are a documentation aid and an advisory validator for the
new team-board layer (``task``, ``message``, ``message_ack``).  The Bead
model itself remains free-form — these values do not gate writes — so
the constants serve as a catalog rather than an enum.
"""
from __future__ import annotations

from agent_baton.models.bead import (
    AGENT_SIGNAL_BEAD_TYPES,
    KNOWN_BEAD_TYPES,
    TEAM_BOARD_BEAD_TYPES,
    is_known_bead_type,
)


class TestBeadTypeConstants:
    def test_agent_signal_types_are_stable(self) -> None:
        assert AGENT_SIGNAL_BEAD_TYPES == frozenset({
            "discovery", "decision", "warning", "outcome", "planning",
        })

    def test_team_board_types_are_new_vocabulary(self) -> None:
        assert TEAM_BOARD_BEAD_TYPES == frozenset({
            "task", "message", "message_ack",
        })

    def test_known_is_union(self) -> None:
        assert KNOWN_BEAD_TYPES == AGENT_SIGNAL_BEAD_TYPES | TEAM_BOARD_BEAD_TYPES

    def test_no_overlap_between_layers(self) -> None:
        assert not AGENT_SIGNAL_BEAD_TYPES & TEAM_BOARD_BEAD_TYPES


class TestIsKnownBeadType:
    def test_agent_signal_types_are_known(self) -> None:
        assert is_known_bead_type("discovery")
        assert is_known_bead_type("decision")
        assert is_known_bead_type("warning")

    def test_team_board_types_are_known(self) -> None:
        assert is_known_bead_type("task")
        assert is_known_bead_type("message")
        assert is_known_bead_type("message_ack")

    def test_unknown_types_are_rejected(self) -> None:
        assert not is_known_bead_type("")
        assert not is_known_bead_type("totally-made-up")
        assert not is_known_bead_type("Task")  # case-sensitive
