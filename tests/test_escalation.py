"""Tests for agent_baton.models.escalation and agent_baton.core.escalation."""
from __future__ import annotations

from pathlib import Path

import pytest

from agent_baton.models.escalation import Escalation
from agent_baton.core.escalation import EscalationManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _escalation(
    agent_name: str = "backend-engineer",
    question: str = "Which database should I use?",
    *,
    context: str = "Designing the schema",
    options: list[str] | None = None,
    priority: str = "normal",
    timestamp: str = "2026-01-15T12:00:00+00:00",
    resolved: bool = False,
    answer: str = "",
) -> Escalation:
    return Escalation(
        agent_name=agent_name,
        question=question,
        context=context,
        options=options or [],
        priority=priority,
        timestamp=timestamp,
        resolved=resolved,
        answer=answer,
    )


def _manager(tmp_path: Path) -> EscalationManager:
    return EscalationManager(path=tmp_path / "escalations.md")


# ---------------------------------------------------------------------------
# Escalation.to_markdown
# ---------------------------------------------------------------------------

class TestEscalationToMarkdown:
    def test_pending_status_in_header(self) -> None:
        esc = _escalation(resolved=False)
        md = esc.to_markdown()
        assert "PENDING" in md

    def test_resolved_status_in_header(self) -> None:
        esc = _escalation(resolved=True)
        md = esc.to_markdown()
        assert "RESOLVED" in md

    def test_agent_name_in_header(self) -> None:
        esc = _escalation(agent_name="architect")
        md = esc.to_markdown()
        assert "architect" in md

    def test_timestamp_in_header(self) -> None:
        esc = _escalation(timestamp="2026-01-15T12:00:00+00:00")
        md = esc.to_markdown()
        assert "2026-01-15T12:00:00+00:00" in md

    def test_question_field(self) -> None:
        esc = _escalation(question="Should I use Postgres or MySQL?")
        md = esc.to_markdown()
        assert "**Question:** Should I use Postgres or MySQL?" in md

    def test_context_field(self) -> None:
        esc = _escalation(context="Building the auth module")
        md = esc.to_markdown()
        assert "**Context:** Building the auth module" in md

    def test_priority_field(self) -> None:
        esc = _escalation(priority="blocking")
        md = esc.to_markdown()
        assert "**Priority:** blocking" in md

    def test_options_comma_joined(self) -> None:
        esc = _escalation(options=["Postgres", "MySQL", "SQLite"])
        md = esc.to_markdown()
        assert "**Options:** Postgres, MySQL, SQLite" in md

    def test_options_empty_renders_blank(self) -> None:
        esc = _escalation(options=[])
        md = esc.to_markdown()
        assert "**Options:** " in md

    def test_answer_field_when_resolved(self) -> None:
        esc = _escalation(resolved=True, answer="Use Postgres")
        md = esc.to_markdown()
        assert "**Answer:** Use Postgres" in md

    def test_answer_empty_when_pending(self) -> None:
        esc = _escalation(resolved=False, answer="")
        md = esc.to_markdown()
        assert "**Answer:** " in md

    def test_header_format_starts_with_three_hashes(self) -> None:
        esc = _escalation()
        lines = esc.to_markdown().splitlines()
        assert lines[0].startswith("### ")

    def test_timestamp_auto_populated_when_empty(self) -> None:
        esc = Escalation(agent_name="agent", question="Q?")
        assert esc.timestamp != ""


# ---------------------------------------------------------------------------
# EscalationManager.add
# ---------------------------------------------------------------------------

