"""Handoff quality scoring for ``baton execute handoff`` (DX.3, bd-d136).

Computes a 0.0--1.0 quality score for an operator-supplied session
handoff note based on five heuristics, each contributing up to 0.2:

1. Length & specificity -- note is substantive (>=100 chars) and mentions
   at least one file path or symbol-like identifier.
2. Mentions next step  -- contains a forward-looking cue
   (``next``, ``then``, ``remaining``, ``todo``, ``tomorrow``,
   ``continue``).
3. Mentions blocker if any -- explicitly names a blocker (``block``,
   ``stuck``, ``fail``, ``error``, ``issue``) OR explicitly says
   there are none (``none``, ``no blockers``, ``clean``).
4. Branch state -- working tree is clean, OR the note explicitly
   acknowledges the dirty tree (``uncommitted``).
5. Test state -- mentions test status (``passing``, ``passed``,
   ``N pass``, ``failing``, ``failures``).

The scorer also returns a list of suggestions for whichever heuristics
did not earn full marks so the caller can nudge the operator toward a
higher-quality handoff next time.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


PER_HEURISTIC_MAX = 0.2


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BranchState:
    """Snapshot of git state at handoff time.

    Attributes:
        branch: Current git branch name (empty string if not in a repo).
        commits_ahead: Number of commits ahead of the upstream/master ref.
            Zero is the default when the count cannot be determined.
        dirty: True if the working tree has uncommitted changes.
    """

    branch: str = ""
    commits_ahead: int = 0
    dirty: bool = False


@dataclass(frozen=True)
class PlanState:
    """Optional context about the active plan for richer scoring later.

    The current scorer does not consume any of these fields directly; the
    dataclass is included so callers can pass plan metadata through
    without breaking the signature when future heuristics need it.
    """

    task_id: str = ""
    phase_id: int = 0
    steps_total: int = 0
    steps_complete: int = 0


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


@dataclass
class HandoffScore:
    """Result of :func:`score_handoff`.

    Attributes:
        total: Sum of the per-heuristic scores, in ``[0.0, 1.0]``.
        breakdown: Mapping of heuristic name -> awarded points
            (``0.0`` to :data:`PER_HEURISTIC_MAX`).
        suggestions: Human-readable hints for whichever heuristics
            did not earn full marks.
    """

    total: float = 0.0
    breakdown: dict[str, float] = field(default_factory=dict)
    suggestions: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Regexes (compiled once)
# ---------------------------------------------------------------------------

# A path-like token: contains a slash, OR a dot-extension (``foo.py``,
# ``Bar.tsx``).  Matches absolute, relative, and bare ``module/name`` forms.
_PATH_RE = re.compile(
    r"(?:[\w.\-]+/[\w./\-]+)"               # contains a slash segment
    r"|(?:\b[\w\-]+\.[a-zA-Z]{1,8}\b)"      # bare filename.ext
)

# A symbol-like identifier: snake_case_or_camelCase that contains at least
# two underscores or a CamelCase boundary.  This is a lightweight
# heuristic, not a parser.
_SYMBOL_RE = re.compile(
    r"\b(?:[a-z]+_[a-z0-9_]+|[A-Z][a-z0-9]+[A-Z][A-Za-z0-9]+)\b"
)

_NEXT_STEP_RE = re.compile(
    r"\b(next|then|remaining|todo|tomorrow|continue)\b",
    re.IGNORECASE,
)

_BLOCKER_RE = re.compile(
    r"\b(block(?:er|ed|ing)?s?|stuck|fail(?:ure|ed|ing|s)?|error|issue)\b",
    re.IGNORECASE,
)
_NO_BLOCKER_RE = re.compile(
    r"\b(none|no blockers|clean)\b",
    re.IGNORECASE,
)

_UNCOMMITTED_RE = re.compile(
    r"\b(uncommitted|wip|work[- ]in[- ]progress)\b",
    re.IGNORECASE,
)

_TEST_STATE_RE = re.compile(
    r"\b(passing|passed|failing|failures?|\d+\s*pass(?:ed|ing)?|"
    r"\d+\s*fail(?:ed|ing|ures?)?|tests?\s+(?:green|red))\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def _score_length_and_specificity(note: str) -> tuple[float, str | None]:
    if len(note) < 100:
        return (
            0.0,
            "Lengthen the note to at least 100 characters describing "
            "what was done.",
        )
    has_path = bool(_PATH_RE.search(note))
    has_symbol = bool(_SYMBOL_RE.search(note))
    if not (has_path or has_symbol):
        return (
            0.0,
            "Mention at least one concrete file path or symbol "
            "(e.g. agent_baton/core/foo.py or my_function).",
        )
    return PER_HEURISTIC_MAX, None


def _score_next_step(note: str) -> tuple[float, str | None]:
    if _NEXT_STEP_RE.search(note):
        return PER_HEURISTIC_MAX, None
    return (
        0.0,
        "Describe the next step or what to continue with "
        "(use words like 'next', 'todo', 'remaining', 'continue').",
    )


def _score_blocker(note: str) -> tuple[float, str | None]:
    if _BLOCKER_RE.search(note) or _NO_BLOCKER_RE.search(note):
        return PER_HEURISTIC_MAX, None
    return (
        0.0,
        "State whether there are blockers (or write 'no blockers' / "
        "'clean' if not).",
    )


def _score_branch_state(
    note: str, branch_state: BranchState
) -> tuple[float, str | None]:
    if not branch_state.dirty:
        return PER_HEURISTIC_MAX, None
    if _UNCOMMITTED_RE.search(note):
        return PER_HEURISTIC_MAX, None
    return (
        0.0,
        "Working tree is dirty -- either commit/stash, or acknowledge "
        "'uncommitted changes' in the note.",
    )


def _score_test_state(note: str) -> tuple[float, str | None]:
    if _TEST_STATE_RE.search(note):
        return PER_HEURISTIC_MAX, None
    return (
        0.0,
        "Mention test status ('passing', 'failing', 'N passed', etc.).",
    )


def score_handoff(
    note: str,
    branch_state: BranchState,
    plan_state: PlanState | None = None,
) -> HandoffScore:
    """Score *note* against the five DX.3 heuristics.

    Args:
        note: Operator-supplied free text describing where the session
            is stopping.
        branch_state: Current git state (branch / dirty / commits ahead).
        plan_state: Optional plan-level context.  Unused by the current
            heuristics but accepted for forward compatibility.

    Returns:
        A :class:`HandoffScore` with the total, per-heuristic breakdown,
        and a list of suggestions for any heuristic that did not earn
        full marks.
    """
    _ = plan_state  # reserved for future heuristics

    note = note or ""
    breakdown: dict[str, float] = {}
    suggestions: list[str] = []

    for name, scorer in (
        ("length_and_specificity",
         lambda: _score_length_and_specificity(note)),
        ("next_step",
         lambda: _score_next_step(note)),
        ("blocker",
         lambda: _score_blocker(note)),
        ("branch_state",
         lambda: _score_branch_state(note, branch_state)),
        ("test_state",
         lambda: _score_test_state(note)),
    ):
        points, hint = scorer()
        breakdown[name] = points
        if hint is not None:
            suggestions.append(f"{name}: {hint}")

    total = round(sum(breakdown.values()), 4)
    return HandoffScore(
        total=total, breakdown=breakdown, suggestions=suggestions
    )
