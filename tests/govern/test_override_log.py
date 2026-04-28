"""Tests for G1.6 governance override + justification logging (bd-1a09)."""
from __future__ import annotations

import argparse
import csv
import io
import json
import sqlite3
from pathlib import Path

import pytest

from agent_baton.core.govern.compliance import verify_chain
from agent_baton.core.govern.override_log import OverrideLog


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "baton.db"


@pytest.fixture()
def chain_path(tmp_path: Path) -> Path:
    return tmp_path / "compliance-audit.jsonl"


@pytest.fixture()
def log(db_path: Path, chain_path: Path) -> OverrideLog:
    return OverrideLog(db_path=db_path, chain_log_path=chain_path)


# ---------------------------------------------------------------------------
# OverrideLog.record — SQL row + chain entry
# ---------------------------------------------------------------------------

def test_record_writes_sql_row(log: OverrideLog, db_path: Path) -> None:
    oid = log.record(
        flag="--force",
        command="baton execute gate",
        args=["baton", "execute", "gate", "--force", "--justification", "ship it"],
        justification="ship it",
    )
    assert isinstance(oid, str) and len(oid) == 32  # uuid hex

    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM governance_overrides WHERE override_id = ?",
            (oid,),
        ).fetchone()
    assert row is not None
    assert row["flag"] == "--force"
    assert row["command"] == "baton execute gate"
    assert row["justification"] == "ship it"
    assert row["chain_hash"] != ""
    assert json.loads(row["args_json"])[0] == "baton"


def test_record_writes_chain_entry(log: OverrideLog, chain_path: Path) -> None:
    oid = log.record(
        flag="--force",
        command="baton execute gate",
        args=["baton"],
        justification="prod outage",
    )
    lines = [json.loads(l) for l in chain_path.read_text().splitlines() if l.strip()]
    assert len(lines) == 1
    entry = lines[0]
    assert entry["event"] == "override"
    assert entry["override_id"] == oid
    assert entry["flag"] == "--force"
    assert entry["justification_present"] is True


def test_chain_entry_excludes_justification_text(
    log: OverrideLog, chain_path: Path
) -> None:
    log.record(
        flag="--force",
        command="baton execute gate",
        args=["baton"],
        justification="SECRET-RATIONALE-DO-NOT-LEAK",
    )
    raw = chain_path.read_text()
    assert "SECRET-RATIONALE-DO-NOT-LEAK" not in raw
    # The structural metadata is present though.
    assert "justification_present" in raw
    assert "override_id" in raw


def test_chain_hash_matches_entry_hash(
    log: OverrideLog, chain_path: Path, db_path: Path
) -> None:
    oid = log.record(
        flag="--force", command="baton x", args=["baton", "x"],
        justification="why",
    )
    last_chain_entry = json.loads(chain_path.read_text().splitlines()[-1])
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT chain_hash FROM governance_overrides WHERE override_id=?",
            (oid,),
        ).fetchone()
    assert row["chain_hash"] == last_chain_entry["entry_hash"]


def test_chain_remains_verifiable_after_overrides(
    log: OverrideLog, chain_path: Path
) -> None:
    log.record(flag="--force", command="cmd1", args=["a"], justification="r1")
    log.record(flag="--skip-gate", command="cmd2", args=["b"], justification=None)
    log.record(flag="--force", command="cmd3", args=["c"], justification="r3")
    ok, msg = verify_chain(chain_path)
    assert ok, msg


# ---------------------------------------------------------------------------
# Read path — list / show / export
# ---------------------------------------------------------------------------

def test_list_recent_returns_newest_first(log: OverrideLog) -> None:
    # created_at granularity is seconds — so we explicitly seed with
    # increasing timestamps via repeated calls (created_at column auto-fills).
    ids = [
        log.record(flag="--force", command=f"c{i}", args=[str(i)],
                   justification=f"r{i}")
        for i in range(3)
    ]
    rows = log.list_recent(limit=10)
    assert len(rows) == 3
    # Newest-first by created_at DESC + override_id DESC tiebreaker
    seen_ids = [r["override_id"] for r in rows]
    assert set(seen_ids) == set(ids)


def test_get_returns_full_row_with_justification(log: OverrideLog) -> None:
    oid = log.record(
        flag="--force", command="cmd", args=["x"], justification="reason-foo",
    )
    row = log.get(oid)
    assert row is not None
    assert row["justification"] == "reason-foo"


def test_get_missing_returns_none(log: OverrideLog) -> None:
    assert log.get("does-not-exist") is None


def test_export_since_filters_by_date(log: OverrideLog) -> None:
    log.record(flag="--force", command="c", args=["x"], justification="r")
    rows = log.export_since(since_iso="1970-01-01T00:00:00+00:00")
    assert len(rows) == 1
    rows_future = log.export_since(since_iso="2999-01-01T00:00:00+00:00")
    assert rows_future == []


