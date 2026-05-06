"""Tests for the BATON_EXPERIMENTAL=swarm gate (bd-18f6).

Covers:
  - Command is blocked (exit 2) when BATON_EXPERIMENTAL is unset or missing 'swarm'
  - Command proceeds when BATON_EXPERIMENTAL=swarm is set
  - PR #59 sign-off gate (handler → _check_enabled → _handle_refactor) is still
    reachable under the experimental flag
  - CSV parsing: BATON_EXPERIMENTAL=swarm,immune correctly enables the flag
"""
from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from agent_baton.cli.commands import swarm_cmd
from agent_baton.cli.commands.swarm_cmd import (
    _EXPERIMENTAL_BLOCKED_MSG,
    _EXPERIMENTAL_WARNING_MSG,
    _check_experimental,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_dispatch_result() -> MagicMock:
    return MagicMock(
        swarm_id="test-swarm-exp",
        n_succeeded=1,
        n_failed=0,
        total_tokens=8000,
        total_cost_usd=0.002,
        wall_clock_sec=0.5,
        coalesce_branch="swarm-coalesce-test",
        approval_bead_id="",
        failed_chunks=[],
    )


# ---------------------------------------------------------------------------
# 1. test_swarm_blocked_when_experimental_flag_unset
# ---------------------------------------------------------------------------


def test_swarm_blocked_when_experimental_flag_unset(capsys: pytest.CaptureFixture) -> None:
    """Without BATON_EXPERIMENTAL=swarm the command exits with code 2."""
    with patch.dict("os.environ", {}, clear=False):
        # Remove BATON_EXPERIMENTAL entirely if present
        env_patch = {"BATON_EXPERIMENTAL": ""}
        with patch.dict("os.environ", env_patch):
            with pytest.raises(SystemExit) as exc_info:
                _check_experimental()

    assert exc_info.value.code == 2
    captured = capsys.readouterr()
    assert "experimental" in captured.err
    assert "BATON_EXPERIMENTAL=swarm" in captured.err
    assert "bd-c925" in captured.err
    assert "bd-2b9f" in captured.err


def test_swarm_blocked_when_experimental_flag_has_other_values(
    capsys: pytest.CaptureFixture,
) -> None:
    """BATON_EXPERIMENTAL set to other flags (not 'swarm') still blocks."""
    with patch.dict("os.environ", {"BATON_EXPERIMENTAL": "immune,other"}):
        with pytest.raises(SystemExit) as exc_info:
            _check_experimental()

    assert exc_info.value.code == 2


def test_swarm_blocked_message_goes_to_stderr(capsys: pytest.CaptureFixture) -> None:
    """Error message must go to stderr, not stdout."""
    with patch.dict("os.environ", {"BATON_EXPERIMENTAL": ""}):
        with pytest.raises(SystemExit):
            _check_experimental()

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "experimental" in captured.err


# ---------------------------------------------------------------------------
# 2. test_swarm_runs_when_experimental_flag_set
# ---------------------------------------------------------------------------


def test_swarm_check_experimental_passes_when_flag_set(
    capsys: pytest.CaptureFixture,
) -> None:
    """BATON_EXPERIMENTAL=swarm allows _check_experimental to return normally."""
    with patch.dict("os.environ", {"BATON_EXPERIMENTAL": "swarm"}):
        # Must NOT raise
        _check_experimental()

    captured = capsys.readouterr()
    # Warning must be printed to stderr
    assert "[EXPERIMENTAL]" in captured.err
    assert "v1 stub" in captured.err
    assert "bd-c925" in captured.err


def test_swarm_handler_proceeds_when_experimental_set(
    capsys: pytest.CaptureFixture,
) -> None:
    """handler() passes the experimental gate and calls _handle_refactor."""
    mock_result = _make_dispatch_result()

    args = SimpleNamespace(
        swarm_command="refactor",
        directive_json='{"kind":"rename-symbol","old":"a.Foo","new":"a.Bar"}',
        max_agents=100,
        language="python",
        model="claude-haiku",
        codebase_root=None,
        dry_run=False,
        yes=True,
        require_approval_bead=None,
    )

    from agent_baton.core.swarm.partitioner import RenameSymbol, _stable_chunk_id, CodeChunk, CallSite, ProofRef, ScopeKind
    from pathlib import Path

    chunk = CodeChunk(
        chunk_id=_stable_chunk_id([Path("a.py")]),
        files=[Path("a.py")],
        call_sites=[
            CallSite(file=Path("a.py"), line=1, column=1,
                     qualified_name="a.Foo", scope_kind=ScopeKind.MODULE)
        ],
        scope=ScopeKind.MODULE,
        estimated_tokens=4000,
        independence_proof=ProofRef(kind="disjoint-files", details="test"),
    )

    with (
        patch.dict("os.environ", {"BATON_EXPERIMENTAL": "swarm", "BATON_SWARM_ENABLED": "1"}),
        patch.object(swarm_cmd, "_partition", return_value=[chunk]),
        patch.object(swarm_cmd, "_run_with_engine", return_value=mock_result),
        patch.object(swarm_cmd, "_file_approval_bead", return_value=""),
    ):
        swarm_cmd.handler(args)

    captured = capsys.readouterr()
    # Experimental warning on stderr
    assert "[EXPERIMENTAL]" in captured.err
    # Dispatch result on stdout
    assert "Swarm complete" in captured.out


# ---------------------------------------------------------------------------
# 3. test_swarm_signoff_gate_runs_under_experimental_flag
# ---------------------------------------------------------------------------


def test_swarm_signoff_gate_runs_under_experimental_flag(
    capsys: pytest.CaptureFixture,
) -> None:
    """The PR #59 sign-off gate (_check_approval_bead) is still invoked under the
    experimental flag.  This verifies the gate is wired correctly and will be
    exercised when the feature graduates."""
    from agent_baton.core.swarm.partitioner import RenameSymbol, _stable_chunk_id, CodeChunk, CallSite, ProofRef, ScopeKind
    from pathlib import Path
    from agent_baton.cli.commands.swarm_cmd import _SENTINEL_REQUIRE_APPROVAL

    chunk = CodeChunk(
        chunk_id=_stable_chunk_id([Path("b.py")]),
        files=[Path("b.py")],
        call_sites=[
            CallSite(file=Path("b.py"), line=2, column=1,
                     qualified_name="b.Old", scope_kind=ScopeKind.MODULE)
        ],
        scope=ScopeKind.MODULE,
        estimated_tokens=4000,
        independence_proof=ProofRef(kind="disjoint-files", details="test"),
    )

    # Simulate a missing approval bead store so _check_approval_bead exits 1.
    with (
        patch.dict("os.environ", {"BATON_EXPERIMENTAL": "swarm", "BATON_SWARM_ENABLED": "1"}),
        patch.object(swarm_cmd, "_partition", return_value=[chunk]),
        patch.object(swarm_cmd, "_get_bead_store", return_value=None),
        pytest.raises(SystemExit) as exc_info,
    ):
        args = SimpleNamespace(
            swarm_command="refactor",
            directive_json='{"kind":"rename-symbol","old":"b.Old","new":"b.New"}',
            max_agents=100,
            language="python",
            model="claude-haiku",
            codebase_root=None,
            dry_run=False,
            yes=True,
            require_approval_bead=_SENTINEL_REQUIRE_APPROVAL,  # trigger sign-off gate
        )
        swarm_cmd.handler(args)

    # Sign-off gate exits 1 (not 2, which would be the experimental block)
    assert exc_info.value.code == 1

    captured = capsys.readouterr()
    # Experimental warning still appeared before the gate ran
    assert "[EXPERIMENTAL]" in captured.err
    # Sign-off gate error message also present
    assert "require-approval-bead" in captured.err


# ---------------------------------------------------------------------------
# 4. test_experimental_flag_supports_csv
# ---------------------------------------------------------------------------


def test_experimental_flag_supports_csv_swarm_first(
    capsys: pytest.CaptureFixture,
) -> None:
    """BATON_EXPERIMENTAL=swarm,immune correctly enables the swarm flag."""
    with patch.dict("os.environ", {"BATON_EXPERIMENTAL": "swarm,immune"}):
        _check_experimental()  # must not raise

    captured = capsys.readouterr()
    assert "[EXPERIMENTAL]" in captured.err


def test_experimental_flag_supports_csv_swarm_last(
    capsys: pytest.CaptureFixture,
) -> None:
    """BATON_EXPERIMENTAL=immune,swarm (swarm last) also works."""
    with patch.dict("os.environ", {"BATON_EXPERIMENTAL": "immune,swarm"}):
        _check_experimental()  # must not raise

    captured = capsys.readouterr()
    assert "[EXPERIMENTAL]" in captured.err


def test_experimental_flag_supports_csv_with_spaces(
    capsys: pytest.CaptureFixture,
) -> None:
    """BATON_EXPERIMENTAL=swarm, immune (trailing space) is parsed correctly."""
    with patch.dict("os.environ", {"BATON_EXPERIMENTAL": " swarm , immune "}):
        _check_experimental()  # must not raise


def test_experimental_flag_csv_missing_swarm_still_blocks(
    capsys: pytest.CaptureFixture,
) -> None:
    """BATON_EXPERIMENTAL=immune,other does not enable swarm."""
    with patch.dict("os.environ", {"BATON_EXPERIMENTAL": "immune,other"}):
        with pytest.raises(SystemExit) as exc_info:
            _check_experimental()

    assert exc_info.value.code == 2


# ---------------------------------------------------------------------------
# 5. Ordering: experimental check fires BEFORE sign-off gate / BATON_SWARM_ENABLED
# ---------------------------------------------------------------------------


def test_experimental_check_fires_before_swarm_enabled_check(
    capsys: pytest.CaptureFixture,
) -> None:
    """Without BATON_EXPERIMENTAL=swarm the exit code is 2 even when
    BATON_SWARM_ENABLED=1.  This confirms experimental check is first."""
    with patch.dict("os.environ", {
        "BATON_EXPERIMENTAL": "",
        "BATON_SWARM_ENABLED": "1",
    }):
        with pytest.raises(SystemExit) as exc_info:
            # Call handler directly to exercise the full ordering
            args = SimpleNamespace(swarm_command="refactor")
            swarm_cmd.handler(args)

    # Must be 2 (experimental block), not 1 (swarm disabled)
    assert exc_info.value.code == 2
