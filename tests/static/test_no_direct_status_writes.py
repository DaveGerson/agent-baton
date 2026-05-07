"""AST lint — direct mutation of ``state.status`` is forbidden in production.

Slice 12 of the migration plan.  All Hole-1-class coupled-field flips
must funnel through ``ExecutionState.transition_to_*`` methods so the
I1/I2/I9 invariants cannot drift through an early ``return`` between
the status flip and the audit-row / completed_at write.

This linter walks ``agent_baton/`` source and rejects any ``Assign``
node whose target is ``state.status`` (or the coupled siblings that I1
and I9 cover) — except inside the model itself or files that have
opted out via:

* a magic ``# noqa: state-mutation`` comment on the assignment line, OR
* an entry in ``ALLOWED_FILES`` below.

The opt-in escape exists because a small number of legacy call sites
need a phased migration window.  Per
docs/internal/state-mutation-proposal.md §8.6 + the
migration-review-summary.md §1.3 Gap 2 audit, the lint is opt-in per
file rather than per project so it can be tightened over time.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Source root the lint walks.
_SRC_ROOT = Path(__file__).resolve().parents[2] / "agent_baton"

# Names that are forbidden as Assign targets when the receiver is named
# "state" or "_state".  The set covers the I1 + I2 + I9 coupled fields
# tracked by docs/internal/state-mutation-proposal.md §1.
_FORBIDDEN_ATTRS: set[str] = {
    "status",
    "pending_approval_request",
    "completed_at",
}

# Names of objects that look like an ExecutionState in production code.
# The lint is intentionally narrow — it triggers only when the LHS
# receiver is ``state``, ``_state``, ``self._state``, or
# ``execution_state``.  Other receivers (e.g. ``record.status``) are
# unrelated.
_STATE_RECEIVER_NAMES: set[str] = {"state", "_state", "execution_state"}

# Files exempted from the lint.  Production code is forbidden from
# direct writes; these are either the model itself (where the writes
# necessarily originate) or call sites that have a justified reason
# the migration plan permits.
ALLOWED_FILES: set[Path] = {
    _SRC_ROOT / "models" / "execution.py",
}


# ---------------------------------------------------------------------------
# AST visitor
# ---------------------------------------------------------------------------

class _Violation:
    """A single forbidden state-attribute write."""

    __slots__ = ("path", "lineno", "attr", "snippet")

    def __init__(self, path: Path, lineno: int, attr: str, snippet: str) -> None:
        self.path = path
        self.lineno = lineno
        self.attr = attr
        self.snippet = snippet

    def __repr__(self) -> str:
        rel = self.path.relative_to(_SRC_ROOT.parent)
        return f"{rel}:{self.lineno} state.{self.attr} — {self.snippet}"


def _is_state_receiver(node: ast.AST) -> bool:
    """Return True when ``node`` reads as one of the watched receivers."""
    if isinstance(node, ast.Name):
        return node.id in _STATE_RECEIVER_NAMES
    if isinstance(node, ast.Attribute):
        # self._state.status = ... — match the Attribute(_state) receiver.
        if node.attr in _STATE_RECEIVER_NAMES:
            return True
    return False


def _line_has_noqa(source_lines: list[str], lineno: int) -> bool:
    """Return True when the given line carries the magic noqa comment."""
    if not (1 <= lineno <= len(source_lines)):
        return False
    return "# noqa: state-mutation" in source_lines[lineno - 1]


def _scan_file(path: Path) -> list[_Violation]:
    """Walk *path* and return any forbidden-attribute writes."""
    text = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError:
        # Partial / experimental file — skip rather than crash the lint.
        return []
    source_lines = text.splitlines()
    violations: list[_Violation] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if not isinstance(target, ast.Attribute):
                continue
            if target.attr not in _FORBIDDEN_ATTRS:
                continue
            if not _is_state_receiver(target.value):
                continue
            if _line_has_noqa(source_lines, node.lineno):
                continue
            snippet = source_lines[node.lineno - 1].strip()
            violations.append(
                _Violation(path, node.lineno, target.attr, snippet)
            )
    return violations


def _all_python_sources() -> list[Path]:
    """Yield every .py file under agent_baton/ excluding allowlisted ones."""
    files: list[Path] = []
    for p in _SRC_ROOT.rglob("*.py"):
        if p in ALLOWED_FILES:
            continue
        if "__pycache__" in p.parts:
            continue
        files.append(p)
    return files


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestNoDirectStatusWrites:
    """Production code must funnel coupled-field mutations through transitions."""

    def test_no_direct_status_assignments_in_production(self) -> None:
        """Walks agent_baton/ and asserts no forbidden writes remain.

        If this test fails after a refactor, EITHER:

        * Replace the offending ``state.status = ...`` (or
          ``state.completed_at = ...`` / ``state.pending_approval_request = ...``)
          with the corresponding ``state.transition_to_*`` method; OR
        * If the call site genuinely needs the direct write (rare), append
          ``# noqa: state-mutation`` to the assignment line and document
          the reason in a code comment immediately above.

        See docs/internal/state-mutation-proposal.md §8.6.
        """
        violations: list[_Violation] = []
        for src in _all_python_sources():
            violations.extend(_scan_file(src))
        assert not violations, (
            "Direct state-attribute writes are forbidden outside "
            "agent_baton/models/execution.py.  Found:\n  "
            + "\n  ".join(str(v) for v in violations)
            + "\n\nReplace with the matching state.transition_to_* method, "
            "or annotate the line with `# noqa: state-mutation` if a "
            "documented exception is required."
        )

    def test_lint_catches_a_synthesised_violation(
        self, tmp_path: Path,
    ) -> None:
        """Self-test the AST visitor against a controlled fixture."""
        synthesized = tmp_path / "synth.py"
        synthesized.write_text(
            "def f(state):\n"
            "    state.status = 'failed'\n"
        )
        violations = _scan_file(synthesized)
        assert len(violations) == 1
        assert violations[0].attr == "status"

    def test_lint_respects_noqa(self, tmp_path: Path) -> None:
        """A `# noqa: state-mutation` comment suppresses the violation."""
        synthesized = tmp_path / "synth.py"
        synthesized.write_text(
            "def f(state):\n"
            "    state.status = 'failed'  # noqa: state-mutation\n"
        )
        assert _scan_file(synthesized) == []

    def test_lint_skips_allowlisted_attrs(self, tmp_path: Path) -> None:
        """Writes to attrs outside the forbidden set are not flagged."""
        synthesized = tmp_path / "synth.py"
        synthesized.write_text(
            "def f(state):\n"
            "    state.run_cumulative_spend_usd = 0.0\n"  # not coupled
        )
        assert _scan_file(synthesized) == []

    def test_lint_skips_non_state_receivers(self, tmp_path: Path) -> None:
        """``record.status`` is not the same field — must not be flagged."""
        synthesized = tmp_path / "synth.py"
        synthesized.write_text(
            "def f(record):\n"
            "    record.status = 'failed'\n"
        )
        assert _scan_file(synthesized) == []
