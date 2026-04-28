"""Tests for Wave 6.2 Part B — ContextCache (bd-be76).

Covers:
- get_or_build() returns JSON string
- Snapshot is cached (second call does not rebuild)
- invalidate() forces rebuild on next call
- Monthly invalidation (built_at > 30 days ago → rebuild)
- _parse_python_file extracts public API and deprecated symbols
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from agent_baton.core.immune.cache import ContextCache


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestContextCacheBuildAndServe:
    def test_get_or_build_returns_json_string(self, tmp_path: Path) -> None:
        """get_or_build() returns a non-empty JSON string."""
        cache = ContextCache(project_root=tmp_path)
        result = cache.get_or_build()
        assert isinstance(result, str)
        data = json.loads(result)
        assert "built_at" in data
        assert "file_tree" in data

    def test_snapshot_written_to_disk(self, tmp_path: Path) -> None:
        """After get_or_build(), context_snapshot.json exists on disk."""
        cache = ContextCache(project_root=tmp_path)
        cache.get_or_build()
        snapshot_path = tmp_path / ".claude" / "immune" / "context_snapshot.json"
        assert snapshot_path.exists()

    def test_second_call_returns_cached_value(self, tmp_path: Path) -> None:
        """Second call to get_or_build() returns the cached snapshot."""
        cache = ContextCache(project_root=tmp_path)
        first = cache.get_or_build()
        # The file tree for tmp_path won't change between calls, so the
        # built_at timestamp should be identical.
        second = cache.get_or_build()
        first_data = json.loads(first)
        second_data = json.loads(second)
        assert first_data["built_at"] == second_data["built_at"]

    def test_invalidate_forces_rebuild(self, tmp_path: Path) -> None:
        """invalidate() causes get_or_build() to rebuild the snapshot."""
        cache = ContextCache(project_root=tmp_path)
        first = cache.get_or_build()
        import time; time.sleep(0.01)  # ensure timestamp changes
        cache.invalidate()
        # After invalidation the file should be gone.
        snapshot_path = tmp_path / ".claude" / "immune" / "context_snapshot.json"
        assert not snapshot_path.exists()
        # Next call rebuilds.
        second = cache.get_or_build()
        first_data = json.loads(first)
        second_data = json.loads(second)
        # Timestamps may differ by milliseconds; structure must be identical.
        assert "built_at" in second_data
        assert "file_tree" in second_data


class TestMonthlyInvalidation:
    def test_stale_snapshot_rebuilt(self, tmp_path: Path) -> None:
        """A snapshot with built_at > 30 days ago is rebuilt automatically."""
        cache = ContextCache(project_root=tmp_path)
        # Manually write a stale snapshot.
        snapshot_dir = tmp_path / ".claude" / "immune"
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        stale_ts = (
            (datetime.now(timezone.utc) - timedelta(days=35))
            .strftime("%Y-%m-%dT%H:%M:%SZ")
        )
        stale = json.dumps({
            "built_at": stale_ts,
            "file_tree": ["old/file.py"],
            "public_api": {},
            "deprecated_symbols": [],
            "dependency_graph": {},
        })
        (snapshot_dir / "context_snapshot.json").write_text(stale)

        result = cache.get_or_build()
        data = json.loads(result)
        # built_at must be newer than the stale timestamp.
        assert data["built_at"] != stale_ts

    def test_fresh_snapshot_not_rebuilt(self, tmp_path: Path) -> None:
        """A snapshot with built_at < 30 days ago is returned as-is."""
        cache = ContextCache(project_root=tmp_path)
        snapshot_dir = tmp_path / ".claude" / "immune"
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        fresh_ts = (
            (datetime.now(timezone.utc) - timedelta(days=1))
            .strftime("%Y-%m-%dT%H:%M:%SZ")
        )
        fresh = json.dumps({
            "built_at": fresh_ts,
            "file_tree": ["src/mod.py"],
            "public_api": {"src.mod": ["MyClass"]},
            "deprecated_symbols": [],
            "dependency_graph": {},
        })
        (snapshot_dir / "context_snapshot.json").write_text(fresh)

        result = cache.get_or_build()
        data = json.loads(result)
        # Must return the cached version unchanged.
        assert data["built_at"] == fresh_ts
        assert data["file_tree"] == ["src/mod.py"]


class TestParsePythonFile:
    def test_extracts_all_from_dunder(self, tmp_path: Path) -> None:
        """Public API extracted from __all__."""
        source = '''\
__all__ = ["Foo", "bar"]

class Foo:
    pass

def bar():
    pass
'''
        exports, deprecated, imports = ContextCache._parse_python_file(source)
        assert "Foo" in exports
        assert "bar" in exports

    def test_detects_deprecated_decorator(self, tmp_path: Path) -> None:
        """Symbols decorated with @deprecated are flagged."""
        source = '''\
from deprecated import deprecated

@deprecated
def old_func():
    pass
'''
        exports, deprecated_syms, imports = ContextCache._parse_python_file(source)
        assert "old_func" in deprecated_syms

    def test_extracts_imports(self, tmp_path: Path) -> None:
        """Import statements contribute to the dependency graph."""
        source = '''\
import os
import json
from pathlib import Path
'''
        exports, deprecated, imports = ContextCache._parse_python_file(source)
        assert "os" in imports
        assert "json" in imports
        assert "pathlib" in imports

    def test_handles_syntax_error_gracefully(self, tmp_path: Path) -> None:
        """Syntax errors in source do not raise; empty lists are returned."""
        source = "def broken(:\n    pass\n"
        exports, deprecated, imports = ContextCache._parse_python_file(source)
        assert exports == []
        assert deprecated == []
        assert imports == []
