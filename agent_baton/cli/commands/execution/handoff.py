"""``baton execute handoff`` -- record a session-handoff note (DX.3, bd-d136).

Subcommands:

* ``baton execute handoff record --note "<text>" [--task-id ID] [--branch] [--score]``
  Captures the operator's free-text description of where they are
  stopping, computes a quality score against five heuristics, and
  persists the row to the project-local ``handoffs`` SQLite table
  (schema v18).  ``--branch`` annotates the note with current git
  branch + commits-ahead-of-master.  ``--score`` prints the numeric
  score after writing.

* ``baton execute handoff list [--task-id ID] [--limit N]``
  Compact table of recent handoffs, newest first.

* ``baton execute handoff show <handoff_id>``
  Full record + per-heuristic breakdown + suggestions for any
  heuristic that did not earn full marks.

The plain ``baton execute handoff --note "..."`` form (no subcommand)
is treated as ``record`` for ergonomics -- it is the most common usage
and the spec's headline behaviour.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from agent_baton.cli.errors import user_error, validation_error
from agent_baton.core.improve.handoff_score import (
    BranchState,
    HandoffScore,
    PlanState,
    score_handoff,
)
from agent_baton.core.storage import detect_backend, get_project_storage
from agent_baton.core.storage.handoff_store import HandoffStore


_BATON_DB = "baton.db"


# ---------------------------------------------------------------------------
# argparse wiring
# ---------------------------------------------------------------------------


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    """Register the ``handoff`` top-level command and its subcommands."""
    p = subparsers.add_parser(
        "handoff",
        help="Record a session-handoff note + quality score (DX.3)",
        description=(
            "Capture where you're stopping so the next session can "
            "pick up without the 'what was I doing?' tax."
        ),
    )

    # Top-level flags so `baton handoff --note "..."` works without
    # the explicit `record` subcommand.
    p.add_argument("--note", default=None,
                   help="Handoff note (free text). Triggers record mode.")
    p.add_argument("--task-id", default=None,
                   help="Target a specific execution by task ID")
    p.add_argument("--branch", action="store_true", default=False,
                   help="Include current git branch + commits-ahead in the note")
    p.add_argument("--score", action="store_true", default=False,
                   help="Print the quality score after writing")
    p.add_argument("--output", choices=["text", "json"], default="text",
                   help="Output format (default: text)")

    sub = p.add_subparsers(dest="handoff_subcommand")

    # record -----------------------------------------------------------
    p_rec = sub.add_parser(
        "record",
        help="Write a new handoff note (default action)",
    )
    p_rec.add_argument("--note", required=True,
                       help="Handoff note (free text)")
    p_rec.add_argument("--task-id", default=None,
                       help="Target a specific execution by task ID")
    p_rec.add_argument("--branch", action="store_true", default=False,
                       help="Include git branch + commits-ahead in the note")
    p_rec.add_argument("--score", action="store_true", default=False,
                       help="Print the quality score after writing")
    p_rec.add_argument("--output", choices=["text", "json"], default="text")

    # list -------------------------------------------------------------
    p_list = sub.add_parser("list", help="List recent handoffs")
    p_list.add_argument("--task-id", default=None,
                        help="Filter by task ID")
    p_list.add_argument("--limit", type=int, default=20,
                        help="Maximum rows to return (default: 20)")
    p_list.add_argument("--output", choices=["text", "json"], default="text")

    # show -------------------------------------------------------------
    p_show = sub.add_parser("show", help="Show a single handoff in full")
    p_show.add_argument("handoff_id", help="Handoff ID to display")
    p_show.add_argument("--output", choices=["text", "json"], default="text")

    return p


# ---------------------------------------------------------------------------
# handler
# ---------------------------------------------------------------------------


def handler(args: argparse.Namespace) -> None:
    sub = getattr(args, "handoff_subcommand", None)

    if sub == "list":
        _handle_list(args)
        return
    if sub == "show":
        _handle_show(args)
        return
    # Default to record (covers both `record` subcommand and bare
    # `baton execute handoff --note "..."` form).
    _handle_record(args)


# ---------------------------------------------------------------------------
# record
# ---------------------------------------------------------------------------


def _handle_record(args: argparse.Namespace) -> None:
    note = (getattr(args, "note", None) or "").strip()
    if not note:
        validation_error(
            "--note is required",
            hint='Try: baton execute handoff --note "Stopped after wiring HandoffStore; tests passing."',
        )

    context_root = _resolve_context_root()
    # _resolve_context_root() returns ``<root>/.claude/team-context``.
    # The project baton.db lives at ``<root>/baton.db``.
    db_path = _project_db_path(context_root)

    task_id = _resolve_task_id(getattr(args, "task_id", None), context_root)

    branch_state = _detect_branch_state(include_branch=bool(getattr(args, "branch", False)))

    if getattr(args, "branch", False) and branch_state.branch:
        suffix = (
            f"\n\n[git] branch={branch_state.branch} "
            f"commits_ahead={branch_state.commits_ahead} "
            f"dirty={'yes' if branch_state.dirty else 'no'}"
        )
        if suffix.strip() not in note:
            note = f"{note}{suffix}"

    plan_state = _load_plan_state(context_root, task_id)
    score = score_handoff(note, branch_state, plan_state)

    store = HandoffStore(db_path)
    hid = store.record(
        task_id=task_id or "",
        note=note,
        branch=branch_state.branch,
        commits_ahead=branch_state.commits_ahead,
        git_dirty=branch_state.dirty,
        quality_score=score.total,
        score_breakdown=score.breakdown,
    )

    if not hid:
        user_error(
            "failed to record handoff (handoffs table missing)",
            hint=(
                "Run any other baton command once to apply the v18 "
                "schema migration, then retry."
            ),
        )

    out = getattr(args, "output", "text")
    if out == "json":
        payload = {
            "handoff_id": hid,
            "task_id": task_id or "",
            "quality_score": score.total,
            "score_breakdown": score.breakdown,
            "suggestions": score.suggestions,
            "branch": branch_state.branch,
            "commits_ahead": branch_state.commits_ahead,
            "git_dirty": branch_state.dirty,
        }
        print(json.dumps(payload, indent=2))
        return

    print(f"Recorded handoff: {hid}")
    if task_id:
        print(f"  task: {task_id}")
    if branch_state.branch:
        print(
            f"  branch: {branch_state.branch} "
            f"(+{branch_state.commits_ahead} ahead, "
            f"{'dirty' if branch_state.dirty else 'clean'})"
        )
    if getattr(args, "score", False) or score.suggestions:
        print(f"  quality_score: {score.total:.2f} / 1.00")
        for name, points in score.breakdown.items():
            marker = "+" if points > 0 else "-"
            print(f"    {marker} {name}: {points:.2f}")
        if score.suggestions:
            print("  to improve next time:")
            for s in score.suggestions:
                print(f"    - {s}")


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


def _handle_list(args: argparse.Namespace) -> None:
    context_root = _resolve_context_root()
    db_path = _project_db_path(context_root)
    task_id = getattr(args, "task_id", None)
    limit = int(getattr(args, "limit", 20) or 20)

    store = HandoffStore(db_path)
    rows = store.list_recent(task_id=task_id, limit=limit)

    out = getattr(args, "output", "text")
    if out == "json":
        print(json.dumps(
            [
                {
                    "handoff_id": r.handoff_id,
                    "task_id": r.task_id,
                    "created_at": r.created_at,
                    "branch": r.branch,
                    "commits_ahead": r.commits_ahead,
                    "git_dirty": r.git_dirty,
                    "quality_score": r.quality_score,
                    "note_preview": (r.note[:80] + "...") if len(r.note) > 80 else r.note,
                }
                for r in rows
            ],
            indent=2,
        ))
        return

    if not rows:
        print("No handoffs recorded.")
        if task_id:
            print(f"  (filter: task_id={task_id})")
        return

    # Compact text table.
    print(f"{'HANDOFF_ID':<18}  {'CREATED':<20}  {'TASK':<14}  {'SCORE':>5}  NOTE")
    print(f"{'-' * 18}  {'-' * 20}  {'-' * 14}  {'-' * 5}  {'-' * 40}")
    for r in rows:
        note_preview = r.note.splitlines()[0] if r.note else ""
        if len(note_preview) > 60:
            note_preview = note_preview[:57] + "..."
        task_preview = (r.task_id or "")[:14]
        print(
            f"{r.handoff_id:<18}  {r.created_at:<20}  "
            f"{task_preview:<14}  {r.quality_score:>5.2f}  {note_preview}"
        )


# ---------------------------------------------------------------------------
# show
# ---------------------------------------------------------------------------


def _handle_show(args: argparse.Namespace) -> None:
    context_root = _resolve_context_root()
    db_path = _project_db_path(context_root)
    handoff_id = getattr(args, "handoff_id", "")

    store = HandoffStore(db_path)
    rec = store.get(handoff_id)
    if rec is None:
        user_error(f"handoff not found: {handoff_id}",
                   hint="Run 'baton execute handoff list' to see available IDs.")

    out = getattr(args, "output", "text")
    if out == "json":
        print(json.dumps(
            {
                "handoff_id": rec.handoff_id,
                "task_id": rec.task_id,
                "created_at": rec.created_at,
                "branch": rec.branch,
                "commits_ahead": rec.commits_ahead,
                "git_dirty": rec.git_dirty,
                "quality_score": rec.quality_score,
                "score_breakdown": rec.score_breakdown,
                "note": rec.note,
            },
            indent=2,
        ))
        return

    print(f"Handoff: {rec.handoff_id}")
    print(f"  task:          {rec.task_id or '(none)'}")
    print(f"  created_at:    {rec.created_at}")
    print(f"  branch:        {rec.branch or '(unknown)'}")
    print(f"  commits_ahead: {rec.commits_ahead}")
    print(f"  git_dirty:     {'yes' if rec.git_dirty else 'no'}")
    print(f"  quality_score: {rec.quality_score:.2f} / 1.00")
    if rec.score_breakdown:
        print(f"  breakdown:")
        for name, points in rec.score_breakdown.items():
            marker = "+" if points > 0 else "-"
            print(f"    {marker} {name}: {points:.2f}")
    print()
    print("--- Note ---")
    print(rec.note)
    print("--- End Note ---")


# ---------------------------------------------------------------------------
# Helpers (mirror the patterns used by execute.py)
# ---------------------------------------------------------------------------


def _resolve_context_root() -> Path:
    """Resolve the ``.claude/team-context`` directory.

    Mirrors :func:`agent_baton.cli.commands.execution.execute._resolve_context_root`
    but kept local to avoid importing the heavy execute module just for
    a path lookup (pulls in the entire engine).
    """
    # Fast path: git repo root.
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            git_root = Path(result.stdout.strip())
            ctx = git_root / ".claude" / "team-context"
            if ctx.is_dir():
                return ctx.resolve()
            # No team-context yet -- still anchor to git root.
            return (git_root / ".claude" / "team-context").resolve()
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass

    cwd = Path.cwd()
    for ancestor in [cwd, *cwd.parents]:
        candidate = ancestor / ".claude" / "team-context"
        if candidate.is_dir():
            return candidate.resolve()
    return (cwd / ".claude" / "team-context").resolve()


def _project_db_path(context_root: Path) -> Path:
    """Return the project-local ``baton.db`` path.

    ``context_root`` is ``<root>/.claude/team-context``; the canonical
    location for ``baton.db`` is ``<root>/baton.db``.  Some installs (and
    several tests) instead put ``baton.db`` next to the team-context
    directory, so we honour either layout.

    Tests can also override the path via ``BATON_DB_PATH``.
    """
    override = os.environ.get("BATON_DB_PATH")
    if override:
        return Path(override).resolve()

    # ``<root>/.claude/team-context/`` -> ``<root>/baton.db``
    project_root = context_root.parent.parent
    candidate = project_root / _BATON_DB
    if candidate.exists():
        return candidate.resolve()

    # Fallback: alongside the team-context directory itself.
    alt = context_root.parent / _BATON_DB
    if alt.exists():
        return alt.resolve()

    # Last resort: prefer the canonical location (will be created on first
    # write by the storage layer).
    return candidate.resolve()


def _resolve_task_id(explicit: str | None, context_root: Path) -> str | None:
    """Apply the same task-id resolution chain as ``baton execute``."""
    if explicit:
        return explicit
    env_id = os.environ.get("BATON_TASK_ID")
    if env_id:
        return env_id
    try:
        backend = detect_backend(context_root)
    except Exception:  # noqa: BLE001 - defensive
        backend = "file"
    if backend == "sqlite":
        try:
            storage = get_project_storage(context_root, backend="sqlite")
            tid = storage.get_active_task()
            if tid:
                return tid
        except Exception:  # noqa: BLE001 - defensive
            pass
    # File-based fallback.
    try:
        from agent_baton.core.engine.persistence import StatePersistence
        return StatePersistence.get_active_task_id(context_root)
    except Exception:  # noqa: BLE001 - defensive
        return None


def _detect_branch_state(*, include_branch: bool) -> BranchState:
    """Snapshot the current git branch / commits-ahead / dirty state.

    When *include_branch* is False we still detect dirtiness (it feeds the
    branch_state heuristic) but skip the branch-name + commits-ahead
    lookups since they are only surfaced when the operator opts in via
    ``--branch``.
    """
    branch = ""
    ahead = 0
    dirty = False
    try:
        # Always cheap: porcelain status to check dirtiness.
        st = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True, text=True, timeout=5,
        )
        if st.returncode == 0 and st.stdout.strip():
            dirty = True
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return BranchState()

    if not include_branch:
        return BranchState(branch="", commits_ahead=0, dirty=dirty)

    try:
        br = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        if br.returncode == 0:
            branch = br.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass

    # Best-effort commits-ahead vs origin/master then master.
    for ref in ("origin/master", "master", "origin/main", "main"):
        try:
            cnt = subprocess.run(
                ["git", "rev-list", "--count", f"{ref}..HEAD"],
                capture_output=True, text=True, timeout=5,
            )
            if cnt.returncode == 0:
                try:
                    ahead = int((cnt.stdout or "0").strip())
                    break
                except ValueError:
                    continue
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            continue

    return BranchState(branch=branch, commits_ahead=ahead, dirty=dirty)


def _load_plan_state(context_root: Path, task_id: str | None) -> PlanState | None:
    """Best-effort plan snapshot for forward-compatible scoring.

    The current scorer ignores PlanState, but we populate it so future
    heuristics can consume it without a breaking change.
    """
    if not task_id:
        return None
    try:
        storage = get_project_storage(context_root)
        st = None
        # Different backends expose execution status differently; tolerate any
        # AttributeError silently.
        try:
            from agent_baton.core.engine.executor import ExecutionEngine
            from agent_baton.core.events.bus import EventBus
            engine = ExecutionEngine(bus=EventBus(), task_id=task_id, storage=storage)
            st = engine.status() or {}
        except Exception:  # noqa: BLE001 - defensive
            st = {}
        return PlanState(
            task_id=task_id,
            phase_id=int(st.get("current_phase", 0) or 0),
            steps_total=int(st.get("steps_total", 0) or 0),
            steps_complete=int(st.get("steps_complete", 0) or 0),
        )
    except Exception:  # noqa: BLE001 - defensive
        return None


# ---------------------------------------------------------------------------
# Public helper for the TTY end-of-run nudge (called from execute.py).
# ---------------------------------------------------------------------------


def maybe_print_handoff_nudge(
    *, task_id: str | None, context_root: Path | None = None,
) -> None:
    """Print the one-line handoff reminder when appropriate.

    The nudge fires only when:
    * stdout is a TTY (avoid polluting JSON / piped output);
    * a task_id is available (we have something to record against);
    * no handoff has been recorded for that task yet.
    """
    try:
        if not task_id:
            return
        if not sys.stdout.isatty():
            return
        ctx = context_root or _resolve_context_root()
        db_path = _project_db_path(ctx)
        if not db_path.exists():
            return
        store = HandoffStore(db_path)
        if store.has_any_for_task(task_id):
            return
        print(
            "tip: record this session's stopping point with: "
            'baton execute handoff --note "..."'
        )
    except Exception:  # noqa: BLE001 - never let a nudge break the CLI
        return
