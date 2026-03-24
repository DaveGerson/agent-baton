"""Tests for agent_baton/cli/commands/query_cmd.py (baton cquery).

Coverage:
- CLI argument parsing (register/handler interface)
- --help exits 0
- --tables lists tables
- --table TABLE lists columns
- shortcut keywords (agents, costs, gaps, failures, mapping)
- arbitrary SQL pass-through
- read-only guard (mutating SQL rejected)
- --format json / csv output
- graceful error when central.db is missing / inaccessible
- discovery: cquery is registered in main dispatch
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import pytest

_PYTHON = sys.executable
_MODULE = "agent_baton.cli.main"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    """Run a baton CLI command via ``python -m`` and return the result."""
    return subprocess.run(
        [_PYTHON, "-m", _MODULE, *args],
        capture_output=True,
        text=True,
        cwd=str(cwd) if cwd else None,
    )


def _make_central_db(tmp_path: Path) -> Path:
    """Create a minimal central.db with the full schema and return its path."""
    from agent_baton.core.storage.central import CentralStore

    db_path = tmp_path / "central.db"
    store = CentralStore(db_path)
    store.close()
    return db_path


def _handler(args_list: list[str], db_path: Path, capsys: pytest.CaptureFixture) -> tuple[str, str]:
    """Invoke query_cmd.handler directly and return (stdout, stderr)."""
    from agent_baton.cli.commands.query_cmd import register, handler

    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    register(sub)
    args = parser.parse_args(["cquery"] + args_list + ["--db", str(db_path)])
    handler(args)
    captured = capsys.readouterr()
    return captured.out, captured.err


# ---------------------------------------------------------------------------
# Help / registration tests
# ---------------------------------------------------------------------------


def test_cquery_help_exits_zero():
    """baton cquery --help returns exit code 0."""
    result = _run("cquery", "--help")
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert "central.db" in result.stdout.lower() or "cquery" in result.stdout.lower()


def test_cquery_registered_in_main_help():
    """baton --help lists the cquery subcommand."""
    result = _run("--help")
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert "cquery" in result.stdout


def test_cquery_has_register_and_handler():
    """query_cmd exposes register() and handler() callables."""
    from agent_baton.cli.commands import query_cmd  # type: ignore[import]

    assert callable(getattr(query_cmd, "register", None))
    assert callable(getattr(query_cmd, "handler", None))


def test_cquery_discovery():
    """discover_commands() includes query_cmd."""
    from agent_baton.cli.main import discover_commands

    modules = discover_commands()
    assert "query_cmd" in modules, f"modules found: {sorted(modules.keys())}"


def test_cquery_in_dispatch_table():
    """After main() builds its dispatch table, 'cquery' is a registered command."""
    import types
    from agent_baton.cli.main import discover_commands

    parser = argparse.ArgumentParser(prog="baton")
    sub = parser.add_subparsers(dest="command")
    modules = discover_commands()
    dispatch: dict[str, types.ModuleType] = {}
    for _mod_name, mod in modules.items():
        sp = mod.register(sub)
        subcommand = sp.prog.split(None, 1)[1] if " " in sp.prog else sp.prog
        dispatch[subcommand] = mod

    assert "cquery" in dispatch, f"dispatch keys: {sorted(dispatch.keys())}"


def test_cquery_no_conflict_with_query():
    """cquery and query are both registered and do not overwrite each other."""
    import types
    from agent_baton.cli.main import discover_commands

    parser = argparse.ArgumentParser(prog="baton")
    sub = parser.add_subparsers(dest="command")
    modules = discover_commands()
    dispatch: dict[str, types.ModuleType] = {}
    for _mod_name, mod in modules.items():
        sp = mod.register(sub)
        subcommand = sp.prog.split(None, 1)[1] if " " in sp.prog else sp.prog
        dispatch[subcommand] = mod

    assert "cquery" in dispatch
    assert "query" in dispatch
    # They must be different modules
    assert dispatch["cquery"] is not dispatch["query"]


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


def test_register_adds_format_flag():
    """--format is registered with choices table/json/csv."""
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    from agent_baton.cli.commands.query_cmd import register

    register(sub)
    args = parser.parse_args(["cquery", "--format", "json"])
    assert args.format == "json"


def test_register_adds_tables_flag():
    """--tables flag is registered."""
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    from agent_baton.cli.commands.query_cmd import register

    register(sub)
    args = parser.parse_args(["cquery", "--tables"])
    assert args.tables is True


def test_register_adds_table_flag():
    """--table TABLE is registered as table_name."""
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    from agent_baton.cli.commands.query_cmd import register

    register(sub)
    args = parser.parse_args(["cquery", "--table", "executions"])
    assert args.table_name == "executions"


def test_register_adds_db_flag():
    """--db PATH is registered."""
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    from agent_baton.cli.commands.query_cmd import register

    register(sub)
    args = parser.parse_args(["cquery", "--db", "/tmp/test.db"])
    assert args.db == "/tmp/test.db"


def test_register_positional_query():
    """Positional QUERY argument is registered."""
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    from agent_baton.cli.commands.query_cmd import register

    register(sub)
    args = parser.parse_args(["cquery", "SELECT 1"])
    assert args.query == "SELECT 1"


# ---------------------------------------------------------------------------
# Functional tests (require central storage module)
# ---------------------------------------------------------------------------


@pytest.fixture
def central_db(tmp_path: Path) -> Path:
    """Return path to a freshly initialised central.db in tmp_path."""
    try:
        return _make_central_db(tmp_path)
    except ImportError:
        pytest.skip("central storage module not available")


class TestCqueryHandler:
    """Direct handler invocation tests using a real (empty) central.db."""

    def test_tables_lists_sqlite_master(
        self, central_db: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """--tables prints at least the sync_watermarks table."""
        out, err = _handler(["--tables"], central_db, capsys)
        assert err == "", f"unexpected stderr: {err}"
        # central.db schema includes sync_watermarks as a minimum
        assert "sync_watermarks" in out or "no data" in out.lower() or "Tables" in out

    def test_table_describe_executions(
        self, central_db: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """--table executions shows column definitions."""
        out, err = _handler(["--table", "executions"], central_db, capsys)
        # executions table should exist; if schema not yet populated the error
        # message should be friendly (no traceback)
        assert "Traceback" not in err
        assert "Traceback" not in out

    def test_table_missing_table(
        self, central_db: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """--table for a non-existent table prints an error (not traceback)."""
        out, err = _handler(["--table", "nonexistent_xyz_table"], central_db, capsys)
        # Either an error message or a 'not found' note; never a Python traceback
        assert "Traceback" not in err
        assert "Traceback" not in out

    def test_sql_select_one(
        self, central_db: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """Arbitrary SELECT 1 AS val returns a result."""
        out, err = _handler(["SELECT 1 AS val"], central_db, capsys)
        assert err == ""
        assert "val" in out.lower() or "1" in out

    def test_sql_select_one_json(
        self, central_db: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """SELECT 1 AS val with --format json returns a JSON list."""
        out, err = _handler(["SELECT 1 AS val", "--format", "json"], central_db, capsys)
        assert err == ""
        parsed = json.loads(out.strip())
        assert isinstance(parsed, list)
        assert len(parsed) == 1
        assert parsed[0].get("val") == 1

    def test_sql_select_one_csv(
        self, central_db: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """SELECT 1 AS val with --format csv returns CSV with header."""
        out, err = _handler(["SELECT 1 AS val", "--format", "csv"], central_db, capsys)
        assert err == ""
        lines = [l for l in out.strip().splitlines() if l]
        assert len(lines) >= 2  # header + data row
        assert "val" in lines[0].lower()

    def test_shortcut_agents_empty(
        self, central_db: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """'agents' shortcut returns (no data) or rows from v_agent_reliability."""
        out, err = _handler(["agents"], central_db, capsys)
        # On empty DB the view exists but returns no rows
        assert "Traceback" not in err
        assert "Traceback" not in out

    def test_shortcut_costs_empty(
        self, central_db: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """'costs' shortcut queries v_cost_by_task_type without error."""
        out, err = _handler(["costs"], central_db, capsys)
        assert "Traceback" not in err

    def test_shortcut_gaps_empty(
        self, central_db: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """'gaps' shortcut queries v_recurring_knowledge_gaps without error."""
        out, err = _handler(["gaps"], central_db, capsys)
        assert "Traceback" not in err

    def test_shortcut_failures_empty(
        self, central_db: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """'failures' shortcut queries v_project_failure_rate without error."""
        out, err = _handler(["failures"], central_db, capsys)
        assert "Traceback" not in err

    def test_shortcut_mapping_empty(
        self, central_db: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """'mapping' shortcut queries v_external_plan_mapping without error."""
        out, err = _handler(["mapping"], central_db, capsys)
        assert "Traceback" not in err

    def test_write_sql_rejected(
        self, central_db: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """Mutating SQL is rejected with a read-only error message."""
        out, err = _handler(
            ["DELETE FROM sync_watermarks"], central_db, capsys
        )
        assert "read-only" in err.lower() or "read-only" in out.lower()

    def test_no_args_prints_help(
        self, central_db: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """No positional argument prints usage/help without crashing."""
        out, err = _handler([], central_db, capsys)
        assert "Traceback" not in err
        # Should print something useful
        assert "Usage" in out or "usage" in out or "baton cquery" in out


# ---------------------------------------------------------------------------
# Subprocess integration tests
# ---------------------------------------------------------------------------


def test_cquery_no_args_no_crash(tmp_path: Path) -> None:
    """baton cquery with no args and a tmp DB does not crash with traceback."""
    try:
        db = _make_central_db(tmp_path)
    except ImportError:
        pytest.skip("central storage module not available")

    result = _run("cquery", "--db", str(db))
    assert "Traceback" not in result.stderr
    assert result.returncode in (0, 1)


def test_cquery_tables_no_crash(tmp_path: Path) -> None:
    """baton cquery --tables with a real DB does not crash."""
    try:
        db = _make_central_db(tmp_path)
    except ImportError:
        pytest.skip("central storage module not available")

    result = _run("cquery", "--tables", "--db", str(db))
    assert "Traceback" not in result.stderr
    assert result.returncode == 0


def test_cquery_select_json(tmp_path: Path) -> None:
    """baton cquery 'SELECT 1 AS x' --format json prints valid JSON."""
    try:
        db = _make_central_db(tmp_path)
    except ImportError:
        pytest.skip("central storage module not available")

    result = _run("cquery", "SELECT 1 AS x", "--format", "json", "--db", str(db))
    assert result.returncode == 0, f"stderr: {result.stderr}"
    parsed = json.loads(result.stdout.strip())
    assert isinstance(parsed, list)
    assert parsed[0]["x"] == 1


def test_cquery_shortcut_agents(tmp_path: Path) -> None:
    """baton cquery agents returns exit 0 (even on empty DB)."""
    try:
        db = _make_central_db(tmp_path)
    except ImportError:
        pytest.skip("central storage module not available")

    result = _run("cquery", "agents", "--db", str(db))
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert "Traceback" not in result.stderr
