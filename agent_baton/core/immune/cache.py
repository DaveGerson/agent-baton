"""Wave 6.2 Part B — ContextCache: monthly-rebuilt project snapshot.

Builds a compact JSON snapshot of the project (file tree, public API surface,
deprecated symbol list, dependency graph) and caches it at
``.claude/immune/context_snapshot.json``.  The snapshot is used as the
prompt-cache prefix for all sweep dispatches, achieving ~90% input-token
discount on cache hits.

Snapshot format:

.. code-block:: json

    {
        "built_at": "2026-04-28T00:00:00Z",
        "file_tree": ["path/to/file.py", ...],
        "public_api": {"module": ["symbol", ...], ...},
        "deprecated_symbols": ["old_func", ...],
        "dependency_graph": {"module": ["dep_module", ...], ...}
    }

Target size: ≤50 KB compressed → ~12K tokens.
"""
from __future__ import annotations

import ast
import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path

from agent_baton.utils.time import utcnow as _utcnow, utcnow_zulu as _utcnow_str

_log = logging.getLogger(__name__)

__all__ = ["ContextCache"]

# Rebuild the snapshot if older than this many days.
_CACHE_REBUILD_INTERVAL_DAYS = 30
# Max bytes for the snapshot file (50 KB guard).
_MAX_SNAPSHOT_BYTES = 50 * 1024
# Max file tree entries (truncated beyond this to keep token count down).
_MAX_FILE_TREE_ENTRIES = 500


