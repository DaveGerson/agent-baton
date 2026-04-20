"""Tests for agent_baton.core.observe.jsonl_scanner."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_baton.core.observe.jsonl_scanner import (
    SessionTokenScan,
    _EMPTY,
    _parse_ts,
    _project_slug,
    scan_session,
)


# ---------------------------------------------------------------------------
# _parse_ts helpers
# ---------------------------------------------------------------------------

def test_parse_ts_returns_utc_datetime():
    dt = _parse_ts("2026-04-17T13:00:05.123Z")
    assert dt is not None
    assert dt.utcoffset().seconds == 0


def test_parse_ts_handles_offset():
    dt = _parse_ts("2026-04-17T13:00:05+00:00")
    assert dt is not None


def test_parse_ts_returns_none_for_empty():
    assert _parse_ts("") is None


def test_parse_ts_returns_none_for_garbage():
    assert _parse_ts("not-a-date") is None


# ---------------------------------------------------------------------------
# _project_slug
# ---------------------------------------------------------------------------

def test_project_slug_encodes_absolute_path():
    slug = _project_slug(Path("/home/user/myproject"))
    assert slug.startswith("-")
    assert "myproject" in slug


# ---------------------------------------------------------------------------
# scan_session — missing / empty inputs
# ---------------------------------------------------------------------------

def test_scan_session_empty_session_id_returns_empty():
    result = scan_session("", "2026-04-17T13:00:00Z")
    assert result == _EMPTY


def test_scan_session_empty_started_at_returns_empty():
    result = scan_session("some-session-id", "")
    assert result == _EMPTY


def test_scan_session_missing_file_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("BATON_PROJECT_ROOT", str(tmp_path))
    # ~/.claude/projects doesn't exist in tmp_path — expect graceful empty
    result = scan_session("nonexistent-session", "2026-04-17T13:00:00Z", project_root=tmp_path)
    assert result == _EMPTY


# ---------------------------------------------------------------------------
# scan_session — real JSONL parsing
# ---------------------------------------------------------------------------

def _write_jsonl(path: Path, entries: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")


@pytest.fixture()
def fake_claude_home(tmp_path, monkeypatch):
    """Patch Path.home() to return tmp_path so scanner finds our fake JSONL."""
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    return tmp_path


def _assistant_line(ts: str, input_tok: int, output_tok: int,
                    cache_read: int = 0, cache_creation: int = 0,
                    model: str = "claude-sonnet-4-6") -> dict:
    return {
        "type": "assistant",
        "timestamp": ts,
        "message": {
            "model": model,
            "usage": {
                "input_tokens": input_tok,
                "output_tokens": output_tok,
                "cache_read_input_tokens": cache_read,
                "cache_creation_input_tokens": cache_creation,
            },
        },
    }


def test_scan_aggregates_assistant_turns(fake_claude_home, tmp_path):
    session_id = "test-session-abc"
    project_root = tmp_path / "myproject"
    project_root.mkdir()

    slug = _project_slug(project_root)
    jsonl_path = fake_claude_home / ".claude" / "projects" / slug / f"{session_id}.jsonl"

    entries = [
        _assistant_line("2026-04-17T13:01:00Z", input_tok=100, output_tok=20, cache_read=500),
        _assistant_line("2026-04-17T13:02:00Z", input_tok=200, output_tok=30, cache_creation=1000),
    ]
    _write_jsonl(jsonl_path, entries)

    result = scan_session(
        session_id,
        "2026-04-17T13:00:00Z",
        project_root=project_root,
    )

    assert result.input_tokens == 300
    assert result.output_tokens == 50
    assert result.cache_read_tokens == 500
    assert result.cache_creation_tokens == 1000
    assert result.turns_scanned == 2
    assert result.model_id == "claude-sonnet-4-6"
    assert result.total_tokens == 300 + 500 + 50


def test_scan_respects_started_at_lower_bound(fake_claude_home, tmp_path):
    session_id = "test-session-bound"
    project_root = tmp_path / "proj"
    project_root.mkdir()
    slug = _project_slug(project_root)
    jsonl_path = fake_claude_home / ".claude" / "projects" / slug / f"{session_id}.jsonl"

    entries = [
        _assistant_line("2026-04-17T12:59:00Z", input_tok=999, output_tok=1),  # before window
        _assistant_line("2026-04-17T13:00:00Z", input_tok=100, output_tok=10),  # in window
    ]
    _write_jsonl(jsonl_path, entries)

    result = scan_session(session_id, "2026-04-17T13:00:00Z", project_root=project_root)

    assert result.input_tokens == 100
    assert result.turns_scanned == 1


def test_scan_respects_ended_at_upper_bound(fake_claude_home, tmp_path):
    session_id = "test-session-end"
    project_root = tmp_path / "proj2"
    project_root.mkdir()
    slug = _project_slug(project_root)
    jsonl_path = fake_claude_home / ".claude" / "projects" / slug / f"{session_id}.jsonl"

    entries = [
        _assistant_line("2026-04-17T13:01:00Z", input_tok=100, output_tok=10),  # in window
        _assistant_line("2026-04-17T13:03:00Z", input_tok=999, output_tok=1),   # after window
    ]
    _write_jsonl(jsonl_path, entries)

    result = scan_session(
        session_id,
        "2026-04-17T13:00:00Z",
        step_ended_at="2026-04-17T13:02:00Z",
        project_root=project_root,
    )

    assert result.input_tokens == 100
    assert result.turns_scanned == 1


def test_scan_ignores_non_assistant_lines(fake_claude_home, tmp_path):
    session_id = "test-session-nonasst"
    project_root = tmp_path / "proj3"
    project_root.mkdir()
    slug = _project_slug(project_root)
    jsonl_path = fake_claude_home / ".claude" / "projects" / slug / f"{session_id}.jsonl"

    entries = [
        {"type": "human", "timestamp": "2026-04-17T13:01:00Z", "message": {"usage": {"input_tokens": 9999}}},
        _assistant_line("2026-04-17T13:01:30Z", input_tok=50, output_tok=5),
    ]
    _write_jsonl(jsonl_path, entries)

    result = scan_session(session_id, "2026-04-17T13:00:00Z", project_root=project_root)
    assert result.input_tokens == 50
    assert result.turns_scanned == 1


def test_scan_skips_malformed_lines(fake_claude_home, tmp_path):
    session_id = "test-session-malformed"
    project_root = tmp_path / "proj4"
    project_root.mkdir()
    slug = _project_slug(project_root)
    jsonl_path = fake_claude_home / ".claude" / "projects" / slug / f"{session_id}.jsonl"

    jsonl_path.parent.mkdir(parents=True, exist_ok=True)
    with jsonl_path.open("w") as f:
        f.write("not json\n")
        f.write(json.dumps(_assistant_line("2026-04-17T13:01:00Z", 80, 8)) + "\n")
        f.write("{broken\n")

    result = scan_session(session_id, "2026-04-17T13:00:00Z", project_root=project_root)
    assert result.turns_scanned == 1
    assert result.input_tokens == 80


def test_scan_picks_most_frequent_model(fake_claude_home, tmp_path):
    session_id = "test-session-model"
    project_root = tmp_path / "proj5"
    project_root.mkdir()
    slug = _project_slug(project_root)
    jsonl_path = fake_claude_home / ".claude" / "projects" / slug / f"{session_id}.jsonl"

    entries = [
        _assistant_line("2026-04-17T13:01:00Z", 10, 1, model="claude-opus-4-7"),
        _assistant_line("2026-04-17T13:02:00Z", 10, 1, model="claude-sonnet-4-6"),
        _assistant_line("2026-04-17T13:03:00Z", 10, 1, model="claude-sonnet-4-6"),
    ]
    _write_jsonl(jsonl_path, entries)

    result = scan_session(session_id, "2026-04-17T13:00:00Z", project_root=project_root)
    assert result.model_id == "claude-sonnet-4-6"


def test_scan_fallback_finds_file_without_project_root(fake_claude_home, tmp_path):
    """When project_root slug doesn't match, scanner falls back to all subdirs."""
    session_id = "test-session-fallback"
    # Put the JSONL in an arbitrary subdirectory that doesn't match any slug
    jsonl_dir = fake_claude_home / ".claude" / "projects" / "some-other-slug"
    jsonl_path = jsonl_dir / f"{session_id}.jsonl"
    _write_jsonl(jsonl_path, [_assistant_line("2026-04-17T13:01:00Z", 42, 7)])

    result = scan_session(session_id, "2026-04-17T13:00:00Z", project_root=None)
    assert result.input_tokens == 42


