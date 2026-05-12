"""Unit tests for the team mailbox (A2.a).

Covers append + read semantics, ordering, the member-filtering helpers,
and the teardown marker behavior. Concurrency is not asserted here —
that's a single-line POSIX write on local FS.
"""
from __future__ import annotations

from pathlib import Path

from agent_baton.core.engine.mailbox import (
    MailboxEvent,
    TeamMailbox,
    open_mailbox,
)


def test_append_and_read_roundtrip(tmp_path: Path) -> None:
    mb = open_mailbox(tmp_path, "team-A")
    e1 = mb.append(
        "task_created", from_member="lead",
        task_ref="step-1.1.a", subject="security review",
    )
    e2 = mb.append(
        "teammate_message", from_member="reviewer",
        to_member="implementer", subject="checked the auth module",
        body="LGTM modulo the JWT TTL.",
    )
    assert e1.event_id == "e1"
    assert e2.event_id == "e2"

    events = mb.read_all()
    assert [e.event_id for e in events] == ["e1", "e2"]
    assert events[1].body.startswith("LGTM")


def test_read_since(tmp_path: Path) -> None:
    mb = open_mailbox(tmp_path, "team-B")
    mb.append("task_created", from_member="lead", task_ref="a")
    mb.append("task_completed", from_member="impl", task_ref="a")
    mb.append("teammate_idle", from_member="impl")

    after_e1 = mb.read_since("e1")
    assert [e.event_id for e in after_e1] == ["e2", "e3"]

    from_start = mb.read_since(None)
    assert len(from_start) == 3


def test_read_for_member_includes_broadcasts(tmp_path: Path) -> None:
    mb = open_mailbox(tmp_path, "team-C")
    mb.append("teammate_message", from_member="lead",
              to_member="alpha", subject="for alpha only")
    mb.append("teammate_message", from_member="lead",
              to_member="*", subject="broadcast")
    mb.append("teammate_message", from_member="lead",
              to_member="beta", subject="for beta only")

    received = mb.read_for_member("alpha")
    subjects = [e.subject for e in received]
    assert "for alpha only" in subjects
    assert "broadcast" in subjects
    assert "for beta only" not in subjects


def test_event_ids_are_monotonic_across_reopens(tmp_path: Path) -> None:
    """The mailbox derives event_id from the file's existing line count
    so reopening doesn't reset the counter."""
    mb1 = open_mailbox(tmp_path, "team-D")
    mb1.append("task_created", from_member="lead", task_ref="x")
    mb1.append("task_created", from_member="lead", task_ref="y")

    mb2 = open_mailbox(tmp_path, "team-D")
    e3 = mb2.append("task_created", from_member="lead", task_ref="z")
    assert e3.event_id == "e3"
    assert len(mb2.read_all()) == 3


def test_teardown_marker_does_not_delete_events(tmp_path: Path) -> None:
    mb = open_mailbox(tmp_path, "team-E")
    mb.append("task_created", from_member="lead", task_ref="x")
    marker = mb.teardown_marker()
    assert marker.exists()
    assert mb.is_torn_down()
    # Audit trail survives teardown.
    assert len(mb.read_all()) == 1


def test_malformed_line_is_skipped_not_raised(tmp_path: Path) -> None:
    mb = open_mailbox(tmp_path, "team-F")
    mb.append("task_created", from_member="lead", task_ref="x")
    # Inject a corrupt line directly.
    with open(mb.path, "a", encoding="utf-8") as fh:
        fh.write("{not valid json\n")
    mb.append("task_completed", from_member="lead", task_ref="x")
    events = mb.read_all()
    # Two valid events; the garbage is skipped.
    assert [e.event_type for e in events] == ["task_created", "task_completed"]


def test_event_serialization_is_jsonl_compatible(tmp_path: Path) -> None:
    """One event = one line, no embedded newlines."""
    mb = open_mailbox(tmp_path, "team-G")
    mb.append(
        "teammate_message", from_member="a",
        body="multi\nline\nbody",
    )
    text = mb.path.read_text(encoding="utf-8")
    assert text.count("\n") == 1, (
        f"Expected exactly one trailing newline; got {text.count(chr(10))}"
    )