class TestEscalationManagerAdd:
    def test_creates_file_on_first_add(self, tmp_path: Path) -> None:
        mgr = _manager(tmp_path)
        mgr.add(_escalation())
        assert mgr.path.exists()

    def test_file_contains_agent_name(self, tmp_path: Path) -> None:
        mgr = _manager(tmp_path)
        mgr.add(_escalation(agent_name="security-reviewer"))
        content = mgr.path.read_text(encoding="utf-8")
        assert "security-reviewer" in content

    def test_file_contains_question(self, tmp_path: Path) -> None:
        mgr = _manager(tmp_path)
        mgr.add(_escalation(question="Which auth scheme is best?"))
        content = mgr.path.read_text(encoding="utf-8")
        assert "Which auth scheme is best?" in content

    def test_second_add_appends_not_overwrites(self, tmp_path: Path) -> None:
        mgr = _manager(tmp_path)
        mgr.add(_escalation(agent_name="agent-a", question="Q1?"))
        mgr.add(_escalation(agent_name="agent-b", question="Q2?"))
        escalations = mgr.get_all()
        assert len(escalations) == 2

    def test_add_creates_parent_directories(self, tmp_path: Path) -> None:
        deep_path = tmp_path / "a" / "b" / "escalations.md"
        mgr = EscalationManager(path=deep_path)
        mgr.add(_escalation())
        assert deep_path.exists()


# ---------------------------------------------------------------------------
# EscalationManager.get_pending
# ---------------------------------------------------------------------------

class TestGetPending:
    def test_returns_empty_list_when_no_file(self, tmp_path: Path) -> None:
        mgr = _manager(tmp_path)
        assert mgr.get_pending() == []

    def test_returns_only_unresolved(self, tmp_path: Path) -> None:
        mgr = _manager(tmp_path)
        mgr.add(_escalation(agent_name="agent-a", resolved=False))
        mgr.add(_escalation(agent_name="agent-b", resolved=True, answer="done"))
        pending = mgr.get_pending()
        assert len(pending) == 1
        assert pending[0].agent_name == "agent-a"

    def test_returns_all_when_none_resolved(self, tmp_path: Path) -> None:
        mgr = _manager(tmp_path)
        mgr.add(_escalation(agent_name="a1"))
        mgr.add(_escalation(agent_name="a2"))
        assert len(mgr.get_pending()) == 2

    def test_returns_empty_when_all_resolved(self, tmp_path: Path) -> None:
        mgr = _manager(tmp_path)
        mgr.add(_escalation(agent_name="a1", resolved=True, answer="yes"))
        assert mgr.get_pending() == []


# ---------------------------------------------------------------------------
# EscalationManager.get_all
# ---------------------------------------------------------------------------

class TestGetAll:
    def test_returns_empty_list_when_no_file(self, tmp_path: Path) -> None:
        mgr = _manager(tmp_path)
        assert mgr.get_all() == []

    def test_returns_resolved_and_unresolved(self, tmp_path: Path) -> None:
        mgr = _manager(tmp_path)
        mgr.add(_escalation(agent_name="agent-a", resolved=False))
        mgr.add(_escalation(agent_name="agent-b", resolved=True, answer="done"))
        all_escs = mgr.get_all()
        assert len(all_escs) == 2

    def test_preserves_order(self, tmp_path: Path) -> None:
        mgr = _manager(tmp_path)
        mgr.add(_escalation(agent_name="first"))
        mgr.add(_escalation(agent_name="second"))
        mgr.add(_escalation(agent_name="third"))
        names = [e.agent_name for e in mgr.get_all()]
        assert names == ["first", "second", "third"]

    def test_roundtrip_preserves_fields(self, tmp_path: Path) -> None:
        mgr = _manager(tmp_path)
        original = _escalation(
            agent_name="architect",
            question="Monolith or microservices?",
            context="Initial design phase",
            options=["Monolith", "Microservices"],
            priority="blocking",
        )
        mgr.add(original)
        retrieved = mgr.get_all()[0]
        assert retrieved.agent_name == "architect"
        assert retrieved.question == "Monolith or microservices?"
        assert retrieved.context == "Initial design phase"
        assert retrieved.options == ["Monolith", "Microservices"]
        assert retrieved.priority == "blocking"
        assert not retrieved.resolved


# ---------------------------------------------------------------------------
# EscalationManager.resolve
# ---------------------------------------------------------------------------

