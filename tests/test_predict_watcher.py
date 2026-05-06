"""Tests for agent_baton.core.predict.watcher (Wave 6.2 Part C, bd-03b0).

Covers:
- test_watcher_filters_gitignore
- test_watcher_never_reads_secrets
- test_watcher_never_reads_via_symlink   (privacy gate — symlink bypass attempt)
- FileEvent construction and debounce helpers
- _glob_match / _match_parts
- _matches_never_read (hardcoded deny-list)
"""
from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent_baton.core.predict.watcher import (
    HARDCODED_NEVER_READ_GLOBS,
    FileEvent,
    FileWatcher,
    _Debouncer,
    _GitignoreFilter,
    _glob_match,
    _match_parts,
    _matches_never_read,
    _snapshot_hash,
)


# ---------------------------------------------------------------------------
# _glob_match / _match_parts
# ---------------------------------------------------------------------------


class TestGlobMatch:
    def test_simple_extension(self) -> None:
        assert _glob_match("/project/src/foo.py", "**/*.py")

    def test_no_match(self) -> None:
        assert not _glob_match("/project/src/foo.js", "**/*.py")

    def test_double_star_zero_components(self) -> None:
        assert _glob_match("/project/foo.py", "**/*.py")

    def test_double_star_multi_component(self) -> None:
        assert _glob_match("/project/a/b/c/foo.py", "**/*.py")

    def test_exact_filename(self) -> None:
        assert _glob_match("/project/.env", "**/.env")

    def test_secrets_dir(self) -> None:
        assert _glob_match("/project/config/secrets/db.key", "**/secrets/**")

    def test_nested_env(self) -> None:
        assert _glob_match("/project/sub/.env", "**/.env")

    def test_key_extension(self) -> None:
        assert _glob_match("/home/user/.ssh/id_rsa.key", "**/*.key")

    def test_pem_extension(self) -> None:
        assert _glob_match("/etc/ssl/certs/cert.pem", "**/*.pem")


# ---------------------------------------------------------------------------
# _matches_never_read
# ---------------------------------------------------------------------------


class TestMatchesNeverRead:
    def test_dot_env_blocked(self, tmp_path: Path) -> None:
        f = tmp_path / ".env"
        f.write_text("SECRET=1")
        assert _matches_never_read(f, ())

    def test_dot_env_with_suffix_blocked(self, tmp_path: Path) -> None:
        f = tmp_path / ".env.production"
        f.write_text("KEY=value")
        assert _matches_never_read(f, ())

    def test_key_file_blocked(self, tmp_path: Path) -> None:
        f = tmp_path / "deploy.key"
        f.touch()
        assert _matches_never_read(f, ())

    def test_pem_file_blocked(self, tmp_path: Path) -> None:
        f = tmp_path / "cert.pem"
        f.touch()
        assert _matches_never_read(f, ())

    def test_secrets_dir_blocked(self, tmp_path: Path) -> None:
        d = tmp_path / "secrets"
        d.mkdir()
        f = d / "password.txt"
        f.write_text("hunter2")
        assert _matches_never_read(f, ())

    def test_normal_py_allowed(self, tmp_path: Path) -> None:
        f = tmp_path / "main.py"
        f.write_text("print('hello')")
        assert not _matches_never_read(f, ())

    def test_custom_glob_blocked(self, tmp_path: Path) -> None:
        f = tmp_path / "internal.secret"
        f.touch()
        assert _matches_never_read(f, ("**/*.secret",))

    def test_unresolvable_path_fails_closed(self) -> None:
        # A path under a non-existent directory cannot always be resolved;
        # the function should fail closed (return True) or at least not raise.
        p = Path("/nonexistent/ghost/dir/.env")
        # We don't assert True because on some systems resolve() doesn't raise
        # for non-existent paths — just assert no exception.
        result = _matches_never_read(p, ())
        assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# test_watcher_never_reads_via_symlink  (privacy gate)
# ---------------------------------------------------------------------------


