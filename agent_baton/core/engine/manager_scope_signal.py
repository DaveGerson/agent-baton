"""Manager-mode scope-expansion signal parsing (M9).

Agents dispatched under a manager-mode plan carry a scope contract (see
``agent_baton.core.manager.context_bundles``) whose ``Allowed Paths`` /
``Escalate If`` sections bound their work. When an agent needs to go
outside that contract it should not silently proceed -- it emits a
structured signal in its outcome text::

    SCOPE_EXPANSION: app/auth/session.py — session metadata needed

The engine parses these lines when recording the step result
(``ExecutionEngine.record_step_result``) and routes them per
``ManagerConfig.scoping.scope_expansion_policy`` (see
``agent_baton.core.config.manager.ScopingConfig``): ``allow_with_note``,
``queue_for_manager``, or ``block``.

This module is deliberately **distinct** from
``agent_baton.core.engine.scope_expansion`` (an unrelated, pre-existing
adaptive-replanning feature) and the ``SCOPE_EXPANSION: <description>``
free-text signal parsed by
``agent_baton.core.engine.bead_signal.parse_scope_expansions``. Both
signal formats share the ``SCOPE_EXPANSION:`` prefix and both parsers may
match the same outcome line (they are independent, best-effort consumers
of the same text) -- but only this module's stricter
``<path> — <reason>`` format participates in manager-mode scope-expansion
routing. See docs/internal/manager-mode-pmo-plan.md Task 13 and
docs/specs/agent-baton-claude-code-middle-manager-prd-tdd.md §13.2.

Design mirrors :mod:`agent_baton.core.engine.gate_addition` (the
canonical signal-parsing pattern for a dataclass + module-level regex +
parser function in this codebase).
"""
from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass

