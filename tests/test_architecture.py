"""Architecture invariant tests for the post-005b engine layout.

These tests are static-analysis style: they walk ``agent_baton/`` source
files and assert that certain mutations only happen in the modules that
own them.  The 005b refactor establishes that:

- ``state.current_phase += <expr>`` is a phase-advance mutation owned by
  :mod:`agent_baton.core.engine.phase_manager`.  No other module in
  ``agent_baton/`` may bump ``current_phase`` inline.
- ``state.current_step_index = 0`` (or ``= 0``-style resets) is part of
  the same phase-advance epilogue and likewise belongs to
  :mod:`phase_manager`.  Only the phase-advance pathway resets the step
  index; mid-phase code MUST NOT reset it inline.

If a future change re-introduces an inline bump, this test fails fast
with a pointer to the offending file/line so the author can route the
mutation through ``PhaseManager.advance_phase()`` instead.

Step: 4.1a (005b refactor)
Reference: docs/internal/005b-phase3-design.md §3
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# ── Project root and search root ──────────────────────────────────────────────
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_PACKAGE_ROOT = _PROJECT_ROOT / "agent_baton"

# Modules that are *allowed* to perform the mutation directly.  Paths are
# expressed relative to ``_PACKAGE_ROOT``.
_PHASE_ADVANCE_ALLOWED = {
    Path("core/engine/phase_manager.py"),
}

_STEP_INDEX_RESET_ALLOWED = {
    Path("core/engine/phase_manager.py"),
}


# ── Regex patterns ───────────────────────────────────────────────────────────
# Match ``state.current_phase +=`` (any whitespace) — the canonical
# phase-advance bump.  Variants like ``state.current_phase = state.current_phase + 1``
# are also forbidden — covered by the second pattern.
_PHASE_BUMP_RE = re.compile(r"state\.current_phase\s*\+=")
_PHASE_REASSIGN_RE = re.compile(
    r"state\.current_phase\s*=\s*state\.current_phase\s*\+"
)

# Match ``state.current_step_index = 0`` — the reset that pairs with the
# phase advance.  Mid-phase code must never reset the step index.
_STEP_INDEX_RESET_RE = re.compile(r"state\.current_step_index\s*=\s*0\b")


def _iter_py_files() -> list[Path]:
    """Yield every ``.py`` file under ``agent_baton/``."""
    return sorted(p for p in _PACKAGE_ROOT.rglob("*.py") if p.is_file())


def _scan(pattern: re.Pattern[str], allowed: set[Path]) -> list[tuple[Path, int, str]]:
    """Return (relpath, lineno, line) tuples for every match outside *allowed*.

    Lines beginning with a ``#`` (after stripping leading whitespace) are
    treated as commentary and skipped — comments referencing the pattern
    (e.g. in design docstrings) are not violations.
    """
    offenders: list[tuple[Path, int, str]] = []
    for path in _iter_py_files():
        rel = path.relative_to(_PACKAGE_ROOT)
        if rel in allowed:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            # Binary or unreadable — skip; agent_baton/ is pure Python so
            # this should never trigger, but guard for safety.
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            # Also skip lines that are entirely inside a docstring marker;
            # we rely on ``#`` comments and the regex itself being
            # narrow enough that prose discussion ("``state.current_phase += 1``")
            # is rare.  Triple-quoted prose hits are addressed by the
            # narrow regex (which requires the bare statement form).
            if pattern.search(line):
                offenders.append((rel, lineno, line.rstrip()))
    return offenders


def _format_offenders(offenders: list[tuple[Path, int, str]]) -> str:
    """Render an offender list as a multi-line failure message."""
    return "\n".join(
        f"  agent_baton/{rel}:{lineno}: {line}"
        for rel, lineno, line in offenders
    )


# ── Tests ────────────────────────────────────────────────────────────────────


def test_only_phase_manager_bumps_current_phase() -> None:
    """``state.current_phase`` is bumped exclusively by ``PhaseManager``.

    The 005b refactor centralises phase-advance mutations in
    :meth:`PhaseManager.advance_phase`.  Any inline ``state.current_phase += N``
    or equivalent reassignment outside ``phase_manager.py`` is a regression
    that bypasses event publication, VETO enforcement, and the audit trail.
    """
    bump_offenders = _scan(_PHASE_BUMP_RE, _PHASE_ADVANCE_ALLOWED)
    reassign_offenders = _scan(_PHASE_REASSIGN_RE, _PHASE_ADVANCE_ALLOWED)
    offenders = bump_offenders + reassign_offenders
    assert not offenders, (
        "Found inline state.current_phase mutations outside "
        "agent_baton/core/engine/phase_manager.py.  Route every phase advance "
        "through self._phase_manager.advance_phase(state, set_status_running=...) "
        "so event publication and VETO enforcement run consistently.\n"
        "Offending lines:\n" + _format_offenders(offenders)
    )


def test_only_phase_manager_resets_current_step_index() -> None:
    """``state.current_step_index = 0`` belongs to the phase-advance epilogue.

    Mid-phase code MUST NOT reset the step index inline.  The reset is
    paired with the phase bump inside :meth:`PhaseManager.advance_phase`,
    so any other site that resets it has skipped event publication and
    risks losing the in-flight step pointer.
    """
    offenders = _scan(_STEP_INDEX_RESET_RE, _STEP_INDEX_RESET_ALLOWED)
    assert not offenders, (
        "Found inline state.current_step_index = 0 resets outside "
        "agent_baton/core/engine/phase_manager.py.  Step-index resets "
        "MUST be paired with a phase advance via "
        "self._phase_manager.advance_phase(state, ...).\n"
        "Offending lines:\n" + _format_offenders(offenders)
    )


# ── Smoke: the test itself must locate phase_manager.py ──────────────────────


def test_phase_manager_module_exists() -> None:
    """Sanity check: ensure the allow-listed module path actually exists.

    If ``phase_manager.py`` is moved or renamed, the allow-list must be
    updated in lockstep — otherwise the test silently allows mutations
    that no longer have a home.
    """
    assert (_PACKAGE_ROOT / "core" / "engine" / "phase_manager.py").is_file(), (
        "phase_manager.py not found at agent_baton/core/engine/.  "
        "Update the _PHASE_ADVANCE_ALLOWED / _STEP_INDEX_RESET_ALLOWED sets "
        "in tests/test_architecture.py if the module was relocated."
    )


# ── bd-ab1d: _executor_helpers must be a pure leaf module ────────────────────


def test_executor_helpers_no_back_imports() -> None:
    """_executor_helpers must NEVER import from executor.py / resolver.py /
    phase_manager.py to keep it a pure leaf module.  Only stdlib + models.

    resolver.py imports from _executor_helpers.py — any import from
    resolver.py back into _executor_helpers.py would create a circular
    dependency.  Same risk for executor.py and phase_manager.py.
    """
    helpers_path = _PACKAGE_ROOT / "core" / "engine" / "_executor_helpers.py"
    assert helpers_path.is_file(), "_executor_helpers.py is missing"

    text = helpers_path.read_text(encoding="utf-8")

    forbidden = [
        "from agent_baton.core.engine.executor",
        "from agent_baton.core.engine.resolver",
        "from agent_baton.core.engine.phase_manager",
        "import agent_baton.core.engine.executor",
        "import agent_baton.core.engine.resolver",
        "import agent_baton.core.engine.phase_manager",
    ]

    violations = [pattern for pattern in forbidden if pattern in text]

    assert not violations, (
        "_executor_helpers.py back-imports from an upstream engine module, "
        "which would create a circular import.  "
        f"Remove the following forbidden imports: {violations!r}"
    )