# ---------------------------------------------------------------------------
# Integration: executor wires real tokens into StepResult
# ---------------------------------------------------------------------------

def test_executor_populates_real_tokens_when_session_data_available(
    fake_claude_home, tmp_path
):
    """record_step_result should populate token fields via the JSONL scanner."""
    from agent_baton.core.engine.executor import ExecutionEngine
    from tests.test_learning_regressions import _plan  # reuse helper

    session_id = "exec-integration-session"
    project_root = tmp_path

    slug = _project_slug(project_root)
    jsonl_path = fake_claude_home / ".claude" / "projects" / slug / f"{session_id}.jsonl"
    _write_jsonl(jsonl_path, [
        _assistant_line("2026-04-17T13:01:00Z", input_tok=1234, output_tok=56, cache_read=78901),
    ])

    engine = ExecutionEngine(team_context_root=tmp_path)
    plan = _plan(task_id="task-real-tokens")
    action = engine.start(plan)

    engine.record_step_result(
        step_id=action.step_id,
        agent_name=action.agent_name,
        status="complete",
        outcome="Done.",
        session_id=session_id,
        step_started_at="2026-04-17T13:00:00Z",
    )

    state = engine._require_execution("test")
    sr = next(r for r in state.step_results if r.step_id == action.step_id)

    assert sr.input_tokens == 1234
    assert sr.output_tokens == 56
    assert sr.cache_read_tokens == 78901
    assert sr.session_id == session_id
    assert sr.model_id == "claude-sonnet-4-6"
    # estimated_tokens should be overridden to real total
    assert sr.estimated_tokens == 1234 + 78901 + 56