from agent_baton.core.engine.planning.scope_contract import (
    ScopeContractError,
    normalize_path_list,
    normalize_scope_path,
    paths_overlap,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Maximum agent-declared scope-expansion signals to accept per step.
# Mirrors gate_addition.py's _MAX_ADDITIONS_PER_STEP so the two
# signal-parsing modules bound agent-declared input identically.
_MAX_SIGNALS_PER_STEP: int = 8

# ---------------------------------------------------------------------------
# Signal pattern
# ---------------------------------------------------------------------------

# Format: SCOPE_EXPANSION: <path> — <reason>  (em dash or hyphen separator,
# optional surrounding whitespace). Anchored per-line via re.MULTILINE so
# ``$`` matches end-of-line rather than end-of-string, and re.IGNORECASE so
# agents do not need to match exact prefix casing.
_SCOPE_EXPANSION_SIGNAL_RE = re.compile(
    r"^SCOPE_EXPANSION:\s*(?P<path>\S+)\s*[—-]\s*(?P<reason>.+)$",
    re.IGNORECASE | re.MULTILINE,
)

# ---------------------------------------------------------------------------
# Data shape
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScopeExpansionSignal:
    """A single ``<path> — <reason>`` scope-expansion request.

    Attributes:
        path: The file/path the agent needs to touch outside its scope
            contract's ``Allowed Paths``.
        reason: Why the agent believes the expansion is necessary.
        step_id: Step ID of the step that produced the signal. Defaults
            to ``""`` for callers that parse text without step context
            (e.g. unit tests); ``record_step_result`` always supplies it.
    """

    path: str
    reason: str
    step_id: str = ""


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def parse_scope_expansion_signals(
    text: str,
    *,
    step_id: str = "",
) -> list[ScopeExpansionSignal]:
    """Parse all ``SCOPE_EXPANSION: <path> — <reason>`` signals from *text*.

    Scans *text* for every matching line. Lines with an empty path or
    empty reason (after stripping whitespace) are ignored. Duplicate
    ``(path, reason)`` pairs are deduplicated, keeping only the first
    occurrence. The result is capped at :data:`_MAX_SIGNALS_PER_STEP`
    entries so a single verbose agent cannot overwhelm downstream routing.

    Args:
        text: Free-text agent outcome (may contain any number of
            ``SCOPE_EXPANSION:`` lines, anywhere in the text, mixed with
            the free-text ``SCOPE_EXPANSION: <description>`` format
            consumed by the unrelated adaptive-replanning pipeline -- a
            line missing the ``<path> — <reason>`` shape simply does not
            match this module's stricter pattern).
        step_id: Step ID to stamp onto every returned signal.

    Returns:
        Ordered, deduplicated list of :class:`ScopeExpansionSignal`
        objects -- at most :data:`_MAX_SIGNALS_PER_STEP` items. Empty
        list when no matching lines are present.
    """
    if not text:
        return []

    seen: set[tuple[str, str]] = set()
    signals: list[ScopeExpansionSignal] = []

    try:
        matches = list(_SCOPE_EXPANSION_SIGNAL_RE.finditer(text))
    except Exception as exc:  # noqa: BLE001 - defensive, mirrors gate_addition.py
        logger.debug("parse_scope_expansion_signals: regex scan failed: %s", exc)
        return []

    for match in matches:
        path = match.group("path").strip()
        reason = match.group("reason").strip()
        if not path or not reason:
            continue
        key = (path, reason)
        if key in seen:
            continue
        seen.add(key)
        signals.append(ScopeExpansionSignal(path=path, reason=reason, step_id=step_id))
        if len(signals) >= _MAX_SIGNALS_PER_STEP:
            break

    return signals


# ---------------------------------------------------------------------------
# Diff-derived evidence (Phase 3 "Make scope contracts authoritative", 3.2)
# ---------------------------------------------------------------------------
#
# The signals above are all agent-*declared*: a step only ever produces one
# if the agent itself emitted a ``SCOPE_EXPANSION:`` line. Nothing stops an
# agent from silently writing outside its scope contract without emitting
# any marker at all -- an omission, not a lie, is enough to bypass the
# parsers above entirely.
#
# The functions below close that gap by deriving the same
# :class:`ScopeExpansionSignal` shape from *evidence*: the step's actual
# git diff, computed independently of anything the agent or the caller of
# ``record_step_result`` reported.


def independent_worktree_diff(handle: "dict | None", *, timeout: float = 15.0) -> list[str]:
    """Recompute a worktree step's real changed-file list from git ground truth.

    Ignores any launcher-/caller-reported ``commit_hash``/``files_changed``
    entirely: walks from the worktree's own ``base_sha`` (captured at
    creation time, before the agent touched anything -- see
    ``agent_baton.core.engine.worktree_manager.WorktreeHandle.base_sha``) to
    its own current ``HEAD``, plus any still-uncommitted working-tree
    changes. This is what makes scope enforcement resistant to a spoofed or
    merely-buggy ``baton execute record`` call: the input here is never a
    value any caller supplied.

    Args:
        handle: A serialized ``WorktreeHandle`` (``.to_dict()`` shape --
            i.e. ``state.step_worktrees[step_id]``). Must contain non-empty
            ``path`` and ``base_sha`` keys.
        timeout: Per-``git`` subprocess timeout in seconds.

    Raises:
        ValueError: *handle* is missing ``path``/``base_sha``.
        RuntimeError: the ``git diff`` invocation failed (not a git repo,
            *base_sha* unreachable, binary missing, etc).

    Callers MUST treat any exception as "diff unknown" and fail closed
    (never as "diff clean") -- see
    ``agent_baton.core.engine.executor.ExecutionEngine.record_step_result``.
    """
    handle = handle or {}
    path = str(handle.get("path") or "")
    base_sha = str(handle.get("base_sha") or "")
    if not path or not base_sha:
        raise ValueError(
            "independent_worktree_diff: worktree handle missing path/base_sha "
            f"(path={path!r}, base_sha={base_sha!r})"
        )

    diff_proc = subprocess.run(
        ["git", "diff", "--name-only", base_sha, "HEAD"],
        cwd=path,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if diff_proc.returncode != 0:
        raise RuntimeError(
            f"independent_worktree_diff: 'git diff' failed in {path}: "
            f"{diff_proc.stderr.strip() or diff_proc.stdout.strip()}"
        )
    changed = [f for f in diff_proc.stdout.splitlines() if f]

    status_proc = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=path,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if status_proc.returncode == 0:
        for line in status_proc.stdout.splitlines():
            if len(line) <= 3:
                continue
            entry = line[3:].strip()
            # Rename entries look like "old -> new"; the new path is what
            # actually exists after the change.
            entry = entry.split(" -> ")[-1].strip()
            if entry and entry not in changed:
                changed.append(entry)

    return changed


def derive_scope_expansion_from_diff(
    *,
    changed_files: "list[str]",
    allowed_paths: "list[str]",
    blocked_paths: "list[str]",
    step_id: str = "",
) -> list[ScopeExpansionSignal]:
    """Independently derive scope-expansion evidence from the ACTUAL diff.

    Unlike :func:`parse_scope_expansion_signals`, this never trusts
    anything the agent said about its own work: it is handed the real
    changed-file list (see :func:`independent_worktree_diff`) and the
    step's scope contract, and reports every file that collides with
    ``blocked_paths`` or falls outside ``allowed_paths`` (when non-empty)
    -- regardless of whether the agent emitted a marker for it.

    Returns ``[]`` when the step's contract is empty (no ``allowed_paths``
    and no ``blocked_paths`` -- there is nothing to violate). A changed
    file that fails path normalization (e.g. a symlink-escape or traversal
    artifact -- see ``normalize_scope_path``) is reported as a violation
    rather than silently dropped: an unnormalizable path can never be
    verified as in-scope, so fail closed.

    Every ``.reason`` is prefixed ``[diff-verified]`` so downstream
    consumers (decision context, bead content) can distinguish evidence
    derived here from an agent-declared marker parsed by
    :func:`parse_scope_expansion_signals`.
    """
    normalized_allowed = normalize_path_list(allowed_paths)
    normalized_blocked = normalize_path_list(blocked_paths)
    if not normalized_allowed and not normalized_blocked:
        return []

    violations: list[ScopeExpansionSignal] = []
    seen: set[str] = set()
    for raw in changed_files or []:
        raw = (raw or "").strip()
        if not raw or raw in seen:
            continue
        seen.add(raw)

        try:
            candidate = normalize_scope_path(raw)
        except ScopeContractError:
            violations.append(ScopeExpansionSignal(
                path=raw,
                reason="[diff-verified] path could not be normalized against the scope contract",
                step_id=step_id,
            ))
            continue

        blocked_hit = next(
            (b for b in normalized_blocked if paths_overlap(candidate, b)), None
        )
        if blocked_hit is not None:
            violations.append(ScopeExpansionSignal(
                path=candidate,
                reason=f"[diff-verified] matches blocked_paths entry '{blocked_hit}'",
                step_id=step_id,
            ))
            continue

        if normalized_allowed and not any(
            paths_overlap(candidate, a) for a in normalized_allowed
        ):
            violations.append(ScopeExpansionSignal(
                path=candidate,
                reason="[diff-verified] outside allowed_paths",
                step_id=step_id,
            ))

    return violations