class TestResolve:
    def test_resolve_returns_true_when_found(self, tmp_path: Path) -> None:
        mgr = _manager(tmp_path)
        mgr.add(_escalation(agent_name="backend-engineer"))
        assert mgr.resolve("backend-engineer", "Use Postgres") is True

    def test_resolve_returns_false_when_not_found(self, tmp_path: Path) -> None:
        mgr = _manager(tmp_path)
        assert mgr.resolve("nonexistent-agent", "answer") is False

    def test_resolve_marks_escalation_as_resolved(self, tmp_path: Path) -> None:
        mgr = _manager(tmp_path)
        mgr.add(_escalation(agent_name="backend-engineer"))
        mgr.resolve("backend-engineer", "Use Postgres")
        all_escs = mgr.get_all()
        assert all_escs[0].resolved is True

    def test_resolve_stores_answer(self, tmp_path: Path) -> None:
        mgr = _manager(tmp_path)
        mgr.add(_escalation(agent_name="backend-engineer"))
        mgr.resolve("backend-engineer", "Use Postgres")
        all_escs = mgr.get_all()
        assert all_escs[0].answer == "Use Postgres"

    def test_resolve_targets_oldest_pending_for_agent(self, tmp_path: Path) -> None:
        mgr = _manager(tmp_path)
        mgr.add(_escalation(
            agent_name="backend-engineer",
            question="Q1?",
            timestamp="2026-01-01T00:00:00+00:00",
        ))
        mgr.add(_escalation(
            agent_name="backend-engineer",
            question="Q2?",
            timestamp="2026-01-02T00:00:00+00:00",
        ))
        mgr.resolve("backend-engineer", "Answer to Q1")
        all_escs = mgr.get_all()
        # First one should be resolved, second still pending
        assert all_escs[0].resolved is True
        assert all_escs[0].answer == "Answer to Q1"
        assert all_escs[1].resolved is False

    def test_resolve_does_not_affect_other_agents(self, tmp_path: Path) -> None:
        mgr = _manager(tmp_path)
        mgr.add(_escalation(agent_name="agent-a"))
        mgr.add(_escalation(agent_name="agent-b"))
        mgr.resolve("agent-a", "answer for a")
        all_escs = mgr.get_all()
        agent_b = next(e for e in all_escs if e.agent_name == "agent-b")
        assert not agent_b.resolved

    def test_resolve_returns_false_when_all_already_resolved(self, tmp_path: Path) -> None:
        mgr = _manager(tmp_path)
        mgr.add(_escalation(agent_name="backend-engineer"))
        mgr.resolve("backend-engineer", "first answer")
        # Already resolved — second call should return False
        assert mgr.resolve("backend-engineer", "second answer") is False


# ---------------------------------------------------------------------------
# EscalationManager.resolve_all
# ---------------------------------------------------------------------------

class TestResolveAll:
    def test_returns_count_of_resolved(self, tmp_path: Path) -> None:
        mgr = _manager(tmp_path)
        mgr.add(_escalation(agent_name="agent-a"))
        mgr.add(_escalation(agent_name="agent-b"))
        count = mgr.resolve_all({"agent-a": "answer a", "agent-b": "answer b"})
        assert count == 2

    def test_skips_missing_agents(self, tmp_path: Path) -> None:
        mgr = _manager(tmp_path)
        mgr.add(_escalation(agent_name="agent-a"))
        count = mgr.resolve_all({"agent-a": "answer", "ghost": "nope"})
        assert count == 1

    def test_resolved_entries_have_correct_answers(self, tmp_path: Path) -> None:
        mgr = _manager(tmp_path)
        mgr.add(_escalation(agent_name="agent-a"))
        mgr.add(_escalation(agent_name="agent-b"))
        mgr.resolve_all({"agent-a": "alpha", "agent-b": "beta"})
        by_name = {e.agent_name: e for e in mgr.get_all()}
        assert by_name["agent-a"].answer == "alpha"
        assert by_name["agent-b"].answer == "beta"

    def test_empty_answers_dict_returns_zero(self, tmp_path: Path) -> None:
        mgr = _manager(tmp_path)
        mgr.add(_escalation(agent_name="agent-a"))
        assert mgr.resolve_all({}) == 0


# ---------------------------------------------------------------------------
# EscalationManager.has_pending
# ---------------------------------------------------------------------------

