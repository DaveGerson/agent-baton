"""Tests for G1.1 — Escalation timeouts + required_role + escalate_to.

Covers:

* New fields round-trip through ``to_dict`` / ``from_dict``.
* Default values are backwards-compatible (empty role, 0 timeout, empty
  escalate_to) so historical records load cleanly.
* ``expired()`` flips at the timeout boundary.
* ``time_remaining()`` is ``None`` when no timeout is configured.
* ``next_role()`` returns ``escalate_to`` when expired AND set; otherwise
  falls back to ``required_role``.
* ``baton escalations --list`` shows ``EXPIRED`` for past-timeout entries.
* ``baton escalations --list --expired`` filters out non-expired entries.

The timeout behaviour is observation-only: nothing in this suite
exercises automatic paging or rerouting because none should exist.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agent_baton.cli.commands.govern import escalations as escalations_cli
from agent_baton.core.govern.escalation import EscalationManager
from agent_baton.models.escalation import Escalation


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

CREATED_AT = "2026-01-15T12:00:00+00:00"
CREATED_DT = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)


def _esc(
    *,
    agent_name: str = "backend-engineer",
    question: str = "Which database?",
    timestamp: str = CREATED_AT,
    required_role: str = "",
    timeout_minutes: int = 0,
    escalate_to: str = "",
) -> Escalation:
    return Escalation(
        agent_name=agent_name,
        question=question,
        timestamp=timestamp,
        required_role=required_role,
        timeout_minutes=timeout_minutes,
        escalate_to=escalate_to,
    )


# ---------------------------------------------------------------------------
# Defaults / backwards-compatibility
# ---------------------------------------------------------------------------

class TestDefaults:
    def test_default_field_values(self) -> None:
        esc = Escalation(agent_name="x", question="q")
        assert esc.required_role == ""
        assert esc.timeout_minutes == 0
        assert esc.escalate_to == ""

    def test_from_dict_without_new_fields_uses_defaults(self) -> None:
        """Historical dicts without the new keys must still load."""
        legacy = {
            "agent_name": "x",
            "question": "q",
            "context": "c",
            "options": ["a", "b"],
            "priority": "blocking",
            "timestamp": CREATED_AT,
            "resolved": False,
            "answer": "",
        }
        esc = Escalation.from_dict(legacy)
        assert esc.required_role == ""
        assert esc.timeout_minutes == 0
        assert esc.escalate_to == ""
        # Existing fields preserved
        assert esc.priority == "blocking"
        assert esc.options == ["a", "b"]


# ---------------------------------------------------------------------------
# Round-trip
# ---------------------------------------------------------------------------

class TestRoundTrip:
    def test_to_dict_includes_new_fields(self) -> None:
        esc = _esc(
            required_role="security-reviewer",
            timeout_minutes=30,
            escalate_to="auditor",
        )
        d = esc.to_dict()
        assert d["required_role"] == "security-reviewer"
        assert d["timeout_minutes"] == 30
        assert d["escalate_to"] == "auditor"

    def test_dict_round_trip_preserves_new_fields(self) -> None:
        original = _esc(
            required_role="tech-lead",
            timeout_minutes=15,
            escalate_to="auditor",
        )
        rebuilt = Escalation.from_dict(original.to_dict())
        assert rebuilt == original


# ---------------------------------------------------------------------------
# Timeout semantics
# ---------------------------------------------------------------------------

class TestExpired:
    def test_no_timeout_never_expired(self) -> None:
        esc = _esc(timeout_minutes=0)
        far_future = CREATED_DT + timedelta(days=365)
        assert esc.expired(now=far_future) is False

    def test_before_boundary_not_expired(self) -> None:
        esc = _esc(timeout_minutes=10)
        just_before = CREATED_DT + timedelta(minutes=10) - timedelta(seconds=1)
        assert esc.expired(now=just_before) is False

    def test_at_exact_boundary_is_expired(self) -> None:
        esc = _esc(timeout_minutes=10)
        boundary = CREATED_DT + timedelta(minutes=10)
        assert esc.expired(now=boundary) is True

    def test_after_boundary_is_expired(self) -> None:
        esc = _esc(timeout_minutes=10)
        after = CREATED_DT + timedelta(minutes=11)
        assert esc.expired(now=after) is True


class TestTimeRemaining:
    def test_no_timeout_returns_none(self) -> None:
        esc = _esc(timeout_minutes=0)
        assert esc.time_remaining(now=CREATED_DT) is None

    def test_returns_positive_when_in_future(self) -> None:
        esc = _esc(timeout_minutes=10)
        now = CREATED_DT + timedelta(minutes=3)
        remaining = esc.time_remaining(now=now)
        assert remaining == timedelta(minutes=7)

    def test_returns_negative_when_expired(self) -> None:
        esc = _esc(timeout_minutes=10)
        now = CREATED_DT + timedelta(minutes=15)
        remaining = esc.time_remaining(now=now)
        assert remaining is not None
        assert remaining < timedelta(0)


class TestNextRole:
    def test_not_expired_returns_required_role(self) -> None:
        esc = _esc(
            required_role="tech-lead",
            timeout_minutes=10,
            escalate_to="auditor",
        )
        before = CREATED_DT + timedelta(minutes=1)
        assert esc.next_role(now=before) == "tech-lead"

    def test_expired_with_escalate_to_uses_it(self) -> None:
        esc = _esc(
            required_role="tech-lead",
            timeout_minutes=10,
            escalate_to="auditor",
        )
        after = CREATED_DT + timedelta(minutes=11)
        assert esc.next_role(now=after) == "auditor"

    def test_expired_without_escalate_to_falls_back(self) -> None:
        esc = _esc(
            required_role="tech-lead",
            timeout_minutes=10,
            escalate_to="",
        )
        after = CREATED_DT + timedelta(minutes=11)
        assert esc.next_role(now=after) == "tech-lead"

    def test_no_timeout_never_escalates(self) -> None:
        esc = _esc(
            required_role="tech-lead",
            timeout_minutes=0,
            escalate_to="auditor",
        )
        far = CREATED_DT + timedelta(days=365)
        assert esc.next_role(now=far) == "tech-lead"


# ---------------------------------------------------------------------------
# Markdown round-trip via EscalationManager
# ---------------------------------------------------------------------------

class TestMarkdownRoundTrip:
    def test_manager_persists_new_fields(self, tmp_path: Path) -> None:
        manager = EscalationManager(path=tmp_path / "escalations.md")
        original = _esc(
            required_role="security-reviewer",
            timeout_minutes=20,
            escalate_to="auditor",
        )
        manager.add(original)

        loaded = manager.get_all()
        assert len(loaded) == 1
        assert loaded[0].required_role == "security-reviewer"
        assert loaded[0].timeout_minutes == 20
        assert loaded[0].escalate_to == "auditor"


# ---------------------------------------------------------------------------
# CLI: baton escalations --list / --expired
# ---------------------------------------------------------------------------

@pytest.fixture
def cli_manager(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> EscalationManager:
    """Patch the CLI to use a per-test EscalationManager."""
    manager = EscalationManager(path=tmp_path / "escalations.md")
    monkeypatch.setattr(
        escalations_cli,
        "EscalationManager",
        lambda: manager,
    )
    return manager


def _ns(**overrides: object) -> argparse.Namespace:
    base: dict[str, object] = {
        "all": False,
        "resolve": None,
        "clear": False,
        "list": False,
        "expired": False,
    }
    base.update(overrides)
    return argparse.Namespace(**base)


class TestCliList:
    def test_empty_list_is_graceful(
        self,
        cli_manager: EscalationManager,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        escalations_cli.handler(_ns(list=True))
        out = capsys.readouterr().out
        assert "No escalations." in out

    def test_list_shows_expired_marker(
        self,
        cli_manager: EscalationManager,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        # Created far in the past with a 1-minute timeout → guaranteed expired.
        old = (datetime.now(tz=timezone.utc) - timedelta(hours=2)).isoformat()
        cli_manager.add(_esc(
            timestamp=old,
            required_role="tech-lead",
            timeout_minutes=1,
            escalate_to="auditor",
        ))

        escalations_cli.handler(_ns(list=True))
        out = capsys.readouterr().out
        assert "EXPIRED" in out
        assert "tech-lead" in out
        # next_role should surface the escalate_to target since it's expired.
        assert "auditor" in out

    def test_list_shows_columns(
        self,
        cli_manager: EscalationManager,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        cli_manager.add(_esc(required_role="tech-lead", timeout_minutes=0))
        escalations_cli.handler(_ns(list=True))
        out = capsys.readouterr().out
        for header in ("ID", "REQUIRED_ROLE", "TIME_REMAINING", "NEXT_ROLE"):
            assert header in out

    def test_expired_filter_excludes_pending(
        self,
        cli_manager: EscalationManager,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        future = (datetime.now(tz=timezone.utc) + timedelta(minutes=5)).isoformat()
        old = (datetime.now(tz=timezone.utc) - timedelta(hours=2)).isoformat()

        cli_manager.add(_esc(
            agent_name="fresh-agent",
            timestamp=future,
            required_role="tech-lead",
            timeout_minutes=60,
        ))
        cli_manager.add(_esc(
            agent_name="stale-agent",
            timestamp=old,
            required_role="security-reviewer",
            timeout_minutes=1,
            escalate_to="auditor",
        ))

        escalations_cli.handler(_ns(list=True, expired=True))
        out = capsys.readouterr().out

        assert "security-reviewer" in out
        assert "fresh-agent" not in out  # pending escalation excluded by --expired

    def test_expired_filter_empty_message(
        self,
        cli_manager: EscalationManager,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        # One non-expired escalation; --expired should yield empty table.
        future = (datetime.now(tz=timezone.utc) + timedelta(minutes=5)).isoformat()
        cli_manager.add(_esc(
            timestamp=future,
            required_role="tech-lead",
            timeout_minutes=60,
        ))
        escalations_cli.handler(_ns(list=True, expired=True))
        out = capsys.readouterr().out
        assert "No escalations." in out
