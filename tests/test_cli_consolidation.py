"""Tests for CLI consolidation: alias deprecation paths and new umbrella commands.

Covers end-user readiness concern #12 (beads bd-8eef, bd-7eec, bd-5049):

Alias tests (deprecated commands must still work + print the standard notice):
  test_migrate_storage_alias_dispatches_to_sync
  test_verify_package_alias_dispatches_to_sync_verify
  test_improve_alias_dispatches_to_learn

New-path tests (umbrella commands carry the new functionality):
  test_sync_migrate_storage_flag_works
  test_sync_verify_flag_works
  test_learn_improve_subcommand_works
"""
from __future__ import annotations

import argparse
import io
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_migrate_args(**kwargs) -> argparse.Namespace:
    defaults = dict(
        team_context=".claude/team-context",
        dry_run=False,
        remove_files=False,
        verify=False,
    )
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def _make_improve_args(**kwargs) -> argparse.Namespace:
    defaults = dict(
        run=False,
        force=False,
        report=False,
        experiments=False,
        history=False,
        min_tasks=None,
        interval=None,
    )
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


# ---------------------------------------------------------------------------
# A. migrate-storage alias (bd-8eef)
# ---------------------------------------------------------------------------


class TestMigrateStorageAlias:
    """baton migrate-storage is a deprecated alias for baton sync --migrate-storage."""

    def test_migrate_storage_alias_dispatches_to_sync(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """Alias handler calls the same _cmd_migrate implementation as the new path."""
        from agent_baton.cli.commands.observe.migrate_storage import handler

        ctx = tmp_path / "team-context"
        ctx.mkdir()
        args = _make_migrate_args(team_context=str(ctx))

        # _cmd_migrate is imported inside the handler body; patch at the source module.
        with patch(
            "agent_baton.core.storage.migrate.StorageMigrator"
        ) as mock_cls:
            mock_migrator = MagicMock()
            mock_migrator.scan.return_value = {}
            mock_cls.return_value = mock_migrator
            handler(args)

        captured = capsys.readouterr()
        # Deprecation notice must go to stderr
        assert "warning:" in captured.err
        assert "`baton migrate-storage` is deprecated" in captured.err
        assert "baton sync --migrate-storage" in captured.err
        assert "This alias will be removed in a future release." in captured.err

    def test_migrate_storage_alias_notice_on_stderr_not_stdout(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """Deprecation notice goes to stderr so stdout-piping scripts still work."""
        from agent_baton.cli.commands.observe.migrate_storage import handler

        ctx = tmp_path / "team-context"
        ctx.mkdir()
        args = _make_migrate_args(team_context=str(ctx))

        with patch(
            "agent_baton.core.storage.migrate.StorageMigrator"
        ) as mock_cls:
            mock_migrator = MagicMock()
            mock_migrator.scan.return_value = {}
            mock_cls.return_value = mock_migrator
            handler(args)

        captured = capsys.readouterr()
        # Warning on stderr, not stdout
        assert "warning:" in captured.err
        assert "warning:" not in captured.out

    def test_migrate_storage_alias_register_prog(self) -> None:
        """register() creates a parser with prog ending in 'migrate-storage'."""
        from agent_baton.cli.commands.observe.migrate_storage import register

        parser = argparse.ArgumentParser(prog="baton")
        sub = parser.add_subparsers()
        sp = register(sub)
        assert sp.prog.endswith("migrate-storage")

    def test_migrate_storage_alias_help_mentions_new_path(self) -> None:
        """--help for the alias shows the new canonical path."""
        from agent_baton.cli.commands.observe.migrate_storage import register

        parser = argparse.ArgumentParser(prog="baton")
        sub = parser.add_subparsers()
        sp = register(sub)
        help_text = sp.format_help()
        assert "baton sync --migrate-storage" in help_text


# ---------------------------------------------------------------------------
# B. verify-package alias (bd-7eec)
# ---------------------------------------------------------------------------


class TestVerifyPackageAlias:
    """baton verify-package is a deprecated alias for baton sync --verify ARCHIVE."""

    def test_verify_package_alias_dispatches_to_sync_verify(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """Alias handler calls the same _cmd_verify implementation as the new path."""
        from agent_baton.cli.commands.distribute.verify_package import handler

        fake_archive = tmp_path / "package.tar.gz"
        fake_archive.write_bytes(b"")
        args = argparse.Namespace(archive=str(fake_archive), checksums=False)

        with patch(
            "agent_baton.core.distribute.packager.PackageVerifier"
        ) as mock_cls:
            mock_verifier = MagicMock()
            mock_result = MagicMock()
            mock_result.valid = True
            mock_result.agent_count = 1
            mock_result.reference_count = 1
            mock_result.knowledge_count = 0
            mock_result.errors = []
            mock_result.warnings = []
            mock_result.checksums = {}
            mock_verifier.validate_package.return_value = mock_result
            mock_cls.return_value = mock_verifier

            handler(args)

        captured = capsys.readouterr()
        assert "warning:" in captured.err
        assert "`baton verify-package` is deprecated" in captured.err
        assert "baton sync --verify" in captured.err
        assert "This alias will be removed in a future release." in captured.err

    def test_verify_package_alias_notice_on_stderr_not_stdout(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """Deprecation notice goes to stderr so stdout-piping scripts still work."""
        from agent_baton.cli.commands.distribute.verify_package import handler

        fake_archive = tmp_path / "package.tar.gz"
        fake_archive.write_bytes(b"")
        args = argparse.Namespace(archive=str(fake_archive), checksums=False)

        with patch(
            "agent_baton.core.distribute.packager.PackageVerifier"
        ) as mock_cls:
            mock_verifier = MagicMock()
            mock_result = MagicMock()
            mock_result.valid = True
            mock_result.agent_count = 0
            mock_result.reference_count = 0
            mock_result.knowledge_count = 0
            mock_result.errors = []
            mock_result.warnings = []
            mock_result.checksums = {}
            mock_verifier.validate_package.return_value = mock_result
            mock_cls.return_value = mock_verifier
            handler(args)

        captured = capsys.readouterr()
        assert "warning:" in captured.err
        assert "warning:" not in captured.out

    def test_verify_package_alias_register_prog(self) -> None:
        """register() creates a parser with prog ending in 'verify-package'."""
        from agent_baton.cli.commands.distribute.verify_package import register

        parser = argparse.ArgumentParser(prog="baton")
        sub = parser.add_subparsers()
        sp = register(sub)
        assert sp.prog.endswith("verify-package")

    def test_verify_package_alias_help_mentions_new_path(self) -> None:
        """--help for the alias shows the new canonical path."""
        from agent_baton.cli.commands.distribute.verify_package import register

        parser = argparse.ArgumentParser(prog="baton")
        sub = parser.add_subparsers()
        sp = register(sub)
        help_text = sp.format_help()
        assert "baton sync --verify" in help_text


# ---------------------------------------------------------------------------
# C. improve alias (bd-5049)
# ---------------------------------------------------------------------------


class TestImproveAlias:
    """baton improve is a deprecated alias for baton learn improve."""

    def test_improve_alias_dispatches_to_learn(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        """Alias handler prints the standard notice then calls _improve_handler_impl."""
        from agent_baton.cli.commands.improve import improve_cmd

        args = _make_improve_args(report=True)

        with patch.object(improve_cmd, "_improve_handler_impl") as mock_impl:
            improve_cmd.handler(args)
            mock_impl.assert_called_once_with(args)

        captured = capsys.readouterr()
        assert "warning:" in captured.err
        assert "`baton improve` is deprecated" in captured.err
        assert "baton learn improve" in captured.err
        assert "This alias will be removed in a future release." in captured.err

    def test_improve_alias_notice_on_stderr_not_stdout(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        """Deprecation notice goes to stderr so stdout-piping scripts still work."""
        from agent_baton.cli.commands.improve import improve_cmd

        args = _make_improve_args()

        with patch.object(improve_cmd, "_improve_handler_impl"):
            improve_cmd.handler(args)

        captured = capsys.readouterr()
        assert "warning:" in captured.err
        assert "warning:" not in captured.out

    def test_improve_alias_register_prog(self) -> None:
        """register() creates a parser with prog ending in 'improve'."""
        from agent_baton.cli.commands.improve.improve_cmd import register

        parser = argparse.ArgumentParser(prog="baton")
        sub = parser.add_subparsers()
        sp = register(sub)
        assert sp.prog.endswith("improve")

    def test_improve_alias_help_mentions_new_path(self) -> None:
        """--help for the alias shows the new canonical path."""
        from agent_baton.cli.commands.improve.improve_cmd import register

        parser = argparse.ArgumentParser(prog="baton")
        sub = parser.add_subparsers()
        sp = register(sub)
        help_text = sp.format_help()
        assert "baton learn improve" in help_text


# ---------------------------------------------------------------------------
# D. New paths: baton sync --migrate-storage (bd-8eef)
# ---------------------------------------------------------------------------


class TestSyncMigrateStorageFlag:
    """baton sync --migrate-storage is the new canonical path."""

    def test_sync_migrate_storage_flag_registered(self) -> None:
        """sync register() exposes --migrate-storage flag."""
        from agent_baton.cli.commands.sync_cmd import register

        parser = argparse.ArgumentParser(prog="baton")
        sub = parser.add_subparsers()
        sp = register(sub)
        # Parse with the flag — if unrecognised, argparse raises SystemExit
        args = sp.parse_args(["--migrate-storage"])
        assert args.migrate_storage is True

    def test_sync_migrate_storage_flag_works(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """baton sync --migrate-storage delegates to _cmd_migrate with correct args."""
        from agent_baton.cli.commands.sync_cmd import handler

        ctx = tmp_path / "team-context"
        ctx.mkdir()
        args = argparse.Namespace(
            migrate_storage=True,
            verify_package=False,
            subcommand=None,
            sync_all=False,
            project=None,
            rebuild=False,
            team_context=str(ctx),
            dry_run=False,
            remove_files=False,
            migrate_verify=False,
            archive=None,
            checksums=False,
        )

        with patch(
            "agent_baton.core.storage.migrate.StorageMigrator"
        ) as mock_cls:
            mock_migrator = MagicMock()
            mock_migrator.scan.return_value = {}
            mock_cls.return_value = mock_migrator
            handler(args)

        # StorageMigrator should have been called with our ctx path
        mock_cls.assert_called_once()
        call_arg = mock_cls.call_args[0][0]
        assert str(ctx) in str(call_arg)

    def test_sync_migrate_storage_dry_run_no_db(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """--dry-run with --migrate-storage does not create baton.db."""
        from agent_baton.cli.commands.sync_cmd import handler

        ctx = tmp_path / "team-context"
        ctx.mkdir()
        # Write a minimal execution stub so scan finds something
        (ctx / "usage-log.jsonl").write_text(
            '{"task_id": "t1", "timestamp": "2026-01-01T00:00:00Z"}\n',
            encoding="utf-8",
        )
        args = argparse.Namespace(
            migrate_storage=True,
            verify_package=False,
            subcommand=None,
            sync_all=False,
            project=None,
            rebuild=False,
            team_context=str(ctx),
            dry_run=True,
            remove_files=False,
            migrate_verify=False,
            archive=None,
            checksums=False,
        )

        handler(args)

        # baton.db must NOT be created in dry-run mode
        assert not (ctx / "baton.db").exists()
        captured = capsys.readouterr()
        assert "dry run" in captured.out.lower()

    def test_sync_migrate_storage_no_deprecation_notice(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """The new path must NOT print a deprecation notice."""
        from agent_baton.cli.commands.sync_cmd import handler

        ctx = tmp_path / "team-context"
        ctx.mkdir()
        args = argparse.Namespace(
            migrate_storage=True,
            verify_package=False,
            subcommand=None,
            sync_all=False,
            project=None,
            rebuild=False,
            team_context=str(ctx),
            dry_run=False,
            remove_files=False,
            migrate_verify=False,
            archive=None,
            checksums=False,
        )

        with patch(
            "agent_baton.core.storage.migrate.StorageMigrator"
        ) as mock_cls:
            mock_migrator = MagicMock()
            mock_migrator.scan.return_value = {}
            mock_cls.return_value = mock_migrator
            handler(args)

        captured = capsys.readouterr()
        assert "deprecated" not in captured.err


# ---------------------------------------------------------------------------
# E. New paths: baton sync --verify ARCHIVE (bd-7eec)
# ---------------------------------------------------------------------------


class TestSyncVerifyFlag:
    """baton sync --verify ARCHIVE is the new canonical path."""

    def test_sync_verify_flag_registered(self) -> None:
        """sync register() exposes --verify ARCHIVE (archive path as flag value)."""
        from agent_baton.cli.commands.sync_cmd import register

        parser = argparse.ArgumentParser(prog="baton")
        sub = parser.add_subparsers()
        sp = register(sub)
        # --verify takes the archive path as its own value
        args = sp.parse_args(["--verify", "pkg.tar.gz"])
        assert args.verify_package == "pkg.tar.gz"

    def test_sync_verify_flag_works(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """baton sync --verify ARCHIVE delegates to _cmd_verify."""
        from agent_baton.cli.commands.sync_cmd import handler

        fake_archive = tmp_path / "package.tar.gz"
        fake_archive.write_bytes(b"")
        # verify_package holds the archive path string (new interface)
        args = argparse.Namespace(
            migrate_storage=False,
            verify_package=str(fake_archive),
            subcommand=None,
            sync_all=False,
            project=None,
            rebuild=False,
            checksums=False,
            team_context=".claude/team-context",
            dry_run=False,
            remove_files=False,
            migrate_verify=False,
        )

        with patch(
            "agent_baton.core.distribute.packager.PackageVerifier"
        ) as mock_cls:
            mock_verifier = MagicMock()
            mock_result = MagicMock()
            mock_result.valid = True
            mock_result.agent_count = 2
            mock_result.reference_count = 3
            mock_result.knowledge_count = 0
            mock_result.errors = []
            mock_result.warnings = []
            mock_result.checksums = {}
            mock_verifier.validate_package.return_value = mock_result
            mock_cls.return_value = mock_verifier

            handler(args)

        mock_verifier.validate_package.assert_called_once()

    def test_sync_verify_flag_missing_archive_exits(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        """--verify with no ARCHIVE value (bare flag, const=True) exits with code 1."""
        from agent_baton.cli.commands.sync_cmd import handler

        # verify_package=True simulates `--verify` with no path (const value)
        args = argparse.Namespace(
            migrate_storage=False,
            verify_package=True,
            subcommand=None,
            sync_all=False,
            project=None,
            rebuild=False,
            checksums=False,
            team_context=".claude/team-context",
            dry_run=False,
            remove_files=False,
            migrate_verify=False,
        )

        with pytest.raises(SystemExit) as exc_info:
            handler(args)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "error:" in captured.err
        assert "ARCHIVE" in captured.err

    def test_sync_verify_no_deprecation_notice(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """The new path must NOT print a deprecation notice."""
        from agent_baton.cli.commands.sync_cmd import handler

        fake_archive = tmp_path / "package.tar.gz"
        fake_archive.write_bytes(b"")
        args = argparse.Namespace(
            migrate_storage=False,
            verify_package=str(fake_archive),
            subcommand=None,
            sync_all=False,
            project=None,
            rebuild=False,
            checksums=False,
            team_context=".claude/team-context",
            dry_run=False,
            remove_files=False,
            migrate_verify=False,
        )

        with patch(
            "agent_baton.core.distribute.packager.PackageVerifier"
        ) as mock_cls:
            mock_verifier = MagicMock()
            mock_result = MagicMock()
            mock_result.valid = True
            mock_result.agent_count = 0
            mock_result.reference_count = 0
            mock_result.knowledge_count = 0
            mock_result.errors = []
            mock_result.warnings = []
            mock_result.checksums = {}
            mock_verifier.validate_package.return_value = mock_result
            mock_cls.return_value = mock_verifier
            handler(args)

        captured = capsys.readouterr()
        assert "deprecated" not in captured.err


# ---------------------------------------------------------------------------
# F. New path: baton learn improve (bd-5049)
# ---------------------------------------------------------------------------


class TestLearnImproveSubcommand:
    """baton learn improve is the canonical path (subcommand under baton learn)."""

    def test_learn_improve_subcommand_registered(self) -> None:
        """learn register() exposes the 'improve' subcommand."""
        from agent_baton.cli.commands.improve.learn_cmd import register

        parser = argparse.ArgumentParser(prog="baton")
        sub = parser.add_subparsers()
        sp = register(sub)
        learn_sub = sp._subparsers  # type: ignore[attr-defined]
        # Parse a 'improve' subcommand — if unrecognised argparse raises SystemExit
        args = sp.parse_args(["improve"])
        assert args.learn_command == "improve"

    def test_learn_improve_subcommand_works(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        """baton learn improve delegates to _improve_handler_impl without a warning."""
        from agent_baton.cli.commands.improve.learn_cmd import handler
        from agent_baton.cli.commands.improve import improve_cmd

        args = argparse.Namespace(
            learn_command="improve",
            run=False,
            force=False,
            report=True,
            experiments=False,
            history=False,
            min_tasks=None,
            interval=None,
        )

        with patch.object(improve_cmd, "_improve_handler_impl") as mock_impl:
            handler(args)
            mock_impl.assert_called_once_with(args)

        captured = capsys.readouterr()
        # New path must NOT print a deprecation warning
        assert "deprecated" not in captured.err

    def test_learn_improve_no_deprecation_notice(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        """baton learn improve does not emit any deprecation text."""
        from agent_baton.cli.commands.improve.learn_cmd import handler
        from agent_baton.cli.commands.improve import improve_cmd

        args = argparse.Namespace(
            learn_command="improve",
            run=True,
            force=False,
            report=False,
            experiments=False,
            history=False,
            min_tasks=None,
            interval=None,
        )

        with patch.object(improve_cmd, "_improve_handler_impl"):
            handler(args)

        captured = capsys.readouterr()
        assert "warning:" not in captured.err
        assert "deprecated" not in captured.err