class TestHasPending:
    def test_false_when_no_file(self, tmp_path: Path) -> None:
        mgr = _manager(tmp_path)
        assert mgr.has_pending() is False

    def test_true_when_pending_exists(self, tmp_path: Path) -> None:
        mgr = _manager(tmp_path)
        mgr.add(_escalation())
        assert mgr.has_pending() is True

    def test_false_after_all_resolved(self, tmp_path: Path) -> None:
        mgr = _manager(tmp_path)
        mgr.add(_escalation(agent_name="backend-engineer"))
        mgr.resolve("backend-engineer", "done")
        assert mgr.has_pending() is False

    def test_true_with_mixed_state(self, tmp_path: Path) -> None:
        mgr = _manager(tmp_path)
        mgr.add(_escalation(agent_name="agent-a", resolved=True, answer="yes"))
        mgr.add(_escalation(agent_name="agent-b", resolved=False))
        assert mgr.has_pending() is True


# ---------------------------------------------------------------------------
# EscalationManager.clear_resolved
# ---------------------------------------------------------------------------

class TestClearResolved:
    def test_removes_only_resolved(self, tmp_path: Path) -> None:
        mgr = _manager(tmp_path)
        mgr.add(_escalation(agent_name="agent-a"))
        mgr.add(_escalation(agent_name="agent-b", resolved=True, answer="done"))
        mgr.clear_resolved()
        remaining = mgr.get_all()
        assert len(remaining) == 1
        assert remaining[0].agent_name == "agent-a"

    def test_clears_all_when_all_resolved(self, tmp_path: Path) -> None:
        mgr = _manager(tmp_path)
        mgr.add(_escalation(agent_name="a", resolved=True, answer="x"))
        mgr.add(_escalation(agent_name="b", resolved=True, answer="y"))
        mgr.clear_resolved()
        assert mgr.get_all() == []

    def test_no_op_when_none_resolved(self, tmp_path: Path) -> None:
        mgr = _manager(tmp_path)
        mgr.add(_escalation(agent_name="agent-a"))
        mgr.add(_escalation(agent_name="agent-b"))
        mgr.clear_resolved()
        assert len(mgr.get_all()) == 2

    def test_no_op_when_file_missing(self, tmp_path: Path) -> None:
        mgr = _manager(tmp_path)
        mgr.clear_resolved()  # should not raise
        assert not mgr.path.exists() or mgr.get_all() == []

    def test_pending_count_unchanged_after_clear(self, tmp_path: Path) -> None:
        mgr = _manager(tmp_path)
        mgr.add(_escalation(agent_name="agent-a"))
        mgr.add(_escalation(agent_name="agent-b", resolved=True, answer="done"))
        mgr.clear_resolved()
        assert len(mgr.get_pending()) == 1


# ---------------------------------------------------------------------------
# Edge cases: empty / missing file
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_get_all_empty_file_returns_empty_list(self, tmp_path: Path) -> None:
        mgr = _manager(tmp_path)
        mgr.path.parent.mkdir(parents=True, exist_ok=True)
        mgr.path.write_text("", encoding="utf-8")
        assert mgr.get_all() == []

    def test_get_pending_empty_file_returns_empty_list(self, tmp_path: Path) -> None:
        mgr = _manager(tmp_path)
        mgr.path.parent.mkdir(parents=True, exist_ok=True)
        mgr.path.write_text("", encoding="utf-8")
        assert mgr.get_pending() == []

    def test_has_pending_empty_file_returns_false(self, tmp_path: Path) -> None:
        mgr = _manager(tmp_path)
        mgr.path.parent.mkdir(parents=True, exist_ok=True)
        mgr.path.write_text("", encoding="utf-8")
        assert mgr.has_pending() is False

    def test_file_with_only_header_returns_empty(self, tmp_path: Path) -> None:
        mgr = _manager(tmp_path)
        mgr.path.parent.mkdir(parents=True, exist_ok=True)
        mgr.path.write_text("# Escalations\n\n", encoding="utf-8")
        assert mgr.get_all() == []

    def test_multiple_adds_and_reads_roundtrip(self, tmp_path: Path) -> None:
        mgr = _manager(tmp_path)
        for i in range(5):
            mgr.add(_escalation(
                agent_name=f"agent-{i}",
                question=f"Question {i}?",
                timestamp=f"2026-01-{i + 1:02d}T00:00:00+00:00",
            ))
        all_escs = mgr.get_all()
        assert len(all_escs) == 5
        for i, esc in enumerate(all_escs):
            assert esc.agent_name == f"agent-{i}"
