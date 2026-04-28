"""Regression tests for DX.7 Phase B deprecation-shim and sync-cmd fixes.

Covers:
- bd-ed80: evolve/experiment shims swallow unknown args and print deprecation.
  The fix lives in main.py (parse_known_args for deprecated commands). Tests
  exercise the full main() path so the root-parser dispatch is validated
  end-to-end (unit-testing the subparser alone cannot reproduce the bug because
  it's the ROOT parser that rejects unrecognised --flags).
- bd-03da: baton sync --verify ARCHIVE populates verify_package correctly;
  no conflicting 'archive' positional exists.
"""
from __future__ import annotations

import argparse
from io import StringIO
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _invoke_main(argv: list[str]) -> tuple[int, str, str]:
    """Run main(argv), return (exit_code, stdout, stderr).

    exit_code=0 when main() returns without raising SystemExit.
    """
    from agent_baton.cli import main as cli_main

    out_buf = StringIO()
    err_buf = StringIO()
    exit_code = 0
    with patch("sys.stdout", out_buf), patch("sys.stderr", err_buf):
        try:
            cli_main.main(argv)
        except SystemExit as exc:
            exit_code = int(exc.code) if exc.code is not None else 0
    return exit_code, out_buf.getvalue(), err_buf.getvalue()


# ---------------------------------------------------------------------------
# bd-ed80: deprecation shims must absorb unknown arguments
# ---------------------------------------------------------------------------


class TestEvolveShimSwallowsUnknownArgs:
    """baton evolve with arbitrary extra flags must reach handler, not argparse error."""

    def test_absorbs_flags_and_prints_deprecation(self) -> None:
        exit_code, stdout, stderr = _invoke_main(["evolve", "--run", "--foo", "--bar"])

        combined = stdout + stderr
        # Must NOT contain the argparse "unrecognized arguments" error phrase
        assert "unrecognized arguments" not in combined, (
            f"argparse error leaked through: {combined!r}"
        )
        # Must contain a deprecation hint (banner from main.py pre-parse OR handler)
        assert "deprecated" in combined.lower() or "baton learn" in combined, (
            f"Expected deprecation message, got: {combined!r}"
        )
        # argparse errors with exit code 2; any other code is acceptable
        assert exit_code != 2, f"Got argparse error exit code 2; stderr: {stderr!r}"

    def test_bare_invocation_still_works(self) -> None:
        exit_code, stdout, stderr = _invoke_main(["evolve"])

        combined = stdout + stderr
        assert "deprecated" in combined.lower() or "baton learn" in combined
        assert exit_code != 2


class TestExperimentShimSwallowsUnknownArgs:
    """baton experiment with arbitrary extra flags must reach handler, not argparse error."""

    def test_absorbs_flags_and_prints_deprecation(self) -> None:
        exit_code, stdout, stderr = _invoke_main(["experiment", "--id", "42", "--whatever"])

        combined = stdout + stderr
        assert "unrecognized arguments" not in combined, (
            f"argparse error leaked through: {combined!r}"
        )
        assert "deprecated" in combined.lower() or "baton learn" in combined, (
            f"Expected deprecation message, got: {combined!r}"
        )
        assert exit_code != 2, f"Got argparse error exit code 2; stderr: {stderr!r}"

    def test_bare_invocation_still_works(self) -> None:
        exit_code, stdout, stderr = _invoke_main(["experiment"])

        combined = stdout + stderr
        assert "deprecated" in combined.lower() or "baton learn" in combined
        assert exit_code != 2


# ---------------------------------------------------------------------------
# bd-03da: sync --verify ARCHIVE populates verify_package; no archive conflict
# ---------------------------------------------------------------------------


class TestSyncVerifyWithArchivePath:
    """baton sync --verify pkg.tar.gz must set verify_package correctly."""

    def _make_sync_parser(self) -> argparse.ArgumentParser:
        from agent_baton.cli.commands import sync_cmd

        root = argparse.ArgumentParser(prog="baton")
        sub = root.add_subparsers(dest="command")
        sync_cmd.register(sub)
        return root

    def test_verify_package_set_from_flag_value(self) -> None:
        root = self._make_sync_parser()
        args = root.parse_args(["sync", "--verify", "pkg.tar.gz"])

        assert args.verify_package == "pkg.tar.gz", (
            f"Expected 'pkg.tar.gz', got {args.verify_package!r}"
        )

    def test_no_conflicting_archive_positional(self) -> None:
        """The 'archive' positional must not exist in the sync namespace (bd-03da)."""
        root = self._make_sync_parser()
        args = root.parse_args(["sync", "--verify", "pkg.tar.gz"])

        assert not hasattr(args, "archive"), (
            "Dangling 'archive' positional still registered on sync parser"
        )

    def test_verify_bare_flag_is_truthy_sentinel(self) -> None:
        """--verify with no path sets verify_package to the const sentinel (True)."""
        root = self._make_sync_parser()
        args = root.parse_args(["sync", "--verify"])

        # const=True when no path is supplied
        assert args.verify_package is True, (
            f"Expected True sentinel, got {args.verify_package!r}"
        )

    def test_verify_absent_is_false(self) -> None:
        """Omitting --verify leaves verify_package=False."""
        root = self._make_sync_parser()
        args = root.parse_args(["sync"])

        assert args.verify_package is False, (
            f"Expected False, got {args.verify_package!r}"
        )