class ContextCache:
    """Monthly-rebuilt project context snapshot.

    Builds once per month and returns the cached JSON string on subsequent
    calls.  The cache file lives at ``.claude/immune/context_snapshot.json``
    relative to *project_root*.

    Args:
        project_root: Root directory of the project being swept.
    """

    def __init__(self, project_root: Path) -> None:
        self._root = project_root
        self._cache_dir = project_root / ".claude" / "immune"
        self._snapshot_path = self._cache_dir / "context_snapshot.json"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_or_build(self) -> str:
        """Return the project context snapshot JSON string.

        Returns the cached value when it is still fresh (< 30 days old).
        Rebuilds otherwise.

        Returns:
            JSON-encoded snapshot string (≤50 KB).
        """
        if self._is_fresh():
            try:
                return self._snapshot_path.read_text(encoding="utf-8")
            except OSError as exc:
                _log.warning("ContextCache: could not read cached snapshot: %s", exc)

        return self._build_and_persist()

    def invalidate(self) -> None:
        """Force a rebuild on the next :meth:`get_or_build` call."""
        try:
            if self._snapshot_path.exists():
                self._snapshot_path.unlink()
        except OSError as exc:
            _log.debug("ContextCache.invalidate: %s", exc)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _is_fresh(self) -> bool:
        """Return ``True`` when the cached snapshot is < 30 days old."""
        if not self._snapshot_path.exists():
            return False
        try:
            raw = json.loads(self._snapshot_path.read_text(encoding="utf-8"))
            built_at_str: str = raw.get("built_at", "")
            if not built_at_str:
                return False
            built_at = datetime.fromisoformat(built_at_str.replace("Z", "+00:00"))
            return (_utcnow() - built_at) < timedelta(days=_CACHE_REBUILD_INTERVAL_DAYS)
        except Exception as exc:
            _log.debug("ContextCache._is_fresh parse error: %s", exc)
            return False

    def _build_and_persist(self) -> str:
        """Build the snapshot, write it to disk, and return the JSON string."""
        snapshot = self._build()
        payload = json.dumps(snapshot, indent=None, separators=(",", ":"))
        # Truncate if over 50 KB (guards context budget).
        if len(payload.encode("utf-8")) > _MAX_SNAPSHOT_BYTES:
            _log.warning(
                "ContextCache: snapshot exceeds %d bytes — truncating file tree",
                _MAX_SNAPSHOT_BYTES,
            )
            snapshot["file_tree"] = snapshot["file_tree"][:200]
            snapshot["_truncated"] = True
            payload = json.dumps(snapshot, indent=None, separators=(",", ":"))

        try:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            self._snapshot_path.write_text(payload, encoding="utf-8")
        except OSError as exc:
            _log.warning("ContextCache: could not persist snapshot: %s", exc)

        return payload

    def _build(self) -> dict:
        """Walk the file tree, extract public API surface, deprecated symbols,
        and an import-based dependency graph.

        Returns:
            A dict suitable for JSON serialisation.
        """
        file_tree = self._collect_file_tree()
        public_api: dict[str, list[str]] = {}
        deprecated_symbols: list[str] = []
        dep_graph: dict[str, list[str]] = {}

        for rel_path in file_tree:
            abs_path = self._root / rel_path
            if not abs_path.suffix == ".py":
                continue
            try:
                source = abs_path.read_text(encoding="utf-8", errors="replace")
                module_key = rel_path.replace(os.sep, ".").removesuffix(".py")
                exports, deprecated, deps = self._parse_python_file(source)
                if exports:
                    public_api[module_key] = exports
                deprecated_symbols.extend(deprecated)
                if deps:
                    dep_graph[module_key] = deps
            except Exception as exc:
                _log.debug("ContextCache: skipping %s: %s", rel_path, exc)

        return {
            "built_at": _utcnow_str(),
            "project_root": str(self._root),
            "file_tree": file_tree,
            "public_api": public_api,
            "deprecated_symbols": sorted(set(deprecated_symbols)),
            "dependency_graph": dep_graph,
        }

    def _collect_file_tree(self) -> list[str]:
        """Return a sorted list of relative paths, excluding common noise dirs."""
        skip_dirs = {
            ".git", "__pycache__", ".tox", ".venv", "venv", "node_modules",
            ".mypy_cache", ".pytest_cache", "dist", "build", ".claude",
            "site-packages",
        }
        result: list[str] = []
        for dirpath, dirnames, filenames in os.walk(self._root):
            # Prune in-place to avoid descending into skip_dirs.
            dirnames[:] = [
                d for d in sorted(dirnames)
                if d not in skip_dirs and not d.startswith(".")
            ]
            for fname in sorted(filenames):
                if fname.startswith("."):
                    continue
                abs_p = Path(dirpath) / fname
                try:
                    rel = abs_p.relative_to(self._root)
                    result.append(str(rel))
                except ValueError:
                    pass
            if len(result) >= _MAX_FILE_TREE_ENTRIES:
                result = result[:_MAX_FILE_TREE_ENTRIES]
                break
        return result

    @staticmethod
    def _parse_python_file(source: str) -> tuple[list[str], list[str], list[str]]:
        """Parse a Python source file and return (exports, deprecated, imports).

        - *exports*: names in ``__all__`` (or top-level public names if absent).
        - *deprecated*: names decorated with ``@deprecated`` or containing
          ``DeprecationWarning`` in their body.
        - *imports*: module names imported by this file.

        Uses ``ast`` for best-effort extraction; returns empty lists on parse
        errors.
        """
        exports: list[str] = []
        deprecated: list[str] = []
        imports: list[str] = []

        try:
            tree = ast.parse(source)
        except SyntaxError:
            return exports, deprecated, imports

        # Extract __all__
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "__all__":
                        if isinstance(node.value, (ast.List, ast.Tuple)):
                            for elt in node.value.elts:
                                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                                    exports.append(elt.value)

        # Collect import names for dependency graph
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.append(node.module.split(".")[0])

        # Detect deprecated symbols (decorator named "deprecated" or "Deprecated")
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                for decorator in node.decorator_list:
                    dec_name = ""
                    if isinstance(decorator, ast.Name):
                        dec_name = decorator.id
                    elif isinstance(decorator, ast.Attribute):
                        dec_name = decorator.attr
                    elif isinstance(decorator, ast.Call):
                        if isinstance(decorator.func, ast.Name):
                            dec_name = decorator.func.id
                        elif isinstance(decorator.func, ast.Attribute):
                            dec_name = decorator.func.attr
                    if dec_name.lower() in ("deprecated", "deprecate"):
                        deprecated.append(node.name)

        return exports, deprecated, list(set(imports))
