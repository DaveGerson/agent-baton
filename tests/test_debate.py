"""Tests for D4 — Multi-Agent Debate (Tier-4 research feature).

Covers:
- Schema migration creates the debates table.
- ViewpointSpec / DebateTurn / DebateResult dataclass shape.
- DebateOrchestrator with a mocked runner returns a transcript of the
  expected length.
- Round 2 prompts include round 1 in their context.
- The moderator is dispatched after rounds complete.
- Persistence to the debates table.
- CLI viewpoint parsing.
- CLI --summary-only flag emits no transcript.
- Graceful handling of runner failure.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

import pytest

from agent_baton.cli.commands import debate_cmd
from agent_baton.core.intel.debate import (
    DEFAULT_MODERATOR,
    DebateOrchestrator,
    DebateResult,
    DebateTurn,
    ViewpointSpec,
    persist_debate,
    stub_runner,
)
from agent_baton.core.storage.connection import ConnectionManager
from agent_baton.core.storage.schema import PROJECT_SCHEMA_DDL, SCHEMA_VERSION


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _open_project_db(tmp_path: Path) -> tuple[sqlite3.Connection, Path]:
    db_path = tmp_path / "baton.db"
    mgr = ConnectionManager(db_path)
    mgr.configure_schema(PROJECT_SCHEMA_DDL, SCHEMA_VERSION)
    return mgr.get_connection(), db_path


class RecordingRunner:
    """Mock DebateRunner that records prompts and returns canned replies.

    ``replies`` is a list of (predicate, response) — the first entry whose
    predicate(agent_name) is truthy is used.  Falls back to a deterministic
    default response if no predicate matches.
    """

    def __init__(self, replies: list[tuple] | None = None) -> None:
        self.calls: list[tuple[str, str]] = []
        self._replies = replies or []

    async def __call__(self, agent_name: str, prompt: str) -> str:
        self.calls.append((agent_name, prompt))
        for pred, reply in self._replies:
            if pred(agent_name):
                if callable(reply):
                    return reply(agent_name, prompt, len(self.calls))
                return reply
        return f"reply from {agent_name} #{len(self.calls)}"


def _moderator_reply(rec: str = "Adopt option A.", unresolved: list[str] | None = None) -> str:
    parts = ["## Recommendation", rec, "", "## Unresolved"]
    if unresolved:
        for u in unresolved:
            parts.append(f"- {u}")
    else:
        parts.append("- none")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


def test_migration_creates_table(tmp_path):
    conn, _ = _open_project_db(tmp_path)
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='debates'"
    ).fetchall()
    assert rows, "debates table missing from PROJECT_SCHEMA_DDL"

    cols = {r[1] for r in conn.execute("PRAGMA table_info(debates)").fetchall()}
    assert {
        "debate_id",
        "question",
        "transcript_json",
        "recommendation",
        "unresolved_json",
        "created_at",
    } <= cols


def test_schema_version_is_30():
    assert SCHEMA_VERSION >= 30, "SCHEMA_VERSION must be at least 30 for D4"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


def test_viewpoint_spec_dataclass():
    vp = ViewpointSpec(agent_name="security-reviewer", framing="defense in depth")
    assert vp.agent_name == "security-reviewer"
    assert vp.framing == "defense in depth"


def test_debate_turn_has_timestamp():
    turn = DebateTurn(agent_name="x", round_number=1, content="hi")
    assert turn.timestamp  # auto-populated by default factory


def test_debate_result_to_dict_shape():
    result = DebateResult(
        question="Q?",
        transcript=[DebateTurn("a", 1, "c1")],
        recommendation="R",
        unresolved=["u1"],
        debate_id="db-x",
    )
    d = result.to_dict()
    assert d["question"] == "Q?"
    assert d["recommendation"] == "R"
    assert d["unresolved"] == ["u1"]
    assert d["transcript"][0]["agent_name"] == "a"
    assert d["debate_id"] == "db-x"


# ---------------------------------------------------------------------------
# DebateOrchestrator core flow
# ---------------------------------------------------------------------------


def test_run_debate_with_mock_runner_returns_transcript():
    runner = RecordingRunner(
        replies=[
            (lambda a: a == DEFAULT_MODERATOR, _moderator_reply("Pick A.", ["x vs y"])),
        ]
    )
    orch = DebateOrchestrator(runner=runner)
    result = orch.run_debate(
        question="Pick a strategy.",
        viewpoints=[
            ViewpointSpec("architect", "long-term"),
            ViewpointSpec("backend-engineer", "pragmatic"),
        ],
        rounds=2,
    )

    # 2 viewpoints * 2 rounds = 4 turns
    assert len(result.transcript) == 4
    assert [(t.agent_name, t.round_number) for t in result.transcript] == [
        ("architect", 1),
        ("backend-engineer", 1),
        ("architect", 2),
        ("backend-engineer", 2),
    ]
    assert result.recommendation == "Pick A."
    assert result.unresolved == ["x vs y"]


def test_round_2_includes_round_1_in_context():
    runner = RecordingRunner(
        replies=[
            (lambda a: a == DEFAULT_MODERATOR, _moderator_reply()),
        ]
    )
    orch = DebateOrchestrator(runner=runner)
    orch.run_debate(
        question="Q?",
        viewpoints=[
            ViewpointSpec("agent1", "framing1"),
            ViewpointSpec("agent2", "framing2"),
        ],
        rounds=2,
    )

    # Calls 0-1 are round 1; call 2 is round 2 first viewpoint.
    round_2_first_prompt = runner.calls[2][1]
    assert "Round 2" in round_2_first_prompt
    assert "reply from agent1 #1" in round_2_first_prompt
    assert "reply from agent2 #2" in round_2_first_prompt


def test_moderator_dispatched_after_rounds_complete():
    runner = RecordingRunner(
        replies=[
            (lambda a: a == "moderator-bot", _moderator_reply("Final.")),
        ]
    )
    orch = DebateOrchestrator(runner=runner)
    orch.run_debate(
        question="Q?",
        viewpoints=[
            ViewpointSpec("a1", "f1"),
            ViewpointSpec("a2", "f2"),
        ],
        rounds=1,
        moderator_agent="moderator-bot",
    )
    # 2 viewpoints * 1 round + 1 moderator = 3 calls
    assert len(runner.calls) == 3
    assert runner.calls[-1][0] == "moderator-bot"
    mod_prompt = runner.calls[-1][1]
    assert "reply from a1 #1" in mod_prompt
    assert "reply from a2 #2" in mod_prompt


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def test_persists_to_db(tmp_path):
    conn, db_path = _open_project_db(tmp_path)
    runner = RecordingRunner(
        replies=[
            (lambda a: a == DEFAULT_MODERATOR, _moderator_reply("Decision: X.", ["edge case Y"])),
        ]
    )
    orch = DebateOrchestrator(runner=runner, db_path=str(db_path))
    result = orch.run_debate(
        question="Should we ship X?",
        viewpoints=[
            ViewpointSpec("architect", "f1"),
            ViewpointSpec("backend-engineer", "f2"),
        ],
        rounds=1,
    )

    rows = conn.execute(
        "SELECT debate_id, question, recommendation, transcript_json, unresolved_json "
        "FROM debates WHERE debate_id = ?",
        (result.debate_id,),
    ).fetchall()
    assert len(rows) == 1
    row = rows[0]
    assert row[1] == "Should we ship X?"
    assert row[2] == "Decision: X."

    transcript = json.loads(row[3])
    assert len(transcript) == 2  # 2 viewpoints * 1 round
    assert transcript[0]["agent_name"] == "architect"

    unresolved = json.loads(row[4])
    assert unresolved == ["edge case Y"]


def test_persist_debate_is_idempotent(tmp_path):
    _, db_path = _open_project_db(tmp_path)
    result = DebateResult(
        question="q",
        transcript=[DebateTurn("a", 1, "c")],
        recommendation="r",
        unresolved=[],
        debate_id="db-fixed",
    )
    persist_debate(str(db_path), result)
    persist_debate(str(db_path), result)

    conn = sqlite3.connect(str(db_path))
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM debates WHERE debate_id = ?", ("db-fixed",)
        ).fetchone()[0]
    finally:
        conn.close()
    assert count == 1


# ---------------------------------------------------------------------------
# CLI parsing
# ---------------------------------------------------------------------------


def test_cli_parses_viewpoints_format():
    parsed = debate_cmd.parse_viewpoints(
        "architect:long-term,security-reviewer:risk minimization"
    )
    assert parsed == [
        ViewpointSpec("architect", "long-term"),
        ViewpointSpec("security-reviewer", "risk minimization"),
    ]


def test_cli_parses_viewpoints_strips_whitespace():
    parsed = debate_cmd.parse_viewpoints("  a : f1 ,  b : f2  ")
    assert parsed == [ViewpointSpec("a", "f1"), ViewpointSpec("b", "f2")]


def test_cli_rejects_viewpoint_missing_colon():
    with pytest.raises(ValueError):
        debate_cmd.parse_viewpoints("architect-only")


def test_cli_rejects_empty_agent():
    with pytest.raises(ValueError):
        debate_cmd.parse_viewpoints(":framing-without-agent")


def test_cli_summary_only_flag(monkeypatch, capsys):
    """--summary-only must omit the transcript section but still emit recommendation."""
    mock_runner = RecordingRunner(
        replies=[
            (lambda a: a == DEFAULT_MODERATOR, _moderator_reply("Concise rec.", ["open Q"])),
        ]
    )

    monkeypatch.setattr(
        debate_cmd,
        "DebateOrchestrator",
        lambda runner=None, db_path=None: DebateOrchestrator(runner=mock_runner, db_path=None),
    )

    args = argparse.Namespace(
        question="Q?",
        viewpoints="a1:f1,a2:f2",
        rounds=1,
        moderator=DEFAULT_MODERATOR,
        output="text",
        summary_only=True,
        db_path=None,
        dry_run=False,
    )
    debate_cmd.handler(args)
    captured = capsys.readouterr().out
    assert "## Recommendation" in captured
    assert "Concise rec." in captured
    assert "## Unresolved" in captured
    assert "## Transcript" not in captured


def test_cli_full_text_includes_transcript(monkeypatch, capsys):
    mock_runner = RecordingRunner(
        replies=[
            (lambda a: a == DEFAULT_MODERATOR, _moderator_reply()),
        ]
    )
    monkeypatch.setattr(
        debate_cmd,
        "DebateOrchestrator",
        lambda runner=None, db_path=None: DebateOrchestrator(runner=mock_runner, db_path=None),
    )
    args = argparse.Namespace(
        question="Q?",
        viewpoints="a1:f1,a2:f2",
        rounds=1,
        moderator=DEFAULT_MODERATOR,
        output="text",
        summary_only=False,
        db_path=None,
        dry_run=False,
    )
    debate_cmd.handler(args)
    captured = capsys.readouterr().out
    assert "## Transcript" in captured
    assert "a1" in captured
    assert "a2" in captured


# ---------------------------------------------------------------------------
# Failure handling
# ---------------------------------------------------------------------------


def test_handles_runner_failure_gracefully():
    """A runner that raises must NOT crash the debate — failure is captured
    in the turn's content as a tagged error."""

    class FailingRunner:
        def __init__(self):
            self.n = 0

        async def __call__(self, agent_name, prompt):
            self.n += 1
            if self.n == 2:
                raise RuntimeError("network down")
            return f"ok from {agent_name}"

    runner = FailingRunner()
    orch = DebateOrchestrator(runner=runner)
    result = orch.run_debate(
        question="Q?",
        viewpoints=[
            ViewpointSpec("a1", "f1"),
            ViewpointSpec("a2", "f2"),
        ],
        rounds=1,
    )
    assert len(result.transcript) == 2
    failed_turn = result.transcript[1]
    assert "[dispatch failed" in failed_turn.content


def test_run_debate_rejects_too_few_viewpoints():
    orch = DebateOrchestrator(runner=RecordingRunner())
    with pytest.raises(ValueError):
        orch.run_debate("Q?", [ViewpointSpec("only", "one")])


def test_run_debate_rejects_too_many_viewpoints():
    orch = DebateOrchestrator(runner=RecordingRunner())
    too_many = [ViewpointSpec(f"a{i}", "f") for i in range(6)]
    with pytest.raises(ValueError):
        orch.run_debate("Q?", too_many)


def test_run_debate_rejects_zero_rounds():
    orch = DebateOrchestrator(runner=RecordingRunner())
    with pytest.raises(ValueError):
        orch.run_debate(
            "Q?",
            [ViewpointSpec("a", "f"), ViewpointSpec("b", "g")],
            rounds=0,
        )


# ---------------------------------------------------------------------------
# Stub runner sanity
# ---------------------------------------------------------------------------


def test_stub_runner_produces_text():
    out = stub_runner("architect", "anything")
    assert "architect" in out
    assert "stub-runner" in out