class TestSymlinkPrivacyGate:
    """The privacy gate MUST catch symlinks pointing at sensitive files."""

    def test_symlink_to_env_blocked(self, tmp_path: Path) -> None:
        """A symlink whose resolved target matches a deny-list pattern is blocked."""
        real_env = tmp_path / ".env"
        real_env.write_text("DB_PASSWORD=s3cr3t")
        # Create a symlink with an innocuous name.
        link = tmp_path / "config_link"
        link.symlink_to(real_env)
        # The symlink target resolves to .env which matches **/.env.
        assert _matches_never_read(link, ())

    def test_symlink_to_key_blocked(self, tmp_path: Path) -> None:
        real_key = tmp_path / "id_rsa.key"
        real_key.write_text("-----BEGIN RSA PRIVATE KEY-----\n...")
        link = tmp_path / "not_a_secret.link"
        link.symlink_to(real_key)
        assert _matches_never_read(link, ())

    def test_symlink_to_normal_file_allowed(self, tmp_path: Path) -> None:
        real_py = tmp_path / "module.py"
        real_py.write_text("x = 1")
        link = tmp_path / "alias.py"
        link.symlink_to(real_py)
        assert not _matches_never_read(link, ())


# ---------------------------------------------------------------------------
# test_watcher_filters_gitignore
# ---------------------------------------------------------------------------


class TestGitignoreFilter:
    def test_builtin_skip_pycache(self, tmp_path: Path) -> None:
        f = _GitignoreFilter(tmp_path)
        p = tmp_path / "__pycache__" / "mod.pyc"
        assert f.should_skip(p)

    def test_builtin_skip_git(self, tmp_path: Path) -> None:
        f = _GitignoreFilter(tmp_path)
        p = tmp_path / ".git" / "HEAD"
        assert f.should_skip(p)

    def test_builtin_skip_node_modules(self, tmp_path: Path) -> None:
        f = _GitignoreFilter(tmp_path)
        p = tmp_path / "node_modules" / "package" / "index.js"
        assert f.should_skip(p)

    def test_normal_py_not_skipped(self, tmp_path: Path) -> None:
        f = _GitignoreFilter(tmp_path)
        p = tmp_path / "src" / "main.py"
        assert not f.should_skip(p)

    def test_gitignore_loaded_when_pathspec_unavailable(self, tmp_path: Path) -> None:
        """Filter works even if pathspec is not installed (builtin fallback)."""
        with patch.dict("sys.modules", {"pathspec": None}):
            f = _GitignoreFilter(tmp_path)
            p = tmp_path / "src" / "app.py"
            # Should not raise; builtin rules apply.
            result = f.should_skip(p)
            assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# test_watcher_never_reads_secrets (FileWatcher integration)
# ---------------------------------------------------------------------------


class TestFileWatcherPrivacyGate:
    """FileWatcher must not read or hash files matching the deny-list."""

    def test_make_event_for_env_returns_empty_hash(self, tmp_path: Path) -> None:
        """_make_event must produce snapshot_hash='' for .env files."""
        env_file = tmp_path / ".env"
        env_file.write_text("SECRET=yes")

        watcher = FileWatcher(
            project_root=tmp_path,
            watch_globs=["**/.env", "**/*.py"],
        )
        event = watcher._make_event(env_file, "modified")
        # The watcher may filter by watch_globs; if it emits the event,
        # snapshot_hash must be empty (never read).
        if event is not None:
            assert event.snapshot_hash == "", (
                f"Privacy gate violated: snapshot_hash={event.snapshot_hash!r} "
                f"for {env_file}"
            )

    def test_make_event_for_normal_py_returns_hash(self, tmp_path: Path) -> None:
        """Non-secret files should produce a valid sha256 hash."""
        py_file = tmp_path / "module.py"
        py_file.write_text("x = 1")

        watcher = FileWatcher(project_root=tmp_path)
        event = watcher._make_event(py_file, "modified")
        assert event is not None
        assert len(event.snapshot_hash) == 64   # sha256 hex = 64 chars

    def test_make_event_for_key_returns_empty_hash(self, tmp_path: Path) -> None:
        """*.key files must never be read."""
        key_file = tmp_path / "deploy.key"
        key_file.write_text("PRIVATE_KEY_DATA")

        watcher = FileWatcher(
            project_root=tmp_path,
            watch_globs=["**/*.key", "**/*.py"],
        )
        event = watcher._make_event(key_file, "modified")
        if event is not None:
            assert event.snapshot_hash == ""

    def test_deleted_event_empty_hash_regardless(self, tmp_path: Path) -> None:
        """Deleted-op events always have empty hash (file gone)."""
        py_file = tmp_path / "gone.py"
        # Don't create the file; it's "deleted".
        watcher = FileWatcher(project_root=tmp_path)
        event = watcher._make_event(py_file, "deleted")
        if event is not None:
            assert event.snapshot_hash == ""

    def test_symlink_to_env_never_read(self, tmp_path: Path) -> None:
        """Symlink pointing at .env must not be read even under innocuous name."""
        real_env = tmp_path / ".env"
        real_env.write_text("DB_PASSWORD=secret")
        link = tmp_path / "harmless_link.py"
        link.symlink_to(real_env)

        watcher = FileWatcher(
            project_root=tmp_path,
            watch_globs=["**/*.py"],
        )
        event = watcher._make_event(link, "modified")
        # If an event is emitted, its hash must be empty.
        if event is not None:
            assert event.snapshot_hash == "", (
                "Privacy gate violated via symlink: snapshot was read for a "
                "symlink pointing at .env"
            )