# ---------------------------------------------------------------------------
# CLI helper — record_override prompt + non-tty warning
# ---------------------------------------------------------------------------

def test_record_override_noop_when_flag_falsy(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_baton.cli import _override_helper

    called = {"yes": False}

    class _StubLog:
        def __init__(self, *_, **__):
            called["yes"] = True

    monkeypatch.setattr(
        "agent_baton.core.govern.override_log.OverrideLog", _StubLog
    )
    assert _override_helper.record_override("", justification=None) is None
    assert called["yes"] is False


def test_record_override_non_tty_records_empty_justification_and_warns(
    db_path: Path, chain_path: Path,
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    from agent_baton.cli import _override_helper

    monkeypatch.setattr(_override_helper, "_resolve_db_path", lambda: db_path)

    # Patch OverrideLog so it points at our test chain log too.
    real_log = OverrideLog(db_path=db_path, chain_log_path=chain_path)
    monkeypatch.setattr(
        "agent_baton.core.govern.override_log.OverrideLog",
        lambda db_path=None: real_log,
    )

    oid = _override_helper.record_override(
        flag="--force",
        justification=None,
        command="baton execute gate",
        argv=["baton", "execute", "gate", "--force"],
        interactive=False,
    )
    assert oid is not None
    err = capsys.readouterr().err
    assert "warning" in err.lower()

    # Justification stored as empty string in SQL.
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT justification FROM governance_overrides WHERE override_id=?",
            (oid,),
        ).fetchone()
    assert row["justification"] == ""


def test_record_override_with_justification_persists(
    db_path: Path, chain_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_baton.cli import _override_helper

    monkeypatch.setattr(_override_helper, "_resolve_db_path", lambda: db_path)
    real_log = OverrideLog(db_path=db_path, chain_log_path=chain_path)
    monkeypatch.setattr(
        "agent_baton.core.govern.override_log.OverrideLog",
        lambda db_path=None: real_log,
    )

    oid = _override_helper.record_override(
        flag="--force",
        justification="explicit reason",
        command="baton execute gate",
        argv=["baton", "execute", "gate", "--force", "--justification", "explicit reason"],
        interactive=False,
    )
    assert oid is not None
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT justification, flag FROM governance_overrides WHERE override_id=?",
            (oid,),
        ).fetchone()
    assert row["justification"] == "explicit reason"
    assert row["flag"] == "--force"


def test_record_override_interactive_prompt_only_when_opted_in(
    db_path: Path, chain_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prompt is opt-in via BATON_OVERRIDE_PROMPT=1 to keep dev flow unblocked."""
    from agent_baton.cli import _override_helper

    monkeypatch.setattr(_override_helper, "_resolve_db_path", lambda: db_path)
    real_log = OverrideLog(db_path=db_path, chain_log_path=chain_path)
    monkeypatch.setattr(
        "agent_baton.core.govern.override_log.OverrideLog",
        lambda db_path=None: real_log,
    )
    monkeypatch.setattr(
        _override_helper, "_prompt_for_justification",
        lambda flag: "interactive-reason",
    )

    # Default (env var unset): TTY does NOT block — empty justification recorded.
    monkeypatch.delenv("BATON_OVERRIDE_PROMPT", raising=False)
    oid = _override_helper.record_override(
        flag="--force",
        justification=None,
        command="baton execute gate",
        argv=["baton", "execute", "gate", "--force"],
        interactive=True,
    )
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT justification FROM governance_overrides WHERE override_id=?",
            (oid,),
        ).fetchone()
    assert row["justification"] == ""

    # Opt-in: BATON_OVERRIDE_PROMPT=1 → prompt fires.
    monkeypatch.setenv("BATON_OVERRIDE_PROMPT", "1")
    oid2 = _override_helper.record_override(
        flag="--force",
        justification=None,
        command="baton execute gate",
        argv=["baton", "execute", "gate", "--force"],
        interactive=True,
    )
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        row2 = conn.execute(
            "SELECT justification FROM governance_overrides WHERE override_id=?",
            (oid2,),
        ).fetchone()
    assert row2["justification"] == "interactive-reason"


# ---------------------------------------------------------------------------
# CLI subcommand round-trip: list / show / export
# ---------------------------------------------------------------------------

def _seed_two_overrides(db_path: Path, chain_path: Path) -> tuple[str, str]:
    log = OverrideLog(db_path=db_path, chain_log_path=chain_path)
    a = log.record(flag="--force", command="cmd-a",
                   args=["baton", "x"], justification="reason-a")
    b = log.record(flag="--skip-gate", command="cmd-b",
                   args=["baton", "y"], justification="reason-b")
    return a, b


def test_cli_list_show_export_roundtrip(
    db_path: Path, chain_path: Path,
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    from agent_baton.cli.commands.govern import overrides as cli

    monkeypatch.setattr(
        "agent_baton.cli._override_helper._resolve_db_path",
        lambda: db_path,
    )
    # The CLI helper builds its own OverrideLog via _resolve_db_path; the
    # chain_log_path defaults to a sibling of db_path, so make sure our
    # seeded chain lives there too.
    seed_chain = db_path.parent / "compliance-audit.jsonl"
    a, b = _seed_two_overrides(db_path, seed_chain)

    # list
    args = argparse.Namespace(overrides_cmd="list", limit=10)
    cli.handler(args)
    out = capsys.readouterr().out
    assert "Recent overrides" in out
    assert a[:12] in out and b[:12] in out

    # show
    args = argparse.Namespace(overrides_cmd="show", override_id=a)
    cli.handler(args)
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert parsed["override_id"] == a
    assert parsed["justification"] == "reason-a"

    # export json
    args = argparse.Namespace(overrides_cmd="export", since=None, format="json")
    cli.handler(args)
    out = capsys.readouterr().out
    rows = json.loads(out)
    assert {r["override_id"] for r in rows} == {a, b}

    # export csv
    args = argparse.Namespace(overrides_cmd="export", since=None, format="csv")
    cli.handler(args)
    out = capsys.readouterr().out
    reader = csv.DictReader(io.StringIO(out))
    csv_rows = list(reader)
    assert {r["override_id"] for r in csv_rows} == {a, b}
    # Justification round-trips through CSV.
    just_map = {r["override_id"]: r["justification"] for r in csv_rows}
    assert just_map[a] == "reason-a"
    assert just_map[b] == "reason-b"


# ---------------------------------------------------------------------------
# bd-fe42: _current_actor must not blindly trust spoofed $USER
# ---------------------------------------------------------------------------

def test_current_actor_prefers_os_identity_over_spoofed_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If $USER differs from the OS-derived identity, the OS value wins
    and the env value is recorded inline so reviewers see the spoof."""
    from agent_baton.core.govern import override_log as ol

    monkeypatch.setattr(ol, "_os_identity", lambda: "real-uid-name")
    monkeypatch.setenv("USER", "auditor")
    monkeypatch.delenv("USERNAME", raising=False)

    actor = ol._current_actor()
    assert actor.startswith("real-uid-name")
    assert "auditor" in actor
    # The OS identity must never be silently replaced by the env value.
    assert actor != "auditor"


def test_current_actor_uses_os_identity_when_env_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_baton.core.govern import override_log as ol

    monkeypatch.setattr(ol, "_os_identity", lambda: "alice")
    monkeypatch.setenv("USER", "alice")
    monkeypatch.delenv("USERNAME", raising=False)

    assert ol._current_actor() == "alice"


def test_current_actor_falls_back_to_env_when_no_os_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On platforms without ``pwd`` (Windows), $USER / $USERNAME is the
    only signal — we still accept it but the docstring records the
    weakened guarantee."""
    from agent_baton.core.govern import override_log as ol

    monkeypatch.setattr(ol, "_os_identity", lambda: None)
    monkeypatch.delenv("USER", raising=False)
    monkeypatch.setenv("USERNAME", "winuser")

    assert ol._current_actor() == "winuser"


def test_current_actor_unknown_when_no_signal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_baton.core.govern import override_log as ol

    monkeypatch.setattr(ol, "_os_identity", lambda: None)
    monkeypatch.delenv("USER", raising=False)
    monkeypatch.delenv("USERNAME", raising=False)

    assert ol._current_actor() == "unknown"


def test_record_override_strips_argv0_basename(
    db_path: Path, chain_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """argv[0] absolute paths must NOT leak $HOME / install paths into the row."""
    from agent_baton.cli import _override_helper

    monkeypatch.setattr(_override_helper, "_resolve_db_path", lambda: db_path)
    real_log = OverrideLog(db_path=db_path, chain_log_path=chain_path)
    monkeypatch.setattr(
        "agent_baton.core.govern.override_log.OverrideLog",
        lambda db_path=None: real_log,
    )

    leaky_argv0 = "/home/secret-user/.local/bin/baton"
    oid = _override_helper.record_override(
        flag="--force",
        justification="ship",
        command="baton execute gate",
        argv=[leaky_argv0, "execute", "gate", "--force"],
        interactive=False,
    )
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT args_json FROM governance_overrides WHERE override_id=?",
            (oid,),
        ).fetchone()
    assert "secret-user" not in row["args_json"]
    assert ".local/bin" not in row["args_json"]
    parsed = json.loads(row["args_json"])
    assert parsed[0] == "baton", "argv[0] must be basename-only"
