"""AST lint — direct ``BeadStore(`` construction and ``._conn()`` bead-store access.

ADR-13b WP-G static guard (updated after teardown).

After WP-G, ``bead_store.py`` has been deleted and ``bd`` is the only mandatory
bead backend.  All bead-store creation must flow through ``make_bead_store()``
in ``agent_baton.core.engine.bead_backend``.  Similarly, ``._conn()`` calls on
a variable named ``_bead_store`` or ``bead_store`` reach into SQLite internals
that do not exist on ``BdBeadStore``.

This lint walks ``agent_baton/`` and flags:

1. ``BeadStore(`` constructor calls outside the one canonical file that
   is allowed to construct it (``bead_backend.py`` which is the factory).
2. ``._conn()`` calls where the receiver is literally named ``_bead_store``
   or ``bead_store``.

Escape hatches:

* Add ``# noqa: bead-store-direct`` to the offending line, OR
* Add the file path to ``ALLOWED_FILES`` below for a coarser per-file
  exemption (use sparingly; prefer per-line noqa).

See docs/internal/adr-13b-migration-design.md §6 (step G) / risks.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_SRC_ROOT = Path(__file__).resolve().parents[2] / "agent_baton"

# Files that are canonically allowed to instantiate BeadStore directly.
# bead_store.py was deleted in ADR-13b WP-G; only the factory remains.
ALLOWED_BEAD_STORE_CONSTRUCTION: set[Path] = {
    _SRC_ROOT / "core" / "engine" / "bead_backend.py",
}

# Files that have not yet been migrated off direct BeadStore construction.
# WP-G completed: all callers migrated to make_bead_store().  This set is
# intentionally empty — any new entry must go through ADR-13b review.
LEGACY_BEAD_STORE_CALLERS: set[Path] = set()

# Receiver names that look like a bead store when followed by ._conn().
_BEAD_STORE_RECEIVER_NAMES: set[str] = {"_bead_store", "bead_store"}

# File-level allowlist for ._conn() on bead store receivers.
# WP-G completed: executor._bead_store._conn() coupling has been removed.
# This set is intentionally empty — BdBeadStore has no _conn() method.
ALLOWED_BEAD_CONN_CALLERS: set[Path] = set()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _line_has_noqa(source_lines: list[str], lineno: int) -> bool:
    if not (1 <= lineno <= len(source_lines)):
        return False
    return "# noqa: bead-store-direct" in source_lines[lineno - 1]


class _Violation:
    __slots__ = ("path", "lineno", "kind", "snippet")

    def __init__(self, path: Path, lineno: int, kind: str, snippet: str) -> None:
        self.path = path
        self.lineno = lineno
        self.kind = kind
        self.snippet = snippet

    def __repr__(self) -> str:
        rel = self.path.relative_to(_SRC_ROOT.parent)
        return f"{rel}:{self.lineno} [{self.kind}] — {self.snippet}"


def _scan_for_bead_store_construction(path: Path) -> list[_Violation]:
    """Find BeadStore( calls in *path* (excluding canonical files)."""
    if path in ALLOWED_BEAD_STORE_CONSTRUCTION:
        return []
    if path in LEGACY_BEAD_STORE_CALLERS:
        return []

    text = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError:
        return []
    source_lines = text.splitlines()
    violations: list[_Violation] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        # Match BeadStore(...) — bare name call
        if isinstance(node.func, ast.Name) and node.func.id == "BeadStore":
            if _line_has_noqa(source_lines, node.lineno):
                continue
            snippet = source_lines[node.lineno - 1].strip()
            violations.append(
                _Violation(path, node.lineno, "BeadStore-direct", snippet)
            )
        # Match something.BeadStore(...) — attribute call (unlikely but catch it)
        elif (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "BeadStore"
        ):
            if _line_has_noqa(source_lines, node.lineno):
                continue
            snippet = source_lines[node.lineno - 1].strip()
            violations.append(
                _Violation(path, node.lineno, "BeadStore-attr-call", snippet)
            )
    return violations


def _scan_for_bead_conn_access(path: Path) -> list[_Violation]:
    """Find ``_bead_store._conn()`` / ``bead_store._conn()`` calls in *path*."""
    if path in ALLOWED_BEAD_CONN_CALLERS:
        return []

    text = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError:
        return []
    source_lines = text.splitlines()
    violations: list[_Violation] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        # Match _bead_store._conn() or bead_store._conn()
        if not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "_conn":
            continue
        receiver = node.func.value
        receiver_name: str | None = None
        if isinstance(receiver, ast.Name):
            receiver_name = receiver.id
        elif isinstance(receiver, ast.Attribute):
            receiver_name = receiver.attr
        if receiver_name not in _BEAD_STORE_RECEIVER_NAMES:
            continue
        if _line_has_noqa(source_lines, node.lineno):
            continue
        snippet = source_lines[node.lineno - 1].strip()
        violations.append(
            _Violation(path, node.lineno, "bead-store-conn", snippet)
        )
    return violations


def _all_python_sources() -> list[Path]:
    files: list[Path] = []
    for p in _SRC_ROOT.rglob("*.py"):
        if "__pycache__" in p.parts:
            continue
        files.append(p)
    return files


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestNoDirectBeadStoreConstruction:
    """New callers must not instantiate BeadStore directly — use make_bead_store()."""

    def test_no_new_bead_store_direct_construction(self) -> None:
        """Walk agent_baton/ and reject any BeadStore( outside allowed files.

        If this fails after adding new code that constructs BeadStore directly,
        either:

        * Replace with ``make_bead_store(db_path, ...)`` from
          ``agent_baton.core.engine.bead_backend``, OR
        * Add ``# noqa: bead-store-direct`` to the line for a one-off exemption.

        WP-G completed: LEGACY_BEAD_STORE_CALLERS is now empty.  New callers
        are not permitted — use make_bead_store() exclusively.

        See docs/internal/adr-13b-migration-design.md §6 (step G).
        """
        violations: list[_Violation] = []
        for src in _all_python_sources():
            violations.extend(_scan_for_bead_store_construction(src))
        assert not violations, (
            "New BeadStore( direct construction is forbidden outside "
            "bead_store.py / bead_backend.py.  Use make_bead_store() instead.\n"
            "Found:\n  "
            + "\n  ".join(str(v) for v in violations)
            + "\n\nAdd to LEGACY_BEAD_STORE_CALLERS if this is a pending "
            "migration site, or use make_bead_store() directly."
        )

    def test_no_bead_store_conn_outside_allowed_files(self) -> None:
        """Walk agent_baton/ and reject _bead_store._conn() anywhere.

        ``BdBeadStore`` has no ``_conn()`` method — any caller that reaches into
        the SQLite connection is broken.  WP-G completed: ALLOWED_BEAD_CONN_CALLERS
        is now empty.
        """
        violations: list[_Violation] = []
        for src in _all_python_sources():
            violations.extend(_scan_for_bead_conn_access(src))
        assert not violations, (
            "_bead_store._conn() / bead_store._conn() callers are forbidden "
            "outside executor.py (BdBeadStore has no _conn()).  "
            "Use DerivedBeadStore or store.query() instead.\n"
            "Found:\n  "
            + "\n  ".join(str(v) for v in violations)
        )

    def test_lint_self_test_construction_violation(self, tmp_path: Path) -> None:
        """Self-test: the lint catches a synthesised BeadStore( construction."""
        fake = tmp_path / "fake_cmd.py"
        fake.write_text(
            "from agent_baton.core.engine.bead_store import BeadStore\n"
            "store = BeadStore(db_path)\n"
        )
        violations = _scan_for_bead_store_construction(fake)
        assert len(violations) == 1
        assert violations[0].kind == "BeadStore-direct"

    def test_lint_self_test_noqa_suppresses(self, tmp_path: Path) -> None:
        """Self-test: # noqa: bead-store-direct suppresses the violation."""
        fake = tmp_path / "fake_exempt.py"
        fake.write_text(
            "from agent_baton.core.engine.bead_store import BeadStore\n"
            "store = BeadStore(db_path)  # noqa: bead-store-direct\n"
        )
        violations = _scan_for_bead_store_construction(fake)
        assert violations == []

    def test_lint_self_test_conn_violation(self, tmp_path: Path) -> None:
        """Self-test: the lint catches a synthesised _bead_store._conn() call."""
        fake = tmp_path / "fake_executor.py"
        fake.write_text(
            "def _synthesize(self):\n"
            "    conn = self._bead_store._conn()\n"
        )
        violations = _scan_for_bead_conn_access(fake)
        assert len(violations) == 1
        assert violations[0].kind == "bead-store-conn"
