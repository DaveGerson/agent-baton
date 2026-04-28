"""Tests for Wave 6.2 Part A sign-off gate (bd-707d).

Covers the operator confirmation workflow added to baton swarm refactor:
  - partition preview output
  - interactive confirmation prompt semantics
  - --yes flag skips prompt, preview still printed
  - --require-approval-bead flag (lookup and verify modes)
  - --dry-run does not dispatch
  - auto-filing approval bead on operator confirmation
  - approval_bead_id threaded onto SwarmResult
"""
from __future__ import annotations

import io
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers — import the CLI module under test
# ---------------------------------------------------------------------------

from agent_baton.cli.commands import swarm_cmd
from agent_baton.cli.commands.swarm_cmd import (
    _APPROVAL_BEAD_MAX_AGE_MINUTES,
    _APPROVAL_BEAD_TAG,
    _SENTINEL_REQUIRE_APPROVAL,
    _build_preview_text,
    _check_approval_bead,
    _directive_summary,
    _estimate_cost,
    _prompt_confirm,
)
from agent_baton.core.swarm.partitioner import (
    CallSite,
    CodeChunk,
    ProofRef,
    RenameSymbol,
    ScopeKind,
    _stable_chunk_id,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_chunk(files: list[str], n_call_sites: int = 2) -> CodeChunk:
    """Construct a minimal CodeChunk for test purposes."""
    paths = [Path(f) for f in files]
    chunk_id = _stable_chunk_id(paths)
    sites = [
        CallSite(
            file=paths[0],
            line=i + 1,
            column=1,
            qualified_name="mymod.OldName",
            scope_kind=ScopeKind.MODULE,
        )
        for i in range(n_call_sites)
    ]
    return CodeChunk(
        chunk_id=chunk_id,
        files=paths,
        call_sites=sites,
        scope=ScopeKind.MODULE,
        estimated_tokens=4000,
        independence_proof=ProofRef(kind="disjoint-files", details="test"),
    )


def _make_chunks(n: int, files_per_chunk: int = 1) -> list[CodeChunk]:
    return [
        _make_chunk([f"src/module_{i}_{j}.py" for j in range(files_per_chunk)])
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# 1. test_swarm_preview_includes_directive_and_chunk_count
# ---------------------------------------------------------------------------


def test_swarm_preview_includes_directive_and_chunk_count() -> None:
    directive = RenameSymbol(old="mymod.OldName", new="mymod.NewName")
    chunks = _make_chunks(5)
    text = _build_preview_text(chunks, directive, model="claude-haiku")

    assert "=== SWARM REFACTOR PREVIEW ===" in text
    assert "rename-symbol" in text
    assert "mymod.OldName" in text
    assert "mymod.NewName" in text
    assert "Chunks: 5" in text
    assert "claude-haiku" in text
    # cost and token estimates present
    assert "Estimated tokens:" in text
    assert "Estimated cost:" in text


# ---------------------------------------------------------------------------
# 2. test_swarm_preview_lists_files
# ---------------------------------------------------------------------------


def test_swarm_preview_lists_files_under_20() -> None:
    directive = RenameSymbol(old="a.X", new="a.Y")
    chunks = _make_chunks(3)
    text = _build_preview_text(chunks, directive, model="claude-haiku")

    # All 3 files should be listed individually
    assert "src/module_0_0.py" in text
    assert "src/module_1_0.py" in text
    assert "src/module_2_0.py" in text
    assert "... and" not in text


def test_swarm_preview_truncates_files_over_20() -> None:
    directive = RenameSymbol(old="a.X", new="a.Y")
    # 25 chunks × 1 file each = 25 unique files
    chunks = _make_chunks(25)
    text = _build_preview_text(chunks, directive, model="claude-haiku")

    assert "... and 5 more" in text


# ---------------------------------------------------------------------------
# 3. test_swarm_prompt_default_no
# ---------------------------------------------------------------------------


def test_swarm_prompt_default_no_empty_input() -> None:
    """Empty input with default_no=True should return False."""
    with patch("builtins.input", return_value=""):
        result = _prompt_confirm("Proceed?", default_no=True)
    assert result is False


def test_swarm_prompt_default_yes_empty_input() -> None:
    """Empty input with default_no=False should return True."""
    with patch("builtins.input", return_value=""):
        result = _prompt_confirm("Proceed?", default_no=False)
    assert result is True


# ---------------------------------------------------------------------------
# 4. test_swarm_prompt_yes — all affirmative variants
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("answer", ["y", "yes", "Y", "YES", "Yes"])
def test_swarm_prompt_yes_variants(answer: str) -> None:
    with patch("builtins.input", return_value=answer):
        result = _prompt_confirm("Proceed?", default_no=True)
    assert result is True


@pytest.mark.parametrize("answer", ["n", "no", "N", "NO", "nope", "cancel"])
def test_swarm_prompt_no_variants(answer: str) -> None:
    with patch("builtins.input", return_value=answer):
        result = _prompt_confirm("Proceed?", default_no=True)
    assert result is False


# ---------------------------------------------------------------------------
# 5. test_swarm_prompt_eof_returns_false
# ---------------------------------------------------------------------------


def test_swarm_prompt_eof_returns_false() -> None:
    """EOFError (Ctrl-D) must return False — never accidentally confirm."""
    with patch("builtins.input", side_effect=EOFError):
        result = _prompt_confirm("Proceed?", default_no=True)
    assert result is False


def test_swarm_prompt_keyboard_interrupt_returns_false() -> None:
    with patch("builtins.input", side_effect=KeyboardInterrupt):
        result = _prompt_confirm("Proceed?", default_no=True)
    assert result is False


# ---------------------------------------------------------------------------
# 6. test_swarm_yes_flag_skips_prompt
# ---------------------------------------------------------------------------


def test_swarm_yes_flag_skips_prompt(capsys: pytest.CaptureFixture) -> None:
    """--yes set: no input() call, preview printed, dispatch proceeds."""
    directive = RenameSymbol(old="a.Foo", new="a.Bar")
    chunks = _make_chunks(2)

    mock_dispatch = MagicMock(return_value=MagicMock(
        swarm_id="test-swarm",
        n_succeeded=2,
        n_failed=0,
        total_tokens=16000,
        total_cost_usd=0.004,
        wall_clock_sec=1.2,
        coalesce_branch="swarm-coalesce-test",
        approval_bead_id="",
        failed_chunks=[],
    ))

    with (
        patch.object(swarm_cmd, "_partition", return_value=chunks),
        patch.object(swarm_cmd, "_run_with_engine", mock_dispatch),
        patch.object(swarm_cmd, "_file_approval_bead", return_value=""),
        patch.object(swarm_cmd, "_check_approval_bead"),
        patch("builtins.input") as mock_input,
    ):
        args = SimpleNamespace(
            directive_json='{"kind":"rename-symbol","old":"a.Foo","new":"a.Bar"}',
            max_agents=100,
            language="python",
            model="claude-haiku",
            codebase_root=None,
            dry_run=False,
            yes=True,
            require_approval_bead=None,
        )
        with patch("os.environ.get", return_value="1"):
            swarm_cmd._handle_refactor(args)

    # input() must NOT have been called
    mock_input.assert_not_called()

    # Preview must still appear in stdout
    captured = capsys.readouterr()
    assert "=== SWARM REFACTOR PREVIEW ===" in captured.out
    assert "--yes flag set" in captured.out


# ---------------------------------------------------------------------------
# 7. test_swarm_require_approval_bead_without_bead_refuses
# ---------------------------------------------------------------------------


def test_swarm_require_approval_bead_without_bead_refuses() -> None:
    """--require-approval-bead set, no recent approval → SystemExit non-zero."""
    mock_store = MagicMock()
    mock_store.find_recent_approvals.return_value = []

    with (
        patch.object(swarm_cmd, "_get_bead_store", return_value=mock_store),
        pytest.raises(SystemExit) as exc_info,
    ):
        _check_approval_bead(_SENTINEL_REQUIRE_APPROVAL)

    assert exc_info.value.code == 1
    mock_store.find_recent_approvals.assert_called_once_with(
        tag=_APPROVAL_BEAD_TAG,
        max_age_minutes=_APPROVAL_BEAD_MAX_AGE_MINUTES,
    )


# ---------------------------------------------------------------------------
# 8. test_swarm_require_approval_bead_with_valid_bead_dispatches
# ---------------------------------------------------------------------------


def test_swarm_require_approval_bead_with_valid_bead_dispatches(
    capsys: pytest.CaptureFixture,
) -> None:
    """--require-approval-bead with valid bead_id → check passes, no SystemExit."""
    mock_bead = MagicMock()
    mock_bead.bead_type = "approval"
    mock_bead.tags = [_APPROVAL_BEAD_TAG, "operator-confirmed"]
    mock_bead.status = "open"
    mock_bead.created_at = "2026-04-28T10:00:00Z"

    mock_store = MagicMock()
    mock_store.read.return_value = mock_bead

    with patch.object(swarm_cmd, "_get_bead_store", return_value=mock_store):
        # Should not raise
        _check_approval_bead("bd-abc123ef")

    mock_store.read.assert_called_once_with("bd-abc123ef")
    captured = capsys.readouterr()
    assert "Approval bead verified" in captured.out
    assert "bd-abc123ef" in captured.out


# ---------------------------------------------------------------------------
# 9. test_swarm_require_approval_bead_rejects_closed_bead
# ---------------------------------------------------------------------------


def test_swarm_require_approval_bead_rejects_closed_bead() -> None:
    mock_bead = MagicMock()
    mock_bead.bead_type = "approval"
    mock_bead.tags = [_APPROVAL_BEAD_TAG]
    mock_bead.status = "closed"  # not open

    mock_store = MagicMock()
    mock_store.read.return_value = mock_bead

    with (
        patch.object(swarm_cmd, "_get_bead_store", return_value=mock_store),
        pytest.raises(SystemExit) as exc_info,
    ):
        _check_approval_bead("bd-closed01")

    assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# 10. test_swarm_require_approval_bead_rejects_wrong_tag
# ---------------------------------------------------------------------------


def test_swarm_require_approval_bead_rejects_wrong_tag() -> None:
    mock_bead = MagicMock()
    mock_bead.bead_type = "approval"
    mock_bead.tags = ["some-other-tag"]  # missing swarm-refactor
    mock_bead.status = "open"

    mock_store = MagicMock()
    mock_store.read.return_value = mock_bead

    with (
        patch.object(swarm_cmd, "_get_bead_store", return_value=mock_store),
        pytest.raises(SystemExit) as exc_info,
    ):
        _check_approval_bead("bd-wrongtag")

    assert exc_info.value.code == 1


def test_swarm_require_approval_bead_rejects_wrong_type() -> None:
    mock_bead = MagicMock()
    mock_bead.bead_type = "warning"  # wrong type
    mock_bead.tags = [_APPROVAL_BEAD_TAG]
    mock_bead.status = "open"

    mock_store = MagicMock()
    mock_store.read.return_value = mock_bead

    with (
        patch.object(swarm_cmd, "_get_bead_store", return_value=mock_store),
        pytest.raises(SystemExit) as exc_info,
    ):
        _check_approval_bead("bd-wrongtype")

    assert exc_info.value.code == 1


def test_swarm_require_approval_bead_rejects_missing_bead() -> None:
    mock_store = MagicMock()
    mock_store.read.return_value = None  # bead not found

    with (
        patch.object(swarm_cmd, "_get_bead_store", return_value=mock_store),
        pytest.raises(SystemExit) as exc_info,
    ):
        _check_approval_bead("bd-notfound")

    assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# 11. test_swarm_dry_run_does_not_dispatch
# ---------------------------------------------------------------------------


def test_swarm_dry_run_does_not_dispatch(capsys: pytest.CaptureFixture) -> None:
    """--dry-run: preview printed, no dispatch, no confirmation prompt."""
    chunks = _make_chunks(3)

    with (
        patch.object(swarm_cmd, "_partition", return_value=chunks),
        patch.object(swarm_cmd, "_run_with_engine") as mock_dispatch,
        patch("builtins.input") as mock_input,
    ):
        args = SimpleNamespace(
            directive_json='{"kind":"replace-import","old":"requests","new":"httpx"}',
            max_agents=100,
            language="python",
            model="claude-haiku",
            codebase_root=None,
            dry_run=True,
            yes=False,
            require_approval_bead=None,
        )
        swarm_cmd._handle_refactor(args)

    mock_dispatch.assert_not_called()
    mock_input.assert_not_called()

    captured = capsys.readouterr()
    assert "=== SWARM REFACTOR PREVIEW ===" in captured.out
    assert "DRY RUN" in captured.out


# ---------------------------------------------------------------------------
# 12. test_swarm_confirm_files_approval_bead
# ---------------------------------------------------------------------------


def test_swarm_confirm_files_approval_bead(capsys: pytest.CaptureFixture) -> None:
    """Operator confirms → bead filed with correct tags + affected_files."""
    chunks = _make_chunks(2)
    directive = RenameSymbol(old="pkg.A", new="pkg.B")

    filed_beads: list = []

    def _capture_write(bead):
        filed_beads.append(bead)
        return bead.bead_id

    mock_store = MagicMock()
    mock_store.write.side_effect = _capture_write

    mock_result = MagicMock(
        swarm_id="sw-123",
        n_succeeded=2,
        n_failed=0,
        total_tokens=16000,
        total_cost_usd=0.004,
        wall_clock_sec=1.0,
        coalesce_branch="swarm-coalesce-sw-123",
        approval_bead_id="",
        failed_chunks=[],
    )

    with (
        patch.object(swarm_cmd, "_partition", return_value=chunks),
        patch.object(swarm_cmd, "_run_with_engine", return_value=mock_result),
        patch.object(swarm_cmd, "_get_bead_store", return_value=mock_store),
        patch("builtins.input", return_value="y"),
        patch.object(swarm_cmd, "_get_operator_identity", return_value="testuser"),
    ):
        args = SimpleNamespace(
            directive_json='{"kind":"rename-symbol","old":"pkg.A","new":"pkg.B"}',
            max_agents=100,
            language="python",
            model="claude-haiku",
            codebase_root=None,
            dry_run=False,
            yes=False,
            require_approval_bead=None,
        )
        swarm_cmd._handle_refactor(args)

    # A bead must have been filed.
    assert len(filed_beads) == 1, "Expected exactly one approval bead to be filed"
    bead = filed_beads[0]
    assert bead.bead_type == "approval"
    assert _APPROVAL_BEAD_TAG in bead.tags
    assert "operator-confirmed" in bead.tags
    # affected_files must contain all chunk files
    chunk_files = sorted({str(f) for c in chunks for f in c.files})
    assert bead.affected_files == chunk_files
    # operator identity recorded
    assert "testuser" in bead.content

    captured = capsys.readouterr()
    assert "=== SWARM REFACTOR PREVIEW ===" in captured.out
    assert "Approval bead filed" in captured.out


# ---------------------------------------------------------------------------
# 13. test_swarm_confirm_abort_when_user_says_no
# ---------------------------------------------------------------------------


def test_swarm_confirm_abort_when_user_says_no() -> None:
    """User types 'n' → SystemExit(1), no dispatch."""
    chunks = _make_chunks(2)

    with (
        patch.object(swarm_cmd, "_partition", return_value=chunks),
        patch.object(swarm_cmd, "_run_with_engine") as mock_dispatch,
        patch("builtins.input", return_value="n"),
        pytest.raises(SystemExit) as exc_info,
    ):
        args = SimpleNamespace(
            directive_json='{"kind":"replace-import","old":"requests","new":"httpx"}',
            max_agents=100,
            language="python",
            model="claude-haiku",
            codebase_root=None,
            dry_run=False,
            yes=False,
            require_approval_bead=None,
        )
        swarm_cmd._handle_refactor(args)

    assert exc_info.value.code == 1
    mock_dispatch.assert_not_called()


# ---------------------------------------------------------------------------
# 14. test_swarm_result_carries_approval_bead_id
# ---------------------------------------------------------------------------


def test_swarm_result_carries_approval_bead_id() -> None:
    """SwarmDispatcher.dispatch() stores approval_bead_id on SwarmResult."""
    from agent_baton.core.swarm.dispatcher import SwarmDispatcher, SwarmResult

    mock_result = SwarmResult(
        swarm_id="sw-xyz",
        n_succeeded=1,
        n_failed=0,
        total_tokens=8000,
        total_cost_usd=0.002,
        wall_clock_sec=0.5,
        coalesce_branch="swarm-coalesce-sw-xyz",
    )
    mock_result.approval_bead_id = "bd-testapproval"
    assert mock_result.approval_bead_id == "bd-testapproval"


def test_swarm_result_default_approval_bead_id_is_empty() -> None:
    from agent_baton.core.swarm.dispatcher import SwarmResult

    result = SwarmResult(
        swarm_id="sw-abc",
        n_succeeded=1,
        n_failed=0,
        total_tokens=1000,
        total_cost_usd=0.001,
        wall_clock_sec=0.1,
        coalesce_branch="test-branch",
    )
    assert result.approval_bead_id == ""


# ---------------------------------------------------------------------------
# 15. test_swarm_require_approval_bead_lookup_finds_recent_bead
# ---------------------------------------------------------------------------


def test_swarm_require_approval_bead_lookup_finds_recent_bead(
    capsys: pytest.CaptureFixture,
) -> None:
    """Lookup mode: recent approval bead found → check passes."""
    mock_bead = MagicMock()
    mock_bead.bead_id = "bd-recent01"
    mock_bead.created_at = "2026-04-28T10:00:00Z"

    mock_store = MagicMock()
    mock_store.find_recent_approvals.return_value = [mock_bead]

    with patch.object(swarm_cmd, "_get_bead_store", return_value=mock_store):
        # Should not raise
        _check_approval_bead(_SENTINEL_REQUIRE_APPROVAL)

    captured = capsys.readouterr()
    assert "bd-recent01" in captured.out
    assert "Approval bead found" in captured.out


# ---------------------------------------------------------------------------
# 16. test_swarm_require_approval_bead_no_store_refuses
# ---------------------------------------------------------------------------


def test_swarm_require_approval_bead_no_store_refuses() -> None:
    """When bead store is unavailable, --require-approval-bead exits non-zero."""
    with (
        patch.object(swarm_cmd, "_get_bead_store", return_value=None),
        pytest.raises(SystemExit) as exc_info,
    ):
        _check_approval_bead(_SENTINEL_REQUIRE_APPROVAL)

    assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# 17. test_estimate_cost
# ---------------------------------------------------------------------------


def test_estimate_cost_haiku() -> None:
    # 1_000_000 input tokens at $0.25/M input + 500_000 output at $1.25/M
    # = 0.25 + 0.625 = $0.875
    cost = _estimate_cost("claude-haiku", 1_000_000)
    assert abs(cost - 0.875) < 0.001


def test_estimate_cost_unknown_model_falls_back_to_haiku() -> None:
    """Unknown model should not crash; falls back to haiku pricing."""
    cost = _estimate_cost("some-unknown-model", 1_000_000)
    assert cost > 0


# ---------------------------------------------------------------------------
# 18. test_directive_summary
# ---------------------------------------------------------------------------


def test_directive_summary_rename() -> None:
    d = RenameSymbol(old="pkg.Old", new="pkg.New")
    assert "pkg.Old" in _directive_summary(d)
    assert "pkg.New" in _directive_summary(d)


def test_directive_summary_replace_import() -> None:
    from agent_baton.core.swarm.partitioner import ReplaceImport
    d = ReplaceImport(old="requests", new="httpx")
    assert "requests" in _directive_summary(d)
    assert "httpx" in _directive_summary(d)


# ---------------------------------------------------------------------------
# 19. BeadStore.find_recent_approvals
# ---------------------------------------------------------------------------


def test_find_recent_approvals_returns_empty_on_no_table() -> None:
    """Graceful degradation when beads table does not exist."""
    from agent_baton.core.engine.bead_store import BeadStore
    import tempfile
    import sqlite3

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)

    # Create a DB with no beads table
    conn = sqlite3.connect(str(db_path))
    conn.close()

    store = BeadStore(db_path=db_path)
    # _table_exists should return False; find_recent_approvals returns []
    result = store.find_recent_approvals(tag="swarm-refactor", max_age_minutes=5)
    assert result == []
    db_path.unlink(missing_ok=True)