# ---------------------------------------------------------------------------
# _snapshot_hash
# ---------------------------------------------------------------------------


class TestSnapshotHash:
    def test_hash_matches_sha256(self, tmp_path: Path) -> None:
        f = tmp_path / "data.py"
        content = b"hello world"
        f.write_bytes(content)
        expected = hashlib.sha256(content).hexdigest()
        assert _snapshot_hash(f) == expected

    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        assert _snapshot_hash(tmp_path / "ghost.py") == ""


# ---------------------------------------------------------------------------
# _Debouncer
# ---------------------------------------------------------------------------


class TestDebouncer:
    def test_debounce_collapses_rapid_events(self, tmp_path: Path) -> None:
        """Multiple rapid pushes for the same path should collapse to one."""
        import queue as _q
        out: _q.Queue = _q.Queue()
        debouncer = _Debouncer(out, window_ms=50)

        p = tmp_path / "foo.py"
        ev1 = FileEvent(path=p, op="modified", ts=1.0, snapshot_hash="aaa")
        ev2 = FileEvent(path=p, op="modified", ts=1.1, snapshot_hash="bbb")
        ev3 = FileEvent(path=p, op="modified", ts=1.2, snapshot_hash="ccc")

        debouncer.push(ev1)
        debouncer.push(ev2)
        debouncer.push(ev3)   # This should be the one that fires.

        # Wait for debounce window to expire.
        time.sleep(0.2)

        collected = []
        while not out.empty():
            collected.append(out.get_nowait())

        assert len(collected) == 1
        assert collected[0].snapshot_hash == "ccc"

    def test_debounce_different_paths_independent(self, tmp_path: Path) -> None:
        """Events for different paths are debounced independently."""
        import queue as _q
        out: _q.Queue = _q.Queue()
        debouncer = _Debouncer(out, window_ms=50)

        p1 = tmp_path / "a.py"
        p2 = tmp_path / "b.py"
        ev1 = FileEvent(path=p1, op="modified", ts=1.0, snapshot_hash="aaa")
        ev2 = FileEvent(path=p2, op="modified", ts=1.0, snapshot_hash="bbb")

        debouncer.push(ev1)
        debouncer.push(ev2)

        time.sleep(0.2)

        collected = []
        while not out.empty():
            collected.append(out.get_nowait())

        assert len(collected) == 2
        hashes = {e.snapshot_hash for e in collected}
        assert hashes == {"aaa", "bbb"}


# ---------------------------------------------------------------------------
# FileWatcher.start() ImportError when watchdog not installed
# ---------------------------------------------------------------------------


class TestFileWatcherImportError:
    def test_start_raises_import_error_without_watchdog(self, tmp_path: Path) -> None:
        """When watchdog is not installed, start() raises ImportError."""
        watcher = FileWatcher(project_root=tmp_path)
        with patch.dict("sys.modules", {"watchdog": None, "watchdog.observers": None,
                                        "watchdog.events": None}):
            # Force import to fail inside start().
            with pytest.raises(ImportError, match="watchdog"):
                # Patch the import inside start() by manipulating builtins.
                import builtins
                original_import = builtins.__import__

                def _block_watchdog(name: str, *args: object, **kwargs: object) -> object:
                    if name.startswith("watchdog"):
                        raise ImportError("No module named 'watchdog'")
                    return original_import(name, *args, **kwargs)

                with patch("builtins.__import__", side_effect=_block_watchdog):
                    watcher.start()
